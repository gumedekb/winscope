#!/usr/bin/env python3
"""Fetch club badges once and publish them to Turso.

The dashboard was drawing generated initials because the ETL stores club *names*
(which is what joins to the model), not any provider's crest id. TheSportsDB
publishes a badge per club and needs no key, so this walks the clubs that
actually appear in recent fixtures and stores a URL for each.

It is a one-off: badges do not change, every response is cached on disk, and the
Turso rows are upserted, so re-running is free and safe. Rate limiting is the
same 90%-of-free-tier cap the rest of the pipeline uses (27 requests/minute).

    python badges.py              # clubs in the last two seasons (~254)
    python badges.py --all        # every club in model_data.csv (~347)
    python badges.py --missing    # only clubs Turso has no badge for yet
    python badges.py --report     # what is stored, worst matches first
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apis import thesportsdb                       # noqa: E402
from apis.ratelimit import Budget, BudgetedSession, QuotaExhausted   # noqa: E402
from ingest.env import DATA_DIR                    # noqa: E402
from sinks import turso                            # noqa: E402

MODEL_DATA = os.path.join(DATA_DIR, "output", "model_data.csv")
# Deliberately gentler than the documented 30/min. A club can cost two requests
# (the alias fallback), and at the full rate TheSportsDB quietly starts
# returning empty results rather than an error — Chelsea and Celtic both came
# back "not found" on the first run purely from going too fast.
BULK_PER_MINUTE = 12
# Save every N clubs rather than once at the end. The first attempt did all the
# lookups and then lost every one of them to a single Turso read-timeout on the
# final write; a long job should never have one fatal moment at the end.
SAVE_EVERY = 20
RECENT_SEASONS = ("2025/26", "2026/27")
# Below this the match is a guess worth eyeballing rather than trusting.
WEAK_MATCH = 2.0


def clubs(all_seasons: bool, client=None) -> pd.DataFrame:
    """Clubs needing a badge, from BOTH sources that name them.

    model_data.csv is the training history; the Turso `fixtures` table is what
    the dashboard actually renders. They can disagree on a club's spelling —
    editing team_aliases.json changes which spelling wins, and model_data.csv is
    only re-normalised on a rebuild. Reading just one of them leaves clubs on
    the board with no badge, which is exactly how Elversberg and Racing
    Santander slipped through.
    """
    df = pd.read_csv(MODEL_DATA, usecols=["season", "league", "country",
                                          "home_team", "away_team"])
    if not all_seasons:
        df = df[df["season"].isin(RECENT_SEASONS)]
    stacked = pd.concat([
        df[["home_team", "league", "country"]].rename(columns={"home_team": "team"}),
        df[["away_team", "league", "country"]].rename(columns={"away_team": "team"}),
    ], ignore_index=True).dropna(subset=["team"])

    if client is not None:
        try:
            rows = client.rows(
                "SELECT home_team, away_team, league, country FROM fixtures")
            live = pd.concat([
                pd.DataFrame([{"team": r["home_team"], "league": r["league"],
                               "country": r.get("country")} for r in rows]),
                pd.DataFrame([{"team": r["away_team"], "league": r["league"],
                               "country": r.get("country")} for r in rows]),
            ], ignore_index=True).dropna(subset=["team"])
            stacked = pd.concat([stacked, live], ignore_index=True)
        except Exception as exc:                       # noqa: BLE001
            print(f"  (could not read fixtures for club names: {exc})")

    # A club can appear in two divisions (promotion); keep its most recent entry.
    return (stacked.drop_duplicates(subset=["team"], keep="last")
                   .sort_values("team")
                   .reset_index(drop=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="every club, not just recent")
    parser.add_argument("--missing", action="store_true",
                        help="skip clubs Turso already has a badge for")
    parser.add_argument("--report", action="store_true", help="show what is stored, then exit")
    parser.add_argument("--force", action="store_true", help="ignore the response cache")
    args = parser.parse_args(argv)

    if not turso.configured():
        print("Turso is not configured — set TURSO_FIXTURES_URL/_TOKEN in data/.env")
        return 1
    client = turso.Turso()
    turso.ensure_assets_table(client)

    if args.report:
        rows = client.rows(
            f"SELECT team, matched_name, confidence, league, badge_url "
            f"FROM {turso.ASSETS_TABLE} ORDER BY confidence ASC, team ASC")
        tally = turso.badge_count(client)
        print(f"\n{tally['with_badge']} of {tally['total']} clubs have a badge\n")
        weak = [r for r in rows if (r["confidence"] or 0) < WEAK_MATCH]
        if weak:
            print(f"weakest {min(len(weak), 20)} matches — worth an eyeball:")
            for r in weak[:20]:
                print(f"  {r['team']:<28} -> {str(r['matched_name']):<26} "
                      f"conf {r['confidence']}  {'badge' if r['badge_url'] else 'NONE'}")
        else:
            print("no weak matches")
        return 0

    wanted = clubs(args.all, client)
    if args.missing:
        have = {r["team"] for r in client.rows(
            f"SELECT team FROM {turso.ASSETS_TABLE} WHERE badge_url IS NOT NULL")}
        wanted = wanted[~wanted["team"].isin(have)]

    print(f"\n{len(wanted)} clubs to look up "
          f"({'all seasons' if args.all else 'seasons ' + ', '.join(RECENT_SEASONS)})")
    if wanted.empty:
        return 0
    rate = max(1, int(BULK_PER_MINUTE * 0.9))
    print(f"Pacing at {rate} req/min, so allow ~{len(wanted) // rate + 2} min.\n")

    sess = BudgetedSession(Budget(thesportsdb.PROVIDER, per_minute=BULK_PER_MINUTE),
                           thesportsdb.base_url(), verbose=False)
    found, missing, weak, results = 0, [], [], []
    pending: list[dict] = []
    written = 0

    def flush() -> None:
        """Persist what we have. A failed write must not kill the run."""
        nonlocal pending, written
        if not pending:
            return
        try:
            turso.save_badges(pending, client)
            written += len(pending)
            pending = []
        except turso.TursoError as exc:
            print(f"  ! save failed ({exc}); will retry with the next batch")
    for i, row in enumerate(wanted.itertuples(index=False), start=1):
        try:
            url, matched, confidence = thesportsdb.find_badge(
                sess, row.team, row.league, row.country, force=args.force)
        except QuotaExhausted as exc:
            print(f"\n  stopped: {exc}")
            break
        record = {"team": row.team, "badge_url": url, "matched_name": matched,
                  "confidence": confidence, "league": row.league,
                  "source": "TheSportsDB"}
        results.append(record)
        pending.append(record)
        if len(pending) >= SAVE_EVERY:
            flush()
        if url:
            found += 1
            if confidence < WEAK_MATCH:
                weak.append((row.team, matched, confidence))
        else:
            missing.append(row.team)
        if i % 25 == 0 or i == len(wanted):
            print(f"  {i}/{len(wanted)} · {found} badges · {len(missing)} not found")

    flush()
    tally = turso.badge_count(client)
    print(f"\nupserted {written} rows -> Turso `{turso.ASSETS_TABLE}`")
    print(f"  {tally['with_badge']}/{tally['total']} clubs now have a badge")
    if weak:
        print(f"\n  {len(weak)} low-confidence match(es) — check with `--report`:")
        for team, matched, confidence in weak[:8]:
            print(f"    {team:<28} -> {matched} ({confidence})")
    if missing:
        print(f"\n  {len(missing)} not found (the app falls back to initials): "
              f"{', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
