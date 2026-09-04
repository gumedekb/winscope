"""Build the model's feature row for a fixture that has not been played.

This is a line-for-line port of `feature_row` in model/train/colab_train.py
(Cell 11). The notebook proves that function reproduces the training features
exactly, so anything that differs here is a train/serve bug. When the notebook
changes, change this file the same way.

Stores (all in artifacts/, written by Cell 12 of the notebook):
    elo_ratings.json       team -> Elo
    pi_ratings.json        team -> {home, away}          (Constantinou pi-ratings)
    team_form.json         team -> rolling form, venue splits, last match date
    h2h_records.json       "A|B" -> head-to-head rates   (key is ALPHABETICAL)
    season_tables.json     league -> {season, table}
    league_map.json        league -> integer code the model was trained with
    feature_defaults.json  training-set MEDIANS for the non-sparse features
    model_metadata.json    constants (Elo start, pi c, max rest days), sparse_features

Fallbacks mirror training exactly:
    unknown club        Elo = initial, pi = 0/0, rest = max, matches_played = 0,
                        form -> defaults
    no head-to-head     0.5 home win rate, 0.25 draw rate, 0 matches
    new season          empty table -> 0 pts / 0 gd / 0 played, position 1
    sparse features     (shots, shots-on-target, corners, odds) stay NaN — XGBoost
                        learned a "missing" branch, and whole leagues lack them
"""
import json
import math
import os
from datetime import date, datetime

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")

FORM_WINDOWS = (5, 10)
FORM_STATS = ("win_rate", "draw_rate", "goals_for", "goals_against", "ppg",
              "sot_for", "sot_against", "shots_for", "shots_against", "corners_for", "corners_against")
VENUE_STATS = ("ppg", "goals_for", "goals_against")


class Stores:
    """Everything the model needs to describe a fixture, loaded once."""

    def __init__(self, artifact_dir: str = ARTIFACT_DIR):
        self.dir = artifact_dir
        self.features: list[str] = self._load("features.json")
        self.defaults: dict[str, float] = self._load("feature_defaults.json")
        self.elo: dict[str, float] = self._load("elo_ratings.json")
        self.pi: dict[str, dict] = self._load("pi_ratings.json")
        self.form: dict[str, dict] = self._load("team_form.json")
        self.h2h: dict[str, dict] = self._load("h2h_records.json")
        self.tables: dict[str, dict] = self._load("season_tables.json")
        self.league_map: dict[str, int] = self._load("league_map.json")
        self.label_map: dict[str, int] = self._load("label_map.json")
        self.metadata: dict = self._load("model_metadata.json")

        self.sparse = set(self.metadata.get("sparse_features", []))
        self.elo_initial = float(self.metadata.get("elo", {}).get("initial", 1000.0))
        self.pi_c = float(self.metadata.get("pi", {}).get("c", 3.0))
        self.max_rest_days = int(self.metadata.get("max_rest_days", 30))
        self.classes: list[str] = self.metadata.get(
            "classes", sorted(self.label_map, key=self.label_map.get))

    def _load(self, name: str):
        with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
            return json.load(fh)

    def knows(self, team: str) -> bool:
        return team in self.elo or team in self.form


def season_of(day: date) -> str:
    """Season label for a kickoff date (Jul-Jun), matching the CSV convention."""
    year = day.year if day.month >= 7 else day.year - 1
    return f"{year}/{(year + 1) % 100:02d}"


def pi_expected_gd(rating: float, c: float) -> float:
    return math.copysign(10 ** (abs(rating) / c) - 1, rating) if rating else 0.0


def league_pos(table: dict, team: str) -> int:
    """1 + number of teams strictly ahead on (points, goal difference). Ties share a position."""
    pts, gd = table.get(team, (0, 0, 0))[:2]
    return 1 + sum(1 for t, (p, g, _) in table.items() if t != team and (p, g) > (pts, gd))


def _nan(value) -> float:
    return float("nan") if value is None else float(value)


def build_row(stores: Stores, home_team: str, away_team: str, league: str | None,
              kickoff: date | None = None) -> tuple[dict, dict]:
    """-> (feature dict in model order, diagnostics)"""
    kickoff = kickoff or date.today()
    hf = stores.form.get(home_team) or {}
    af = stores.form.get(away_team) or {}
    hp = stores.pi.get(home_team) or {"home": 0.0, "away": 0.0}
    ap = stores.pi.get(away_team) or {"home": 0.0, "away": 0.0}

    def rest(entry: dict) -> float:
        if not entry:
            return float(stores.max_rest_days)
        last = datetime.strptime(str(entry["last_match_date"])[:10], "%Y-%m-%d").date()
        return float(min((kickoff - last).days, stores.max_rest_days))

    t1, t2 = sorted([home_team, away_team])
    pair = stores.h2h.get(f"{t1}|{t2}")
    entry = stores.tables.get(league) if league else None
    table: dict = {}
    if entry and entry.get("season") == season_of(kickoff):      # new season -> empty table
        table = {t: (v["pts"], v["gd"], v["played"]) for t, v in entry["table"].items()}

    row: dict = {
        "home_elo": stores.elo.get(home_team, stores.elo_initial),
        "away_elo": stores.elo.get(away_team, stores.elo_initial),
        "home_pi_home": hp["home"], "home_pi_away": hp["away"],
        "away_pi_home": ap["home"], "away_pi_away": ap["away"],
        "pi_exp_gd": pi_expected_gd(hp["home"], stores.pi_c) - pi_expected_gd(ap["away"], stores.pi_c),
        "h2h_home_winrate": (pair["t1_win_rate"] if home_team == t1 else pair["t2_win_rate"]) if pair else 0.5,
        "h2h_draw_rate": pair["draw_rate"] if pair else 0.25,
        "h2h_matches_count": pair["matches"] if pair else 0,
        "home_days_rest": rest(hf), "away_days_rest": rest(af),
        "league_code": stores.league_map.get(league) if league else None,
    }
    for side, team in (("home", home_team), ("away", away_team)):
        pts, gd, played = table.get(team, (0, 0, 0))
        row[f"{side}_season_ppg"] = pts / played if played else None
        row[f"{side}_season_played"] = played
        row[f"{side}_season_gd"] = gd
        row[f"{side}_league_pos"] = league_pos(table, team)
    row["elo_diff"] = row["home_elo"] - row["away_elo"]
    row["pi_diff"] = (hp["home"] + hp["away"]) / 2 - (ap["home"] + ap["away"]) / 2
    row["season_pos_diff"] = row["home_league_pos"] - row["away_league_pos"]
    row["season_ppg_diff"] = _nan(row["home_season_ppg"]) - _nan(row["away_season_ppg"])
    for w in FORM_WINDOWS:
        for name in FORM_STATS:
            row[f"home_form_{name}_{w}"] = hf.get(f"form_{name}_{w}")
            row[f"away_form_{name}_{w}"] = af.get(f"form_{name}_{w}")
    for name in VENUE_STATS:
        row[f"home_venue_{name}"] = hf.get(f"venue_home_{name}")
        row[f"away_venue_{name}"] = af.get(f"venue_away_{name}")
    row["home_matches_played"] = hf.get("matches_played", 0)
    row["away_matches_played"] = af.get("matches_played", 0)

    # differentials (NaN propagates, then the fill below decides what happens to it)
    row["ppg_diff_5"] = _nan(row["home_form_ppg_5"]) - _nan(row["away_form_ppg_5"])
    row["ppg_diff_10"] = _nan(row["home_form_ppg_10"]) - _nan(row["away_form_ppg_10"])
    row["venue_ppg_diff"] = _nan(row["home_venue_ppg"]) - _nan(row["away_venue_ppg"])
    row["sot_diff_10"] = ((_nan(row["home_form_sot_for_10"]) - _nan(row["home_form_sot_against_10"]))
                          - (_nan(row["away_form_sot_for_10"]) - _nan(row["away_form_sot_against_10"])))

    # fill: non-sparse NaN -> training median; sparse stays NaN; anything the
    # model was trained with but is not produced above (e.g. odds_*) -> NaN
    filled = []
    out: dict = {}
    for name in stores.features:
        value = _nan(row.get(name))
        if math.isnan(value) and name in stores.defaults:
            value = float(stores.defaults[name])
            filled.append(name)
        out[name] = value

    diagnostics = {
        "home_known": stores.knows(home_team),
        "away_known": stores.knows(away_team),
        "league_known": bool(league) and league in stores.league_map,
        "season_table_current": bool(table),
        "h2h_matches": pair["matches"] if pair else 0,
        "home_matches_played": hf.get("matches_played", 0),
        "away_matches_played": af.get("matches_played", 0),
        "defaults_used": len(filled),
        "defaulted_features": filled[:12],
    }
    return out, diagnostics
