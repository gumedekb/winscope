"""Spec output 2: the coverage report printed to the console.

Per league: country, match count, season range — then assert the target window
2018/19 -> 2026/27 and flag anything missing or thin.
"""
import pandas as pd

from ingest.seasons import label_to_fduk_code, label_to_start_year
from leagues import BY_NAME, REGISTRY, TARGET_SEASONS

FDUK_URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"

# Below this many matches a *finished* season is treated as suspicious, not just
# small. The in-progress current season is exempted (it is meant to be partial).
THIN_SEASON_ROWS = 100

# Bookmaker odds are a model feature, so a season that has rows but no odds is a
# quiet hole: the coverage table looks full while the model trains blind on it.
MIN_ODDS_COVERAGE = 0.5


def _fmt(n: int) -> str:
    return f"{n:,}"


def coverage(df: pd.DataFrame, current_season: str | None = None) -> dict:
    """-> {'rows': [...per league...], 'gaps': [...], 'thin': [...]}"""
    rows, gaps, thin = [], [], []
    by_league = {name: grp for name, grp in df.groupby("league")} if not df.empty else {}

    for lg in REGISTRY:
        grp = by_league.get(lg.name)
        if grp is None or grp.empty:
            rows.append({"league": lg.name, "country": lg.country, "matches": 0,
                         "seasons": "—", "n_seasons": 0})
            gaps.append((lg.name, "no matches at all"))
            continue
        seasons = sorted(grp["season"].dropna().unique(), key=label_to_start_year)
        rows.append({
            "league": lg.name,
            "country": lg.country,
            "matches": len(grp),
            "seasons": f"{seasons[0]}–{seasons[-1]}" if seasons else "—",
            "n_seasons": len(seasons),
        })
        missing = [s for s in TARGET_SEASONS if s not in seasons]
        if missing:
            gaps.append((lg.name, "missing " + ", ".join(missing)))
        counts = grp.groupby("season").size()
        for season, n in counts.items():
            if n < THIN_SEASON_ROWS and season != current_season:
                thin.append((lg.name, season, int(n)))
    # League-seasons with no match statistics. Same idea as the odds check: the
    # coverage table looks full while those columns are entirely blank.
    statless = []
    if not df.empty and "home_shots" in df.columns:
        by_stats = df.groupby(["league", "season"]).agg(
            n=("date", "size"), stats=("home_shots", lambda s: s.notna().mean()))
        for (league, season), row in by_stats.iterrows():
            if row["stats"] < MIN_ODDS_COVERAGE:
                statless.append((league, season, int(row["n"]), float(row["stats"])))

    # League-seasons that are present but carry no bookmaker odds.
    oddsless = []
    if not df.empty and "odds_home" in df.columns:
        by_season = df.groupby(["league", "season"]).agg(
            n=("date", "size"), odds=("odds_home", lambda s: s.notna().mean()))
        for (league, season), row in by_season.iterrows():
            if row["odds"] < MIN_ODDS_COVERAGE:
                oddsless.append((league, season, int(row["n"]), float(row["odds"])))

    return {"rows": rows, "gaps": gaps, "thin": sorted(thin),
            "oddsless": sorted(oddsless), "statless": sorted(statless)}


def print_coverage(df: pd.DataFrame, current_season: str | None = None) -> dict:
    rep = coverage(df, current_season)
    width = max([len(r["league"]) for r in rep["rows"]] + [6])
    print(f"\n{'League'.ljust(width)}  {'Country'.ljust(13)} {'Matches':>8}  Seasons")
    print("-" * (width + 40))
    for r in rep["rows"]:
        print(f"{r['league'].ljust(width)}  {r['country'].ljust(13)} "
              f"{_fmt(r['matches']):>8}  {r['seasons']} ({r['n_seasons']})")
    print("-" * (width + 40))
    print(f"{'TOTAL'.ljust(width)}  {''.ljust(13)} {_fmt(len(df)):>8}")

    print(f"\nTarget window: {TARGET_SEASONS[0]} -> {TARGET_SEASONS[-1]}")
    if rep["gaps"]:
        print("  ! gaps:")
        for league, why in rep["gaps"]:
            print(f"      - {league}: {why}")
            for hint in fix_hints(league, why):
                print(f"          {hint}")
    else:
        print("  ✓ every league covers the full window")
    if rep["thin"]:
        print(f"  ! thin seasons (<{THIN_SEASON_ROWS} matches, current season excluded):")
        for league, season, n in rep["thin"]:
            print(f"      - {league} {season}: {n} matches")
    if rep["oddsless"]:
        total = sum(n for _l, _s, n, _c in rep["oddsless"])
        print(f"  ! {_fmt(total)} rows have no bookmaker odds — the model uses odds as a "
              f"feature, so these train blind:")
        # A league with no odds anywhere gets one line, not one per season —
        # otherwise the SA PSL alone prints eight identical warnings.
        grouped: dict[str, list] = {}
        for league, season, n, _ratio in rep["oddsless"]:
            grouped.setdefault(league, []).append((season, n))
        for league, entries in grouped.items():
            lg = BY_NAME.get(league)
            rows_total = sum(n for _s, n in entries)
            if lg is not None and lg.fduk_div is None:
                seasons = ", ".join(s for s, _n in entries)
                print(f"      - {league}: {_fmt(rows_total)} rows across {seasons}")
                print("          -> no free source carries odds for this league")
                continue
            for season, n in entries:
                print(f"      - {league} {season}: {_fmt(n)} rows")
                for hint in odds_hints(league, season):
                    print(f"          {hint}")

    if rep["statless"]:
        total = sum(n for _l, _s, n, _c in rep["statless"])
        by_league: dict[str, int] = {}
        for league, _season, n, _c in rep["statless"]:
            by_league[league] = by_league.get(league, 0) + n
        print(f"  ! {_fmt(total)} rows have no shot/corner/card stats:")
        for league, n in sorted(by_league.items(), key=lambda kv: -kv[1]):
            print(f"      - {league}: {_fmt(n)} rows")

    if current_season:
        played = len(df[df["season"] == current_season]) if not df.empty else 0
        print(f"  i {current_season} is in progress — {_fmt(played)} matches so far "
              f"(partial is expected, not an error)")
    return rep


def fix_hints(league_name: str, why: str) -> list[str]:
    """Turn a gap into an actionable instruction rather than just a complaint."""
    lg = BY_NAME.get(league_name)
    if lg is None:
        return []
    if lg.fduk_div is None:
        return ["-> API-only league: run  python pipeline.py --with-api"]
    if not why.startswith("missing "):
        return []
    hints = []
    for season in why[len("missing "):].split(", "):
        url = FDUK_URL.format(code=label_to_fduk_code(season), div=lg.fduk_div)
        hints.append(f"-> drop {season} into unsorted/: {url}")
    hints.append("-> or fill it for free: python pipeline.py --openfootball"
                 if lg.of_code else
                 "-> or fill it from the APIs: python pipeline.py --with-api")
    return hints


def odds_hints(league_name: str, season: str) -> list[str]:
    """A season with rows but no odds is usually an openfootball or API gap-fill.
    The football-data.co.uk file for the same season has Bet365 odds."""
    lg = BY_NAME.get(league_name)
    if lg is None or lg.fduk_div is None:
        return ["-> no free source carries odds for this league"]
    url = FDUK_URL.format(code=label_to_fduk_code(season), div=lg.fduk_div)
    return [f"-> replace with the odds-carrying file: {url}"]


def print_duplicate_seasons(per_file: dict) -> None:
    """`per_file` = {filename: season}. Two files on one season means a season
    got downloaded twice — and another one never got downloaded at all."""
    by_season = {}
    for name, season in per_file.items():
        by_season.setdefault(season, []).append(name)
    dupes = {s: names for s, names in by_season.items() if len(names) > 1}
    if not dupes:
        return
    print("\n  ! the same season was downloaded more than once "
          "(harmless, but usually means another season is missing):")
    for season, names in sorted(dupes.items()):
        print(f"      - {season}: {', '.join(sorted(names))}")


def print_diff(d, conflicts_path: str | None = None) -> None:
    print(f"\nChange report vs previous model_data.csv")
    print(f"  added      {_fmt(d.added)}")
    print(f"  unchanged  {_fmt(d.unchanged)}")
    print(f"  conflicts  {_fmt(d.conflict_count)}"
          + ("  (same fixture, different score/outcome)" if d.conflict_count else ""))
    if d.conflict_count:
        head = d.conflicts.head(5)
        for _, row in head.iterrows():
            print(f"      - {row['_k']}: "
                  f"{row['home_score_old']}-{row['away_score_old']} ({row['source_old']})"
                  f" vs {row['home_score']}-{row['away_score']} ({row['source']})")
        if conflicts_path:
            print(f"      -> full list written to {conflicts_path}")
