"""Parser: FootyStats exports (the `england-premier-league-*-2018-to-2019-stats.csv` set).

A different schema, split across league/matches/teams/players files. Only the
*-matches-* file holds one row per match, so that is the only one we ingest;
the rest are aggregates the model does not train on. Off by default (`--footystats`)
because football-data.co.uk already covers the same fixtures with better odds —
FootyStats sits at the bottom of SOURCE_PRIORITY as a cross-check only.
"""
import os
import re
import pandas as pd

from ingest import schema
from ingest.seasons import resolve_file_season
from leagues import BY_NAME

SOURCE = "FootyStats"
REQUIRED = {"date_GMT", "home_team_name", "away_team_name",
            "home_team_goal_count", "away_team_goal_count"}

# filename slug -> our canonical league
SLUG_LEAGUES = {
    "england-premier-league": "Premier League",
    "england-championship": "Championship",
    "england-league-one": "League One",
    "germany-bundesliga": "Bundesliga",
    "italy-serie-a": "Serie A",
    "spain-la-liga": "La Liga",
    "france-ligue-1": "Ligue 1",
    "netherlands-eredivisie": "Eredivisie",
    "portugal-liga-nos": "Liga Portugal",
    "portugal-primeira-liga": "Liga Portugal",
    "scotland-premiership": "Scottish Premiership",
    "south-africa-premier-soccer-league": "Betway Premiership",
}


def is_match_file(path: str) -> bool:
    name = os.path.basename(path).lower()
    if "-matches-" not in name:
        return False
    try:
        head = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
    except Exception:
        return False
    return REQUIRED.issubset(set(head.columns))


def league_from_filename(path: str):
    name = os.path.basename(path).lower()
    for slug, league in SLUG_LEAGUES.items():
        if name.startswith(slug):
            return BY_NAME.get(league)
    return None


def parse(path: str) -> pd.DataFrame:
    """One FootyStats *-matches-* CSV -> canonical rows.

    Unlike football-data.co.uk there is no Div column, so the league genuinely has
    to come from the filename slug here — it is the only signal the file carries.
    """
    league = league_from_filename(path)
    if league is None:
        return schema.empty()
    raw = pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="skip", low_memory=False)
    if not REQUIRED.issubset(set(raw.columns)):
        return schema.empty()

    out = pd.DataFrame()
    # "Aug 10 2018 - 7:00pm" -> drop the time half, parse the date half.
    date_part = raw["date_GMT"].astype(str).str.split(" - ").str[0].str.strip()
    out["date"] = pd.to_datetime(date_part, format="%b %d %Y", errors="coerce")
    out["home_team"] = raw["home_team_name"].astype(str).str.strip()
    out["away_team"] = raw["away_team_name"].astype(str).str.strip()
    out["home_score"] = pd.to_numeric(raw["home_team_goal_count"], errors="coerce")
    out["away_score"] = pd.to_numeric(raw["away_team_goal_count"], errors="coerce")
    out["outcome"] = [schema.result_to_outcome(h, a)
                      for h, a in zip(out["home_score"], out["away_score"])]
    out["odds_home"] = pd.to_numeric(raw.get("odds_ft_home_team_win"), errors="coerce")
    out["odds_draw"] = pd.to_numeric(raw.get("odds_ft_draw"), errors="coerce")
    out["odds_away"] = pd.to_numeric(raw.get("odds_ft_away_team_win"), errors="coerce")
    out["league"] = league.name
    out["country"] = league.country
    out["season"] = resolve_file_season(out["date"])
    out["source"] = SOURCE

    if "status" in raw.columns:                       # keep only played matches
        out = out[raw["status"].astype(str).str.lower().eq("complete").values]
    out = out.dropna(subset=["date", "home_team", "away_team", "outcome"])
    # FootyStats writes odds as 0 when it has none.
    for col in ("odds_home", "odds_draw", "odds_away"):
        out[col] = out[col].where(out[col] > 1.0)
    return schema.conform(out)
