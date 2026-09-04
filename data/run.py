"""
WinScope ETL entrypoint.  extract -> transform -> load(csv)
Free-first waterfall: football-data.co.uk (no key) is the base training history.
Add keyed collectors (API-Football, The Odds API) later for live fixtures + thin leagues.
Run:  python run.py
Output: output/training.csv  (the model server trains on this)
"""
import os
import pandas as pd
from clean.normalize import normalize
from collectors import football_data_uk

OUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUT_CSV = os.path.join(OUT_DIR, "training.csv")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    frames = []

    print("[1/3] football-data.co.uk (free bulk history)...")
    frames.append(football_data_uk.collect())

    # TODO: add collectors.api_football (keyed) for SA PSL + Scottish + live fixtures
    # TODO: add collectors.fbref (soccerdata) for xG + women's

    raw = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
    print(f"[2/3] normalising {len(raw)} raw rows...")
    clean = normalize(raw)

    clean.to_csv(OUT_CSV, index=False)
    print(f"[3/3] wrote {len(clean)} rows -> {OUT_CSV}")
    if not clean.empty:
        print("\nRows per league:")
        print(clean.groupby('league').size().sort_values(ascending=False).to_string())
        print(f"\nDate range: {clean['date'].min().date()} -> {clean['date'].max().date()}")


if __name__ == "__main__":
    main()
