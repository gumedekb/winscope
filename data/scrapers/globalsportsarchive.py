"""Scraper: globalsportsarchive.com — South African top flight, full history.

The one league football-data.co.uk has never carried. robots.txt here is
`Allow: /`, so nothing is off-limits.

Two routes, because the obvious one is only half useful:

  * a SEASON page renders only the latest round — the rest of the rounds are
    pulled in by JavaScript the server will not serve directly (every `?round=`
    variant returns the same round), so historical seasons are not reachable
    this way;
  * a TEAM's `/matches` page lists that club's fixtures across the current and
    neighbouring seasons, scores included. Iterating the league's clubs and
    de-duplicating on match id therefore recovers whole recent seasons.

It carries no betting odds — see scrapers/betexplorer.py for those.
"""
import re
from html import unescape

import pandas as pd

from ingest import schema
from ingest.seasons import label_to_start_year, season_label
from leagues import SA_LEAGUE
from scrapers.base import PoliteFetcher

SOURCE = "globalsportsarchive"
BASE = "https://globalsportsarchive.com"

# season label -> the competition page for it. The league changed sponsor twice,
# so the slug changes even though it is the same competition throughout.
SA_SEASONS: dict[str, str] = {
    "2008/09": "/en/soccer/competition/absa-premiership-2008-2009/1165",
    "2009/10": "/en/soccer/competition/absa-premiership-2009-2010/1164",
    "2010/11": "/en/soccer/competition/absa-premiership-2010-2011/1163",
    "2011/12": "/en/soccer/competition/absa-premiership-2011-2012/1162",
    "2012/13": "/en/soccer/competition/absa-premiership-2012-2013/2408",
    "2013/14": "/en/soccer/competition/absa-premiership-2013-2014/2587",
    "2014/15": "/en/soccer/competition/absa-premiership-2014-2015/4218",
    "2015/16": "/en/soccer/competition/absa-premiership-2015-2016/9218",
    "2016/17": "/en/soccer/competition/absa-premiership-2016-2017/12156",
    "2017/18": "/en/soccer/competition/absa-premiership-2017-2018/13878",
    "2018/19": "/en/soccer/competition/absa-premiership-2018-2019/17830",
    "2019/20": "/en/soccer/competition/absa-premiership-2019-2020/22293",
    "2020/21": "/en/soccer/competition/dstv-premiership-2020-2021/46429",
    "2021/22": "/en/soccer/competition/dstv-premiership-2021-2022/52711",
    "2022/23": "/en/soccer/competition/dstv-premiership-2022-2023/66428",
    "2023/24": "/en/soccer/competition/dstv-premiership-2023-2024/69475",
    "2024/25": "/en/soccer/competition/betway-premiership-2024-2025/73494",
    "2025/26": "/en/soccer/competition/betway-premiership-2025-2026/77184",
    "2026/27": "/en/soccer/competition/betway-premiership-2026-2027/80296",
}

# Each match is one <a> to /en/soccer/match/<date>/<home>-vs-<away>/<id>.
_MATCH_ANCHOR = re.compile(
    r'<a\s+href="https://globalsportsarchive\.com/en/soccer/match/'
    r'(?P<date>\d{4}-\d{2}-\d{2})/(?P<slug>[^/"]+)/(?P<id>\d+)"'
    r'(?P<body>.*?)</a>',
    re.DOTALL,
)
# The long-form club name; the short one sits in a sibling div we ignore.
_FULL_NAME = re.compile(r'<div class="gsa-d-sm-md-none gsa-d-block">\s*(.*?)\s*</div>', re.DOTALL)
_SCORE_BLOCK = re.compile(
    r'gsa-match-rounds-v2__match-score.*?'
    r'<div class="gsa-text-yellow">\s*(\d+)\s*</div>.*?'
    r'<div class="gsa-text-grey">\s*(\d+)\s*</div>',
    re.DOTALL,
)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", name))).strip()


def parse_season(html: str, season: str | None) -> pd.DataFrame:
    """A page of match anchors -> canonical rows. Unplayed fixtures are skipped.

    `season=None` derives the season from each match's own date, which is what a
    team page needs since it spans more than one.
    """
    seen: set[str] = set()
    records = []
    for m in _MATCH_ANCHOR.finditer(html):
        match_id = m.group("id")
        if match_id in seen:            # the page renders a mobile copy as well
            continue
        body = m.group("body")

        score = _SCORE_BLOCK.search(body)
        if not score:                   # not played yet
            continue
        names = [_clean(n) for n in _FULL_NAME.findall(body)]
        names = [n for n in names if n]
        if len(names) < 2:
            continue

        seen.add(match_id)
        home_goals, away_goals = int(score.group(1)), int(score.group(2))
        match_date = pd.to_datetime(m.group("date"), errors="coerce")
        records.append({
            "date": match_date,
            "season": season or season_label(match_date),
            "league": SA_LEAGUE.name,
            "country": SA_LEAGUE.country,
            "home_team": names[0],
            "away_team": names[1],
            "home_score": home_goals,
            "away_score": away_goals,
            "outcome": schema.result_to_outcome(home_goals, away_goals),
            "source": SOURCE,
        })

    if not records:
        return schema.empty()
    df = pd.DataFrame(records).dropna(subset=["date", "outcome"])
    return schema.conform(df)


_TEAM_LINK = re.compile(
    r'href="(https://globalsportsarchive\.com/en/soccer/team/[a-z0-9-]+/\d+)/?"')


def league_team_urls(html: str) -> list[str]:
    """The clubs linked from a season page, de-duplicated and trailing-slash-free."""
    return sorted({m.rstrip("/") for m in _TEAM_LINK.findall(html)})


def collect_via_teams(season_path: str, fetcher: PoliteFetcher | None = None,
                      force: bool = False, verbose: bool = True):
    """Every match GSA lists for a league's clubs, via their /matches pages.

    One request per club instead of one per round, and it reaches seasons the
    season page itself will not render. Matches appear twice (once per club), so
    they are de-duplicated on (date, home, away).
    """
    fetcher = fetcher or PoliteFetcher(verbose=verbose)
    notes = []
    try:
        season_html = fetcher.get(BASE + season_path, force=force)
    except Exception as exc:
        return schema.empty(), [f"season page: {exc}"]

    teams = league_team_urls(season_html)
    notes.append(f"{len(teams)} clubs to walk")
    frames = []
    for url in teams:
        try:
            html = fetcher.get(url + "/matches", force=force)
        except Exception as exc:
            notes.append(f"{url.rsplit('/', 2)[-2]}: {exc}")
            continue
        rows = parse_season(html, season=None)
        if not rows.empty:
            frames.append(rows)

    if not frames:
        return schema.empty(), notes
    df = pd.concat(frames, ignore_index=True)
    before = len(df)
    df = df.drop_duplicates(subset=["date", "home_team", "away_team"]).reset_index(drop=True)
    notes.append(f"{before} rows across clubs -> {len(df)} distinct matches")
    return schema.conform(df), notes


def collect(seasons=None, fetcher: PoliteFetcher | None = None, force: bool = False,
            verbose: bool = True):
    """Scrape the requested seasons' pages. Only the latest round of each is
    served, so this is useful for topping up the current season, not history."""
    fetcher = fetcher or PoliteFetcher(verbose=verbose)
    seasons = seasons or sorted(SA_SEASONS, key=label_to_start_year)
    frames, notes = [], []
    for season in seasons:
        path = SA_SEASONS.get(season)
        if not path:
            notes.append(f"{season}: no page known")
            continue
        try:
            html = fetcher.get(BASE + path, force=force)
        except Exception as exc:
            notes.append(f"{season}: fetch failed — {exc}")
            continue
        rows = parse_season(html, season)
        if not rows.empty:
            frames.append(rows)
        notes.append(f"{season}: {len(rows)} played matches")
    combined = pd.concat(frames, ignore_index=True) if frames else schema.empty()
    return combined, notes
