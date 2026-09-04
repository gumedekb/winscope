#!/usr/bin/env python3
"""One-off scrapers -> a canonical CSV in unsorted/.

The South African top flight is the league no free bulk source carries, so it is
assembled by hand from the two sites that publish it and permit scraping:

  betexplorer.com        current season, results + BOOKMAKER ODDS.
                         Its robots.txt disallows `?year=`/`?month=`/`?stage=`,
                         which is where the season archives live, so only the
                         plain /results/ path is touched. The fetcher enforces
                         that — a disallowed URL raises rather than being fetched.
  globalsportsarchive    results, no odds. Season pages render only the latest
                         round (the rest arrives via JavaScript), so the clubs'
                         /matches pages are walked instead, which reaches whole
                         recent seasons.

Output goes to unsorted/ in the canonical schema, so `python pipeline.py` picks
it up like any other file — deduped against everything else by source trust.

    python scrape.py              # both sources
    python scrape.py --betexplorer-only
    python scrape.py --force      # ignore the page cache and refetch
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clean.normalize import normalize                        # noqa: E402
from ingest import schema                                    # noqa: E402
from ingest.env import DATA_DIR                              # noqa: E402
from scrapers import betexplorer, globalsportsarchive        # noqa: E402
from scrapers.base import Disallowed, PoliteFetcher          # noqa: E402

UNSORTED = os.path.join(DATA_DIR, "unsorted")
OUT_NAME = "sa-betway-premiership-scraped.csv"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--betexplorer-only", action="store_true")
    parser.add_argument("--gsa-only", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="refetch instead of using the on-disk page cache")
    parser.add_argument("--out", default=OUT_NAME)
    args = parser.parse_args(argv)

    fetcher = PoliteFetcher()
    frames, notes = [], []

    if not args.gsa_only:
        print("\nbetexplorer.com — current season, with odds")
        try:
            rows, log = betexplorer.collect(fetcher, force=args.force)
            frames.append(rows)
            notes += [f"[betexplorer] {n}" for n in log]
        except Disallowed as exc:
            notes.append(f"[betexplorer] refused by robots.txt: {exc}")
        for n in notes[-3:]:
            print(f"    {n}")

    if not args.betexplorer_only:
        print("\nglobalsportsarchive.com — club match pages, no odds")
        try:
            rows, log = globalsportsarchive.collect_via_teams(
                globalsportsarchive.SA_SEASONS["2026/27"], fetcher, force=args.force)
            frames.append(rows)
            notes += [f"[gsa] {n}" for n in log]
            for n in log:
                print(f"    {n}")
        except Disallowed as exc:
            notes.append(f"[gsa] refused by robots.txt: {exc}")

    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        print("\nNothing scraped — no file written.")
        return 1

    # betexplorer first: same fixtures, but it is the one carrying odds.
    combined = normalize(pd.concat(frames, ignore_index=True))
    os.makedirs(UNSORTED, exist_ok=True)
    out_path = os.path.join(UNSORTED, args.out)
    combined.to_csv(out_path, index=False, date_format="%Y-%m-%d")

    print(f"\nWrote {len(combined)} matches -> unsorted/{args.out}")
    print(f"  seasons: {', '.join(sorted(combined['season'].dropna().unique()))}")
    print(f"  with odds: {int(combined['odds_home'].notna().sum())}"
          f" ({combined['odds_home'].notna().mean():.0%})")
    print(f"  sources: {combined['source'].value_counts().to_dict()}")
    print(f"  pages fetched: {fetcher.fetched} (cache hits: {fetcher.from_cache})")
    print("\nNow run:  python pipeline.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
