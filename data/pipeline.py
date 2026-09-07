#!/usr/bin/env python3
"""WinScope data pipeline (Mode B) — the app the data/README.md spec describes.

  unsorted/*.csv  ──▶  parse ──▶ normalise ──▶ dedupe ──▶  output/model_data.csv
       + APIs     ──▶  Betway Premiership history + live/upcoming fixtures

Default run is 100% offline and free: it scans unsorted/, ingests only files that
are new or changed since the last run, merges them into output/model_data.csv,
and prints the coverage report. The APIs are strictly opt-in (--with-api) and
every one of them is capped at 90% of its free tier — see apis/ratelimit.py.

    python pipeline.py                  # files only (free, no network)
    python pipeline.py --with-api       # + SA PSL history + live/upcoming fixtures
    python pipeline.py --live           # fixtures refresh only (+ push to Turso)
    python pipeline.py --live --providers football-data,thesportsdb
                                        # same, but spends no API-Football budget
    python pipeline.py --quota          # what's left of today's API budgets
    python pipeline.py --full-rescan    # ignore the manifest, reprocess everything
"""
import argparse
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clean.normalize import align_to_vocabulary, normalize             # noqa: E402
from ingest import (audit, canonical, consolidate, fduk,   # noqa: E402
                    footystats, report, schema)
from ingest.env import DATA_DIR, load_env                               # noqa: E402
from ingest.manifest import Manifest, discover                          # noqa: E402
from ingest.seasons import current_season_start_year                    # noqa: E402
from leagues import TARGET_SEASONS                                      # noqa: E402

SRC_DIR = os.path.join(DATA_DIR, "unsorted")
OUT_DIR = os.path.join(DATA_DIR, "output")
MODEL_DATA = os.path.join(OUT_DIR, "model_data.csv")
FIXTURES = os.path.join(OUT_DIR, "fixtures.csv")
CONFLICTS = os.path.join(OUT_DIR, "conflicts.csv")


def _rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


# ---------------------------------------------------------------- stage 1
def ingest_files(full_rescan: bool = False, with_footystats: bool = False,
                 verbose: bool = True):
    """Scan unsorted/, parse whatever is new, return (rows, stats)."""
    manifest = Manifest(OUT_DIR)
    files = discover(SRC_DIR)
    stats = {"seen": len(files), "parsed": 0, "skipped": 0, "unrecognised": [],
             "rows": 0, "season_by_file": {}}
    frames = []

    for name, path, digest, size in files:
        if not full_rescan and not manifest.is_new_or_changed(name, digest):
            stats["skipped"] += 1
            continue
        if fduk.looks_like_fduk(path):
            rows = fduk.parse(path)
        elif canonical.looks_canonical(path):
            # Already in our schema — e.g. anything written by scrape.py.
            rows = canonical.parse(path)
        elif footystats.is_match_file(path):
            if not with_footystats:
                stats["unrecognised"].append(f"{name} (FootyStats — use --footystats)")
                continue
            rows = footystats.parse(path)
        else:
            stats["unrecognised"].append(f"{name} (unknown layout)")
            continue

        manifest.record(name, digest, size, len(rows))
        stats["parsed"] += 1
        stats["rows"] += len(rows)
        if not rows.empty:
            frames.append(rows)
        seasons = sorted(rows["season"].dropna().unique()) if not rows.empty else []
        if seasons:
            stats["season_by_file"][name] = f"{rows['league'].iloc[0]} {seasons[0]}"
        if verbose:
            league = rows["league"].iloc[0] if not rows.empty else "—"
            print(f"  + {name:<48} {len(rows):>5} rows  {league} "
                  f"{seasons[0] if seasons else ''}")

    manifest.save()
    combined = pd.concat(frames, ignore_index=True) if frames else schema.empty()
    return combined, stats


# ---------------------------------------------------------------- stage 2
def api_stage(args, have, verbose: bool = True):
    from apis import topup                       # imported late: no network on a plain run
    use_of = args.openfootball or args.cross_check
    return topup.run(sa_history=not (args.live or args.cross_check),
                     fixtures=not args.cross_check,
                     openfootball_seasons=TARGET_SEASONS if use_of else None,
                     openfootball_mode="crosscheck" if args.cross_check else "gapfill",
                     have=have, all_leagues=args.all_leagues,
                     force=args.force_refresh, verbose=verbose,
                     providers=getattr(args, "providers", None))


def write_fixtures(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Write fixtures.csv — ONLY what has not finished.

    The CSV is the "what's on" view: a completed match is already in
    model_data.csv, so keeping it here too would just be a stale second copy.
    Turso is the opposite and gets the full frame, finished matches included,
    because that table is the permanent record the model gets scored against.
    Returns the filtered frame that was written.
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    if not fixtures.empty:
        fixtures = fixtures[~fixtures["status"].map(schema.is_finished)].copy()
        fixtures = fixtures.sort_values("kickoff_utc").reset_index(drop=True)
    fixtures.to_csv(FIXTURES, index=False)
    print(f"\nWrote {len(fixtures)} live/upcoming fixtures -> {FIXTURES}")
    if fixtures.empty:
        return
    in_play = fixtures[fixtures["status"].isin(schema.STATUS_IN_PLAY)]
    print(f"    {len(in_play)} in play, {len(fixtures) - len(in_play)} upcoming, "
          f"through {str(fixtures['kickoff_utc'].max())[:16]}")
    for league, n in fixtures.groupby("league").size().sort_values(ascending=False).items():
        marker = fixtures[(fixtures["league"] == league) &
                          fixtures["status"].isin(schema.STATUS_IN_PLAY)]
        print(f"    {league:<24} {n:>3}" + (f"  ({len(marker)} live)" if len(marker) else ""))
    return fixtures


def push_to_turso(fixtures: pd.DataFrame, args) -> None:
    """Publish fixtures to Turso. Upsert only — finished matches are retained."""
    if args.no_turso:
        return
    from sinks import turso
    if not turso.configured():
        print("\n  (Turso not configured — set TURSO_FIXTURES_URL/_TOKEN in .env "
              "to publish fixtures)")
        return
    _rule("5. Publishing to Turso (finished matches retained, never deleted)")
    try:
        turso.push(fixtures)
    except turso.TursoError as exc:
        # A database hiccup must not lose the run's work: the CSVs are written.
        print(f"  ! Turso push failed: {exc}")
        print("    output/fixtures.csv is still written — re-run "
              "`python pipeline.py --live` to retry the push")


def show_quota() -> None:
    from apis import api_football, football_data_org, openfootball, thesportsdb
    from apis.ratelimit import Budget, QuotaStore
    store = QuotaStore()
    print("\nAPI budgets (hard tier limit -> our 90% cap -> used today)")
    print("-" * 68)
    rows = [
        (api_football.PROVIDER, Budget(api_football.PROVIDER, api_football.FREE_PER_DAY,
                                       api_football.FREE_PER_MINUTE),
         "key set" if api_football.available() else "NO KEY"),
        (football_data_org.PROVIDER, Budget(football_data_org.PROVIDER, None,
                                            football_data_org.FREE_PER_MINUTE),
         "key set" if football_data_org.available() else "NO KEY"),
        (thesportsdb.PROVIDER, Budget(thesportsdb.PROVIDER, None,
                                      thesportsdb.FREE_PER_MINUTE),
         "free key" if thesportsdb.using_free_key() else "patreon key"),
        (openfootball.PROVIDER, Budget(openfootball.PROVIDER, None,
                                       openfootball.POLITE_PER_MINUTE), "no key needed"),
    ]
    for provider, budget, note in rows:
        used = store.used_today(provider)
        cap = budget.day_cap
        left = "unlimited" if cap is None else f"{max(0, cap - used)} left"
        print(f"  {provider:<20} {budget.describe().split(': ', 1)[1]:<34} "
              f"used {used:>3}  {left}  [{note}]")
    print("\nCaps are enforced in apis/ratelimit.py and persisted in "
          "output/.quota.json,\nso every run of the day shares one budget.")


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="WinScope ETL — unsorted/ CSVs + APIs -> output/model_data.csv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("    python")[0].rsplit("\n\n", 1)[-1])
    parser.add_argument("--with-api", action="store_true",
                        help="also run the API stage (SA PSL history + live/upcoming)")
    parser.add_argument("--live", action="store_true",
                        help="fixtures only: refresh live/upcoming, skip file ingest")
    parser.add_argument("--api-only", action="store_true",
                        help="run the API stage without scanning unsorted/")
    parser.add_argument("--full-rescan", action="store_true",
                        help="ignore output/.manifest.json and reparse every CSV")
    parser.add_argument("--footystats", action="store_true",
                        help="also ingest the FootyStats *-matches-* files")
    parser.add_argument("--openfootball", action="store_true",
                        help="fill missing league-seasons from openfootball (free, no key)")
    parser.add_argument("--cross-check", action="store_true",
                        help="diff openfootball against our data WITHOUT merging it")
    parser.add_argument("--providers", default="all", metavar="LIST",
                        help="comma-separated API stage providers to run: "
                             "api-football, football-data, thesportsdb, "
                             "openfootball (default: all). Naming only the "
                             "uncapped ones lets a frequent cron run without "
                             "spending API-Football's 100/day budget.")
    parser.add_argument("--all-leagues", action="store_true",
                        help="keep API fixtures for leagues outside leagues.py too")
    parser.add_argument("--force-refresh", action="store_true",
                        help="bypass the response cache (spends real quota)")
    parser.add_argument("--report-only", action="store_true",
                        help="print the coverage report for the existing CSV and exit")
    parser.add_argument("--quota", action="store_true",
                        help="show today's API budget usage and exit")
    parser.add_argument("--audit-teams", action="store_true",
                        help="find cross-source team-name mismatches and exit")
    parser.add_argument("--no-turso", action="store_true",
                        help="skip the Turso push even though credentials are set")
    parser.add_argument("--turso-status", action="store_true",
                        help="show what is currently in the Turso fixtures table")
    parser.add_argument("--turso-migrate", action="store_true",
                        help="rewrite team names already in Turso to match "
                             "model_data.csv (run after editing team_aliases.json)")
    parser.add_argument("--from-turso", action="store_true",
                        help="fold finished matches from Turso back into model_data.csv "
                             "(run this before a retrain)")
    args = parser.parse_args(argv)

    load_env()
    os.makedirs(OUT_DIR, exist_ok=True)
    current_season = f"{current_season_start_year()}/{(current_season_start_year() + 1) % 100:02d}"

    if args.quota:
        show_quota()
        return 0

    if args.turso_status:
        from sinks import turso
        if not turso.configured():
            print("Turso is not configured — set TURSO_FIXTURES_URL and "
                  "TURSO_FIXTURES_TOKEN in data/.env")
            return 1
        turso.print_status()
        return 0

    previous = consolidate.load_existing(MODEL_DATA)

    if args.turso_migrate:
        from sinks import turso
        if not turso.configured():
            print("Turso is not configured — set TURSO_FIXTURES_URL/_TOKEN in .env")
            return 1
        _rule("Aligning team names already in Turso to model_data.csv")
        stats = turso.migrate_team_names(normalize(previous))
        print(f"\n  {stats['checked']} rows checked · {stats['renamed']} renamed · "
              f"{stats['merged']} duplicate copies dropped")
        return 0

    if args.audit_teams:
        _rule("Team-name audit (grow clean/team_aliases.json from this)")
        frames = [normalize(previous)]
        if os.path.exists(FIXTURES):
            fx = pd.read_csv(FIXTURES)
            if not fx.empty:
                frames.append(fx[["league", "home_team", "away_team", "source"]])
        audit.print_audit(pd.concat(frames, ignore_index=True))
        return 0

    if args.report_only:
        _rule("Coverage report (existing output/model_data.csv)")
        report.print_coverage(normalize(previous), current_season)
        return 0

    incoming_frames = []
    fixtures = None
    do_files = not (args.live or args.api_only or args.cross_check or args.from_turso)
    do_api = args.with_api or args.live or args.api_only or args.cross_check

    if args.from_turso:
        _rule("Folding finished matches from Turso into model_data.csv")
        from sinks import turso
        if not turso.configured():
            print("  Turso is not configured — set TURSO_FIXTURES_URL/_TOKEN in .env")
            return 1
        try:
            results = turso.fetch_finished()
        except turso.TursoError as exc:
            print(f"  ! {exc}")
            return 1
        print(f"  {len(results)} finished match(es) in the database")
        if not results.empty:
            incoming_frames.append(results)

    if do_files:
        _rule(f"1. Scanning {os.path.relpath(SRC_DIR, DATA_DIR)}/ for new or changed CSVs")
        rows, stats = ingest_files(args.full_rescan, args.footystats)
        print(f"\n  {stats['seen']} files seen · {stats['parsed']} parsed · "
              f"{stats['skipped']} unchanged (skipped) · {stats['rows']:,} raw rows")
        for note in stats["unrecognised"]:
            print(f"    - not ingested: {note}")
        report.print_duplicate_seasons(stats["season_by_file"])
        if not rows.empty:
            incoming_frames.append(rows)

    if do_api:
        _rule("2. API top-up (Betway Premiership history + live/upcoming fixtures)")
        # What we already hold going INTO the API stage = the previous CSV plus
        # whatever the file scan just produced. Without the second half, a fresh
        # checkout would ask openfootball to refill all 81 league-seasons it can
        # already read off disk.
        baseline = normalize(pd.concat([previous] + incoming_frames, ignore_index=True)) \
            if (not previous.empty or incoming_frames) else previous
        result = api_stage(args, baseline)
        if args.cross_check:
            _rule("Cross-check: openfootball vs output/model_data.csv (nothing merged)")
            check = consolidate.diff(baseline, normalize(result.crosscheck))
            print(f"  {check.unchanged:,} agree · {check.added:,} we do not have · "
                  f"{check.conflict_count:,} disagree on the score")
            if check.conflict_count:
                check.conflicts.to_csv(CONFLICTS, index=False)
                report.print_diff(check, CONFLICTS)
            if result.quota:
                for line in result.quota:
                    print(f"    {line}")
            return 0
        if not result.history.empty:
            incoming_frames.append(result.history)
        fixtures = result.fixtures
        if result.quota:
            print("\n  Budget after this run:")
            for line in result.quota:
                print(f"    {line}")

    incoming = normalize(pd.concat(incoming_frames, ignore_index=True)) \
        if incoming_frames else schema.empty()

    _rule("3. Merging into output/model_data.csv")
    diff = consolidate.diff(previous, incoming)
    merged = consolidate.merge(previous, incoming)
    merged.to_csv(MODEL_DATA, index=False, date_format="%Y-%m-%d")
    print(f"  wrote {len(merged):,} rows -> {MODEL_DATA}")
    if diff.conflict_count:
        diff.conflicts.to_csv(CONFLICTS, index=False)
    report.print_diff(diff, CONFLICTS if diff.conflict_count else None)

    if fixtures is not None:
        _rule("4. Fixtures")
        # Fixtures must speak the same club names as the training set, or the
        # web app cannot join tonight's match to that club's history.
        fixtures = align_to_vocabulary(fixtures, merged)
        write_fixtures(fixtures)
        # Turso deliberately receives the UNFILTERED frame: results that landed
        # since the last run are what a later "did the model call it?" check
        # needs, and the table never drops them.
        push_to_turso(fixtures, args)

    _rule("6. Coverage report")
    report.print_coverage(merged, current_season)

    if fixtures is not None and not fixtures.empty:
        audit.print_audit(pd.concat(
            [merged, fixtures[["league", "home_team", "away_team", "source"]]],
            ignore_index=True))
    print(f"\nDone at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
