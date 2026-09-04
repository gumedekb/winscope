"""WinScope model server — FastAPI in front of the v3.1-club XGBoost ensemble.

Artifacts live in artifacts/ and are produced by the Colab training notebook,
not by this repo. This process only serves them:

    5 x xgboost_seed*.ubj   the ensemble; probabilities are averaged, which is
                            bit-identical to the exported VotingClassifier and
                            avoids depending on a pickle's sklearn version.
    features.py             turns two club names into the model's 81 features.

Endpoints
    GET  /health          liveness + what is loaded
    GET  /model           metadata: version, training window, test metrics
    GET  /teams           clubs the model knows
    POST /predict         one fixture
    POST /predict/batch   many fixtures in one call (the dashboard uses this)
"""
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from features import ARTIFACT_DIR, Stores, build_row

MODEL_GLOB = "xgboost_seed{}.ubj"
N_SEEDS = 5

state: dict = {"stores": None, "boosters": [], "alias": {}}


# --------------------------------------------------------------------------
# Team-name resolution
# --------------------------------------------------------------------------
def _norm(name: str) -> str:
    """Fold accents, drop club-form noise — mirrors the ETL's matching rules."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\b(FC|AFC|CF|SC|AC|SV|BV|CD|UD|SD)\b", " ", text, flags=re.I)
    text = re.sub(r"[^A-Za-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def build_alias_index(stores: Stores) -> dict[str, str]:
    index: dict[str, str] = {}
    for team in set(stores.elo) | set(stores.form):
        index.setdefault(_norm(team), team)
    return index


def resolve_team(name: str) -> tuple[Optional[str], bool]:
    """-> (canonical name or None, exact?). Never guesses across clubs."""
    if not name:
        return None, False
    stores: Stores = state["stores"]
    if name in stores.elo or name in stores.form:
        return name, True
    hit = state["alias"].get(_norm(name))
    return (hit, False) if hit else (None, False)


# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    stores = Stores(ARTIFACT_DIR)
    boosters = []
    for seed in range(N_SEEDS):
        path = os.path.join(ARTIFACT_DIR, MODEL_GLOB.format(seed))
        if not os.path.exists(path):
            continue
        booster = xgb.Booster()
        booster.load_model(path)
        boosters.append(booster)
    if not boosters:
        raise RuntimeError(f"no xgboost_seed*.ubj found in {ARTIFACT_DIR}")

    state.update(stores=stores, boosters=boosters, alias=build_alias_index(stores))
    print(f"[model] v{stores.metadata.get('model_version')} — {len(boosters)} seeds, "
          f"{len(stores.features)} features, {len(stores.elo)} clubs")
    yield
    state.update(stores=None, boosters=[], alias={})


app = FastAPI(title="WinScope model server", version="3.1", lifespan=lifespan)


# --------------------------------------------------------------------------
class PredictRequest(BaseModel):
    home_team: str
    away_team: str
    league: Optional[str] = None
    kickoff: Optional[str] = Field(None, description="ISO date; affects days-rest")
    # Accepted and ignored — club football always has a home side. Kept so older
    # callers do not break.
    neutral: Optional[bool] = False


class BatchRequest(BaseModel):
    fixtures: list[PredictRequest]


def _predict_rows(rows: list[dict]) -> np.ndarray:
    """Average the seeds' probabilities. Columns must be in trained order."""
    stores: Stores = state["stores"]
    frame = pd.DataFrame(rows, columns=stores.features)
    matrix = xgb.DMatrix(frame, feature_names=stores.features)
    stacked = np.stack([b.predict(matrix) for b in state["boosters"]])
    return stacked.mean(axis=0)


def _shape(probs: np.ndarray, stores: Stores, diagnostics: dict,
           home: str, away: str, league: Optional[str]) -> dict:
    by_class = {label: float(probs[idx]) for label, idx in stores.label_map.items()}
    home_win = by_class.get("home_win", 0.0)
    draw = by_class.get("draw", 0.0)
    away_win = by_class.get("away_win", 0.0)
    outcome = max((("H", home_win), ("D", draw), ("A", away_win)), key=lambda kv: kv[1])[0]

    home_form = stores.form.get(home) or {}
    away_form = stores.form.get(away) or {}
    return {
        "home_team": home,
        "away_team": away,
        "league": league,
        # Named to match what the web app reads.
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "outcome": outcome,
        "prediction": {"H": "home_win", "D": "draw", "A": "away_win"}[outcome],
        "confidence": max(home_win, draw, away_win),
        "home_elo": stores.elo.get(home),
        "away_elo": stores.elo.get(away),
        "home_form": home_form.get("form_summary", []),
        "away_form": away_form.get("form_summary", []),
        "model_version": stores.metadata.get("model_version"),
        # How much of this was real data vs. training-set averages. A caller can
        # use this to mark a prediction as thin rather than presenting a guess
        # about two unknown clubs with the same confidence as a derby.
        "coverage": diagnostics,
    }


@app.get("/health")
def health():
    stores: Stores = state["stores"]
    return {
        "status": "ok" if stores else "loading",
        "seeds": len(state["boosters"]),
        "features": len(stores.features) if stores else 0,
        "teams": len(stores.elo) if stores else 0,
        "model_version": stores.metadata.get("model_version") if stores else None,
    }


@app.get("/model")
def model_info():
    stores: Stores = state["stores"]
    if not stores:
        raise HTTPException(503, "model not loaded")
    meta = dict(stores.metadata)
    meta["leagues"] = sorted(stores.league_map)
    meta["n_teams"] = len(stores.elo)
    return meta


@app.get("/teams")
def teams(league: Optional[str] = None):
    stores: Stores = state["stores"]
    if league:
        table = (stores.tables.get(league) or {}).get("table") or {}
        names = sorted(table)
    else:
        names = sorted(set(stores.elo) | set(stores.form))
    return {"count": len(names), "teams": names}


def _prepare(req: PredictRequest):
    stores: Stores = state["stores"]
    home, home_exact = resolve_team(req.home_team)
    away, away_exact = resolve_team(req.away_team)
    if home is None or away is None:
        unknown = [n for n, r in ((req.home_team, home), (req.away_team, away)) if r is None]
        raise HTTPException(404, f"unknown club(s): {', '.join(unknown)}")

    kickoff = date.today()
    if req.kickoff:
        try:
            kickoff = datetime.fromisoformat(req.kickoff.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    row, diagnostics = build_row(stores, home, away, req.league, kickoff)
    diagnostics["home_name_exact"] = home_exact
    diagnostics["away_name_exact"] = away_exact
    return home, away, row, diagnostics


@app.post("/predict")
def predict(req: PredictRequest):
    stores: Stores = state["stores"]
    if not stores:
        raise HTTPException(503, "model not loaded")
    home, away, row, diagnostics = _prepare(req)
    probs = _predict_rows([row])[0]
    return _shape(probs, stores, diagnostics, home, away, req.league)


@app.post("/predict/batch")
def predict_batch(req: BatchRequest):
    """One DMatrix for the whole slate — the dashboard asks for ~100 at a time,
    and doing that as 100 round trips is the difference between a fast page and
    a slow one."""
    stores: Stores = state["stores"]
    if not stores:
        raise HTTPException(503, "model not loaded")
    if not req.fixtures:
        return {"predictions": []}

    prepared, failures = [], []
    for index, fixture in enumerate(req.fixtures):
        try:
            home, away, row, diagnostics = _prepare(fixture)
            prepared.append({"index": index, "home": home, "away": away,
                             "row": row, "diagnostics": diagnostics,
                             "league": fixture.league})
        except HTTPException as exc:
            failures.append({"index": index, "home_team": fixture.home_team,
                             "away_team": fixture.away_team, "error": exc.detail})

    results: list[dict] = []
    if prepared:
        probs = _predict_rows([item["row"] for item in prepared])
        for item, p in zip(prepared, probs):
            shaped = _shape(p, stores, item["diagnostics"],
                            item["home"], item["away"], item["league"])
            shaped["index"] = item["index"]
            results.append(shaped)

    return {"predictions": results, "failed": failures,
            "model_version": stores.metadata.get("model_version")}
