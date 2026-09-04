"""Collector: openfootball (https://openfootball.github.io/) — FREE, no key.

Plain JSON season files served from GitHub, so there is no account to ban and no
quota to blow — we still pace it (54/min) out of politeness. Coverage checked
against the live repo: en.1, en.2, en.3, de.1, es.1, it.1, fr.1, nl.1, pt.1.
No Scotland, no South Africa.

Role here: a free second opinion — NOT a bulk source. Two measured findings from
running it against the dump:
  * some COVID-restart fixtures in the 2019-20 files carry the season's start
    year instead of the real one (Barnsley v Nottingham Forest dated 2019-07-19,
    actually played 2020-07-19), which manufactures phantom duplicates;
  * scores for the in-progress season disagree with football-data.co.uk on
    dozens of matches.
So it sits at the bottom of SOURCE_PRIORITY and is used in one of two narrow,
opt-in modes rather than merged wholesale:
  --openfootball   fill ONLY the league-seasons the dataset is actually missing
  --cross-check    fetch and DIFF against what we have, merging nothing
"""
import pandas as pd

from apis import cache
from apis.ratelimit import Budget, BudgetedSession, QuotaExhausted
from ingest import schema
from ingest.seasons import label_to_start_year
from leagues import REGISTRY

PROVIDER = "openfootball"
SOURCE = "openfootball"
BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
POLITE_PER_MINUTE = 60


def available() -> bool:
    return True                                  # no key required


def session(verbose: bool = True) -> BudgetedSession:
    return BudgetedSession(Budget(PROVIDER, per_minute=POLITE_PER_MINUTE),
                           BASE_URL, verbose=verbose)


def of_season(label: str) -> str:
    """'2024/25' -> '2024-25' (openfootball's directory naming)."""
    y = label_to_start_year(label)
    return f"{y}-{(y + 1) % 100:02d}"


def _matches(payload: dict) -> list:
    """Flatten either layout: {"matches": [...]} or {"rounds": [{"matches": [...]}]}."""
    payload = payload or {}
    if isinstance(payload.get("matches"), list):
        return payload["matches"]
    out = []
    for rnd in payload.get("rounds") or []:
        out.extend((rnd or {}).get("matches") or [])
    return out


def _team(value):
    """team1 is a plain string in newer files, an object in older ones."""
    if isinstance(value, dict):
        return value.get("name") or value.get("key") or value.get("code")
    return value


def _score(m: dict):
    """Goals, across every shape openfootball has used. -> (home, away)."""
    score = m.get("score")
    if isinstance(score, dict):
        ft = score.get("ft")
        if isinstance(ft, (list, tuple)) and len(ft) == 2:
            return ft[0], ft[1]
        if isinstance(ft, dict):
            return ft.get("1"), ft.get("2")
        if "score1" in score and "score2" in score:
            return score.get("score1"), score.get("score2")
    elif isinstance(score, (list, tuple)) and len(score) == 2:
        return score[0], score[1]
    if "score1" in m and "score2" in m:          # oldest layout, goals on the match
        return m.get("score1"), m.get("score2")
    return None, None


def _rows(payload: dict, league) -> pd.DataFrame:
    recs = []
    for m in _matches(payload):
        if not isinstance(m, dict):
            continue
        home, away = _score(m)
        recs.append({
            "date": pd.to_datetime(m.get("date"), errors="coerce"),
            "league": league.name,
            "country": league.country,
            "home_team": _team(m.get("team1")),
            "away_team": _team(m.get("team2")),
            "home_score": home,
            "away_score": away,
            "source": SOURCE,
        })
    df = pd.DataFrame(recs)
    if df.empty:
        return schema.empty()
    df = df.dropna(subset=["date", "home_team", "away_team", "home_score", "away_score"])
    if df.empty:
        return schema.empty()
    df["outcome"] = [schema.result_to_outcome(h, a)
                     for h, a in zip(df["home_score"], df["away_score"])]
    from ingest.seasons import resolve_file_season
    df["season"] = resolve_file_season(df["date"])
    return schema.conform(df)


MIN_SEASON_ROWS = 100        # fewer than this and we do not really "have" a season


def missing_pairs(have: pd.DataFrame, seasons: list[str], leagues) -> set:
    """Which (league, season) pairs the dataset is genuinely missing."""
    present = set()
    if have is not None and not have.empty:
        counts = have.groupby(["league", "season"]).size()
        present = {pair for pair, n in counts.items() if n >= MIN_SEASON_ROWS}
    return {(lg.name, season) for lg in leagues for season in seasons
            if (lg.name, season) not in present}


def collect(sess: BudgetedSession, seasons: list[str], leagues=None,
            force: bool = False, only: set | None = None):
    """Pull `seasons` (canonical '2024/25' labels) for the covered leagues.

    `only` restricts the pull to a set of (league_name, season) pairs — that is
    what gap-fill mode passes in, so a run costs a handful of requests instead of
    ninety and cannot overwrite good data with openfootball's known-bad rows.
    """
    leagues = leagues if leagues is not None else [lg for lg in REGISTRY if lg.of_code]
    frames, notes = [], []
    for lg in leagues:
        for label in seasons:
            if only is not None and (lg.name, label) not in only:
                continue
            path = f"{of_season(label)}/{lg.of_code}.json"
            try:
                payload, cached = cache.fetch(sess, PROVIDER, path.replace("/", "_"),
                                              cache.TTL_SEASON, path, force=force)
            except QuotaExhausted as exc:
                notes.append(f"{lg.name} {label}: {exc}")
                return (pd.concat(frames, ignore_index=True) if frames
                        else schema.empty()), notes
            except RuntimeError:
                continue                          # 404 = that season is not published
            rows = _rows(payload, lg)
            if not rows.empty:
                frames.append(rows)
                notes.append(f"{lg.name} {label}: {len(rows)} matches"
                             + (" (cache)" if cached else ""))
    return (pd.concat(frames, ignore_index=True) if frames else schema.empty()), notes
