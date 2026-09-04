"""The one canonical schema every source is normalised into."""
import pandas as pd

# Match statistics: only football-data.co.uk carries them, everything else leaves
# them blank. Nullable on purpose -- the model treats a blank as "unknown".
STAT_COLUMNS = [
    "home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
    "home_corners", "away_corners", "home_fouls", "away_fouls",
    "home_yellow", "away_yellow", "home_red", "away_red",
    # Half-time score: 100% present in the dump, and the only in-match state we
    # keep. Useful as form signal (a side that keeps drawing at the break).
    "home_half_score", "away_half_score",
]

# All of the above are measured during the match, so they describe what happened
# rather than anything known beforehand.

COLUMNS = [
    "date", "season", "league", "country", "home_team", "away_team",
    "home_score", "away_score", "outcome",
    "odds_home", "odds_draw", "odds_away", *STAT_COLUMNS, "source",
]

# 1 = home win, 2 = draw, 3 = away win
OUTCOME = {"H": 1, "D": 2, "A": 3}

# Lower number = higher trust; wins on duplicate (date, home_team, away_team).
# football-data.co.uk first because it is the only source carrying bookmaker odds.
SOURCE_PRIORITY = {
    "football-data.co.uk": 0,
    # betexplorer sits high because it is the ONLY source carrying odds for the
    # SA league; on a fixture everything agrees about, we want its row to be the
    # one that survives, odds and all.
    "betexplorer": 1,
    "API-Football": 2,
    "football-data.org": 3,
    "openfootball": 4,
    "globalsportsarchive": 5,
    "TheSportsDB": 6,
    "FootyStats": 7,
}

# One status vocabulary, pooled across sources, so downstream code (and the web
# app) never has to know which API a row came from.
STATUS_FINISHED = {"FT", "AET", "PEN", "FINISHED", "AWARDED", "MATCH FINISHED"}
STATUS_IN_PLAY = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE",
                  "IN_PLAY", "PAUSED"}
STATUS_SCHEDULED = {"NS", "TBD", "SCHEDULED", "TIMED"}
STATUS_OFF = {"PST", "POSTPONED", "CANC", "CANCELLED", "ABD", "SUSPENDED", "WO", "AWD"}


def is_finished(status) -> bool:
    return str(status).strip().upper() in STATUS_FINISHED


FIXTURE_COLUMNS = [
    "kickoff_utc", "status", "minute", "league", "country", "season",
    "home_team", "away_team", "home_score", "away_score", "venue",
    "source", "source_match_id", "fetched_at",
]


def empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def conform(df: pd.DataFrame) -> pd.DataFrame:
    """Add any missing canonical columns and put them in order."""
    df = df.copy()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[COLUMNS]


def result_to_outcome(home: float, away: float):
    if pd.isna(home) or pd.isna(away):
        return pd.NA
    return 1 if home > away else (3 if away > home else 2)
