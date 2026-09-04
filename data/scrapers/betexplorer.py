"""Scraper: betexplorer.com — South African top flight, current season, WITH ODDS.

Why only the current season: BetExplorer's robots.txt disallows `/*?year=`,
`/*?month=` and `/*?stage=`, and its season archives live behind `?stage=`. The
plain `/results/` path is not disallowed, so that is all this touches. The
fetcher enforces it — a disallowed URL raises rather than being fetched — so this
cannot quietly start scraping the archives later.

The payoff is real though: this is the only free source of BOOKMAKER ODDS for the
SA league. API-Football and TheSportsDB both give results with none, and odds are
a model feature.
"""
import re
from datetime import datetime
from html import unescape

import pandas as pd

from ingest import schema
from ingest.seasons import season_label
from leagues import SA_LEAGUE
from scrapers.base import PoliteFetcher

SOURCE = "betexplorer"
RESULTS_URL = "https://www.betexplorer.com/football/south-africa/betway-premiership/results/"

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
_MATCH_LINK = re.compile(r'<a[^>]*class="in-match"[^>]*>(.*?)</a>', re.DOTALL)
_SCORE = re.compile(r">(\d+):(\d+)<")
_ODD = re.compile(r'data-odd="([0-9.]+)"')
# BetExplorer writes "dd.mm.yyyy", or "dd.mm." for the current year, or "Today".
_DATE = re.compile(r">(\d{1,2}\.\d{1,2}\.(?:\d{4})?)<")


def _teams(link_html: str) -> tuple[str, str] | None:
    """The anchor nests the clubs in <span>/<strong>, so strip tags then split."""
    text = unescape(re.sub(r"<[^>]+>", "", link_html))
    text = re.sub(r"\s+", " ", text).strip()
    parts = text.split(" - ")
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()
    return (home, away) if home and away else None


def _parse_date(row_html: str, today: datetime) -> pd.Timestamp | None:
    m = _DATE.search(row_html)
    if not m:
        # "Today" / "Yesterday" columns carry no date; the results page is
        # ordered by round, so an undated row is one of the most recent.
        return pd.Timestamp(today.date())
    raw = m.group(1).rstrip(".")
    parts = raw.split(".")
    try:
        day, month = int(parts[0]), int(parts[1])
        year = int(parts[2]) if len(parts) > 2 and parts[2] else None
    except (ValueError, IndexError):
        return None
    if year is None:
        # No year given means the current season; pick the year that puts the
        # date in the past rather than months in the future.
        year = today.year
        candidate = pd.Timestamp(year=year, month=month, day=day)
        if candidate > pd.Timestamp(today) + pd.Timedelta(days=30):
            year -= 1
    try:
        return pd.Timestamp(year=year, month=month, day=day)
    except ValueError:
        return None


def parse_results(html: str, now: datetime | None = None) -> pd.DataFrame:
    """The results table -> canonical rows, odds included."""
    now = now or datetime.utcnow()
    records = []
    for row in _ROW.findall(html):
        link = _MATCH_LINK.search(row)
        if not link:
            continue
        teams = _teams(link.group(1))
        score = _SCORE.search(row)
        if not teams or not score:
            continue

        odds = _ODD.findall(row)
        home_goals, away_goals = int(score.group(1)), int(score.group(2))
        date = _parse_date(row, now)
        if date is None:
            continue

        records.append({
            "date": date,
            "season": season_label(date),
            "league": SA_LEAGUE.name,
            "country": SA_LEAGUE.country,
            "home_team": teams[0],
            "away_team": teams[1],
            "home_score": home_goals,
            "away_score": away_goals,
            "outcome": schema.result_to_outcome(home_goals, away_goals),
            # Three odds columns in table order: 1, X, 2. Anything else is a
            # layout we do not recognise, so take none rather than guess.
            "odds_home": float(odds[0]) if len(odds) >= 3 else None,
            "odds_draw": float(odds[1]) if len(odds) >= 3 else None,
            "odds_away": float(odds[2]) if len(odds) >= 3 else None,
            "source": SOURCE,
        })

    if not records:
        return schema.empty()
    df = pd.DataFrame(records).dropna(subset=["date", "outcome"])
    df = df.drop_duplicates(subset=["date", "home_team", "away_team"])
    return schema.conform(df)


def collect(fetcher: PoliteFetcher | None = None, force: bool = False,
            verbose: bool = True):
    fetcher = fetcher or PoliteFetcher(verbose=verbose)
    try:
        html = fetcher.get(RESULTS_URL, force=force)
    except Exception as exc:
        return schema.empty(), [f"results page: {exc}"]
    rows = parse_results(html)
    with_odds = int(rows["odds_home"].notna().sum()) if not rows.empty else 0
    return rows, [f"current season: {len(rows)} played matches, {with_odds} with odds"]
