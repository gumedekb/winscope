"""Turso retention: drop rows older than N days, except the Betway Premiership.

    python prune.py                    # keep 180 days (PRUNE_KEEP_DAYS overrides)
    python prune.py --keep-days 90
    python prune.py --dry-run          # report only

Why: the `fixtures` table was designed as an append-only log so the track
record could score every prediction ever made, and the free Turso tier is
sized for it comfortably today — but it only grows. This bounds it. The SA
league is exempt because its history is hard to get anywhere else (no
football-data.co.uk file, thin API coverage), so Turso IS its archive.

Ordering matters. The retrain workflow runs this AFTER `pipeline.py
--from-turso` has folded finished results into model_data.csv and pushed it,
and this script enforces the same rule on its own: a finished match whose
match_key is not in model_data.csv is never deleted, whatever its age. Results
move into the training set first, or they stay.

What goes, per match_key older than the cutoff (kickoff_utc) outside the SA
league: the `fixtures` row, and the web app's `predictions` and `ai_insights`
rows for it if those tables exist. `betslip` is left alone — it is the user's
own record and tiny.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

from clean.normalize import match_key
from leagues import SA_LEAGUE
from sinks.turso import TABLE, Turso, TursoError, configured, counts

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DATA = os.path.join(DATA_DIR, "output", "model_data.csv")
DEFAULT_KEEP_DAYS = int(os.environ.get("PRUNE_KEEP_DAYS", "180"))

# Tables the web app keys on the same match_key. Created lazily by the app, so
# each is checked for before it is touched.
WEB_TABLES = ("predictions", "ai_insights")


def training_keys(path: str = MODEL_DATA) -> set[str]:
    if not os.path.exists(path):
        return set()
    df = pd.read_csv(path, usecols=["date", "home_team", "away_team"])
    return set(match_key(df))


def table_exists(client: Turso, name: str) -> bool:
    rows = client.rows("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name])
    return bool(rows)


def delete_keys(client: Turso, table: str, keys: list[str]) -> int:
    return client.batch([(f"DELETE FROM {table} WHERE match_key = ?", [k]) for k in keys])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
                        help=f"retention window in days (default {DEFAULT_KEEP_DAYS})")
    parser.add_argument("--dry-run", action="store_true", help="report, delete nothing")
    args = parser.parse_args(argv)

    if not configured():
        print("Turso is not configured — set TURSO_FIXTURES_URL/_TOKEN")
        return 1
    if args.keep_days < 30:
        print("refusing a window under 30 days — the dashboard's form strips and "
              "track record read from these rows")
        return 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.keep_days)).isoformat(timespec="seconds")
    keep_league = SA_LEAGUE.name
    print(f"Turso retention: {args.keep_days} days (kickoff before {cutoff[:10]}), "
          f"keeping every '{keep_league}' row")

    try:
        client = Turso()
        client.ensure_schema()
        before = counts(client)
        candidates = client.rows(
            f"SELECT match_key, status_group, league FROM {TABLE} "
            f"WHERE league != ? AND kickoff_utc < ?", [keep_league, cutoff])
    except TursoError as exc:
        print(f"  ! {exc}")
        return 1

    if not candidates:
        print(f"  nothing older than the window ({before['total']} rows in the table)")
        return 0

    known = training_keys()
    to_delete, held_back = [], []
    for row in candidates:
        finished = row["status_group"] == "finished"
        if finished and row["match_key"] not in known:
            held_back.append(row)                      # a result the training set lacks
        else:
            to_delete.append(row["match_key"])

    doomed = set(to_delete)
    by_league: dict[str, int] = {}
    for row in candidates:
        if row["match_key"] in doomed:
            by_league[row["league"]] = by_league.get(row["league"], 0) + 1
    for league, n in sorted(by_league.items()):
        print(f"    {league:<24} {n:>5}")
    if held_back:
        print(f"  {len(held_back)} finished row(s) kept: their results are not in model_data.csv yet "
              f"(run `pipeline.py --from-turso` and commit it first)")
    if not to_delete:
        print("  nothing to delete")
        return 0
    if args.dry_run:
        print(f"  dry run: would delete {len(to_delete)} row(s) from {TABLE}"
              + "".join(f" + {t}" for t in WEB_TABLES))
        return 0

    try:
        removed = delete_keys(client, TABLE, to_delete)
        print(f"  deleted {removed} row(s) from {TABLE}")
        for table in WEB_TABLES:
            if table_exists(client, table):
                n = delete_keys(client, table, to_delete)
                print(f"  deleted {n} row(s) from {table}")
        after = counts(client)
    except TursoError as exc:
        print(f"  ! {exc}")
        return 1

    print(f"  table now: {after['total']} total · {after['finished']} finished "
          f"(was {before['total']} · {before['finished']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
