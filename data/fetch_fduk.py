"""Download the current season's football-data.co.uk CSVs into unsorted/.

    python fetch_fduk.py                 # this season, every league with a Div code
    python fetch_fduk.py --season 2526   # a specific season code

Why this exists: football-data.co.uk is the only free source that carries
shots, shots on target, corners and closing odds — the model's sparse
features. The results the ETL folds back from Turso have none of those, so
left alone the current season would slowly hollow those features out as the
training set grew. The retrain workflow runs this first, so the fresh rows
arrive with their stats.

The files land next to the hand-downloaded dump under a STABLE name
(`E0_2627.csv`, not the browser's `E0 (11).csv`), so re-running overwrites the
same file. The ordinary `pipeline.py` scan then picks them up: the manifest
hashes content and re-parses a file only when the site has added rows, and
normalize() dedupes them against anything the same match already has.
"""
import argparse
import os
import sys
from datetime import date

import requests

from ingest.env import offline
from leagues import REGISTRY, SEASON_CODES

BASE = "https://www.football-data.co.uk/mmz4281"          # /{season}/{div}.csv
UNSORTED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unsorted")
UA = "winscope-etl/1.0 (+https://github.com)"


def current_season_code(today: date | None = None) -> str:
    """'2627' for anything from July 2026 to June 2027 — the CSV convention."""
    today = today or date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def fetch(season: str, div: str) -> bytes | None:
    url = f"{BASE}/{season}/{div}.csv"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA})
    except requests.RequestException as exc:
        print(f"  ! {div} {season}: {exc}")
        return None
    if r.status_code != 200 or not r.content:
        print(f"  ! {div} {season}: HTTP {r.status_code}")
        return None
    # A live file always has a header plus at least one row; anything shorter
    # is the site's "no such season yet" page, not data.
    if b"HomeTeam" not in r.content[:2048]:
        print(f"  ! {div} {season}: not a match CSV (season not started?)")
        return None
    return r.content


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--season", default=None, metavar="CODE",
                        help="season code like 2627 (default: the current one)")
    args = parser.parse_args(argv)

    if offline():
        print("WINSCOPE_OFFLINE is set — not downloading")
        return 0

    season = args.season or current_season_code()
    if season not in SEASON_CODES:
        print(f"season {season} is not in leagues.SEASON_CODES — add it there first")
        return 1

    os.makedirs(UNSORTED, exist_ok=True)
    changed = unchanged = failed = 0
    for lg in REGISTRY:
        if not lg.fduk_div:
            continue                                   # Betway Premiership: not on FDUK
        content = fetch(season, lg.fduk_div)
        if content is None:
            failed += 1
            continue
        path = os.path.join(UNSORTED, f"{lg.fduk_div}_{season}.csv")
        if os.path.exists(path) and open(path, "rb").read() == content:
            unchanged += 1
            continue
        with open(path, "wb") as f:
            f.write(content)
        rows = max(content.count(b"\n") - 1, 0)
        print(f"  + {lg.name:<22} {os.path.basename(path)}  ~{rows} rows")
        changed += 1

    print(f"\nfootball-data.co.uk {season}: {changed} updated · {unchanged} unchanged · {failed} unavailable")
    # A site outage must not sink the rest of the weekly job — the Turso fold
    # still runs and the next week catches up — so this never fails the step.
    return 0


if __name__ == "__main__":
    sys.exit(main())
