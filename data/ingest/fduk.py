"""Parser: football-data.co.uk match CSVs (the E0 / D1 / SP1 / … files in unsorted/).

Gotchas this handles, all learned from the real dump:
  * UTF-8 BOM on some files -> first header reads `ï»¿Div`. Read with utf-8-sig,
    and strip a stray BOM defensively anyway.
  * latin-1 bytes in a few older files (referee names) -> fall back on decode error.
  * Filename never tells you the season; the `(1)…(8)` suffixes are browser
    download numbering. Season comes from the Date column.
  * The current season's file is partial. That is expected, not an error.
"""
import os
import pandas as pd

from ingest import schema
from ingest.seasons import resolve_file_season, season_series
from leagues import BY_DIV

SOURCE = "football-data.co.uk"
REQUIRED = {"Div", "Date", "HomeTeam", "AwayTeam"}


def _read_csv(path: str) -> pd.DataFrame:
    """BOM-safe, encoding-tolerant read."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, encoding=encoding, on_bad_lines="skip",
                             low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeDecodeError("fduk", b"", 0, 1, f"cannot decode {path}")
    # Belt and braces: kill any BOM that survived and trim header whitespace.
    df.columns = [str(c).replace("﻿", "").strip() for c in df.columns]
    return df


def looks_like_fduk(path: str) -> bool:
    try:
        head = _read_csv(path).head(0)
    except Exception:
        return False
    return REQUIRED.issubset(set(head.columns))


def parse(path: str) -> pd.DataFrame:
    """One football-data.co.uk CSV -> canonical rows. Unknown Div codes are dropped."""
    raw = _read_csv(path)
    if not REQUIRED.issubset(set(raw.columns)):
        return schema.empty()

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(raw["Date"], dayfirst=True, errors="coerce",
                                 format="mixed")
    out["_div"] = raw["Div"].astype(str).str.replace("﻿", "", regex=False).str.strip()
    out["home_team"] = raw["HomeTeam"].astype(str).str.strip()
    out["away_team"] = raw["AwayTeam"].astype(str).str.strip()
    out["home_score"] = pd.to_numeric(raw.get("FTHG"), errors="coerce")
    out["away_score"] = pd.to_numeric(raw.get("FTAG"), errors="coerce")

    if "FTR" in raw.columns:
        out["outcome"] = raw["FTR"].astype(str).str.strip().map(schema.OUTCOME)
    else:
        out["outcome"] = pd.NA
    # Derive the outcome from the score when FTR is blank/garbled.
    missing = out["outcome"].isna()
    if missing.any():
        out.loc[missing, "outcome"] = [
            schema.result_to_outcome(h, a)
            for h, a in zip(out.loc[missing, "home_score"], out.loc[missing, "away_score"])
        ]

    # Bet365 closing 1X2 odds when present; Avg* is the fallback (some files drop B365).
    for col, b365, avg in (("odds_home", "B365H", "AvgH"),
                           ("odds_draw", "B365D", "AvgD"),
                           ("odds_away", "B365A", "AvgA")):
        primary = pd.to_numeric(raw.get(b365), errors="coerce") if b365 in raw else pd.Series(pd.NA, index=raw.index)
        backup = pd.to_numeric(raw.get(avg), errors="coerce") if avg in raw else pd.Series(pd.NA, index=raw.index)
        out[col] = primary.fillna(backup)

    # Match statistics. Present in every season file since ~2000; blank in the
    # odd row, which is fine. These are POST-match measurements — see the note in
    # ingest/schema.py about never feeding them to the model directly.
    for col, raw_col in (("home_shots", "HS"), ("away_shots", "AS"),
                         ("home_shots_on_target", "HST"), ("away_shots_on_target", "AST"),
                         ("home_corners", "HC"), ("away_corners", "AC"),
                         ("home_fouls", "HF"), ("away_fouls", "AF"),
                         ("home_yellow", "HY"), ("away_yellow", "AY"),
                         ("home_red", "HR"), ("away_red", "AR"),
                         ("home_half_score", "HTHG"), ("away_half_score", "HTAG")):
        out[col] = pd.to_numeric(raw.get(raw_col), errors="coerce") if raw_col in raw else pd.NA

    # Division -> league + country (from leagues.py, NOT from the filename).
    out["league"] = out["_div"].map(lambda d: BY_DIV[d].name if d in BY_DIV else None)
    out["country"] = out["_div"].map(lambda d: BY_DIV[d].country if d in BY_DIV else None)
    # Each football-data.co.uk file is exactly ONE season, so resolve it per file:
    # that keeps the COVID-extended 2019/20 season (which ran into July 2020) whole
    # instead of splitting its tail into 2020/21.
    out["season"] = resolve_file_season(out["date"])
    out["source"] = SOURCE
    out["source_file"] = os.path.basename(path)

    out = out.dropna(subset=["date", "league", "home_team", "away_team", "outcome"])
    out = out[(out["home_team"] != "") & (out["away_team"] != "")]
    out = out[out["home_team"].str.lower() != "nan"]
    return schema.conform(out)
