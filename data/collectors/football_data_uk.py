"""
Collector: football-data.co.uk  (FREE, no API key)
Bulk historical results + bookmaker odds for the main European leagues.
This is the primary TRAINING-history source. Coverage note: no SA PSL here.
"""
import io
import requests
import pandas as pd
from leagues import LEAGUES, SEASON_CODES

BASE = "https://www.football-data.co.uk/mmz4281"  # /{season}/{div}.csv
OUTCOME = {"H": 1, "D": 2, "A": 3}  # 1=home win, 2=draw, 3=away win


def _one(season: str, div: str, league_name: str) -> pd.DataFrame:
    url = f"{BASE}/{season}/{div}.csv"
    r = requests.get(url, timeout=30)
    if r.status_code != 200 or not r.content:
        return pd.DataFrame()
    try:
        raw = pd.read_csv(io.StringIO(r.content.decode("latin-1")), on_bad_lines="skip")
    except Exception as e:
        print(f"  ! parse fail {url}: {e}")
        return pd.DataFrame()
    if "HomeTeam" not in raw or "FTR" not in raw:
        return pd.DataFrame()

    df = pd.DataFrame()
    df["date"] = pd.to_datetime(raw.get("Date"), dayfirst=True, errors="coerce")
    df["league"] = league_name
    df["home_team"] = raw["HomeTeam"]
    df["away_team"] = raw["AwayTeam"]
    df["home_score"] = pd.to_numeric(raw.get("FTHG"), errors="coerce")
    df["away_score"] = pd.to_numeric(raw.get("FTAG"), errors="coerce")
    df["outcome"] = raw["FTR"].map(OUTCOME)
    # Bet365 closing 1X2 odds when present (feed the odds-blend feature later)
    df["odds_home"] = pd.to_numeric(raw.get("B365H"), errors="coerce")
    df["odds_draw"] = pd.to_numeric(raw.get("B365D"), errors="coerce")
    df["odds_away"] = pd.to_numeric(raw.get("B365A"), errors="coerce")
    df["source"] = "football-data.co.uk"
    return df.dropna(subset=["home_team", "away_team", "outcome"])


def collect() -> pd.DataFrame:
    frames = []
    for name, _country, div, _af in LEAGUES:
        if not div:
            continue
        for season in SEASON_CODES:
            part = _one(season, div, name)
            if not part.empty:
                print(f"  + {name} {season}: {len(part)} rows")
                frames.append(part)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    out = collect()
    print(f"football-data.co.uk total: {len(out)} rows")
