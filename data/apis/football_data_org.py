"""Collector: football-data.org v4 (https://www.football-data.org/) — KEYED.

Free tier = 10 requests/minute across 13 competitions (capped to 9/min here).
No published daily cap, so the budget is per-minute only.

Its value in this pipeline: the free tier will serve a *date range* of scheduled
matches, which API-Football's free plan flatly refuses. One request to
/v4/matches?dateFrom=&dateTo= returns upcoming fixtures for every competition the
token can see, so we get a real 7-day lookahead for the big European leagues for
the price of a single call. It has no South African coverage.
"""
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from apis import cache
from apis.ratelimit import Budget, BudgetedSession, QuotaExhausted
from ingest import schema
from ingest.clock import utc_today
from ingest.env import get
from ingest.seasons import season_label
from leagues import BY_FDO_CODE

PROVIDER = "football-data.org"
SOURCE = "football-data.org"
BASE_URL = "https://api.football-data.org/v4"

FREE_PER_MINUTE = 10
# The API rejects any dateFrom..dateTo span wider than this ("Specified period
# must not exceed 10 days."), so a longer window is fetched as several slices.
MAX_WINDOW_DAYS = 10

# football-data.org status -> our vocabulary
FINISHED = {"FINISHED", "AWARDED"}
IN_PLAY = {"IN_PLAY", "PAUSED"}
SCHEDULED = {"SCHEDULED", "TIMED"}
KNOWN = FINISHED | IN_PLAY | SCHEDULED | {"POSTPONED", "CANCELLED", "SUSPENDED"}


def _status(raw, kickoff, score) -> str:
    """Repair the status field.

    The API returns a timestamp in `status` for a slice of its matches (seen on
    Bundesliga / Eredivisie / Liga Portugal rows), so anything outside the known
    vocabulary is re-derived: a full-time score means FINISHED, otherwise the
    kickoff time decides SCHEDULED vs UNKNOWN.
    """
    text = str(raw or "").strip().upper()
    if text in KNOWN:
        return text
    if score.get("home") is not None and score.get("away") is not None:
        return "FINISHED"
    if pd.notna(kickoff) and kickoff > pd.Timestamp.now(tz="UTC"):
        return "SCHEDULED"
    return "UNKNOWN"


def available() -> bool:
    return bool(get("FOOTBALL_DATA_API_KEY"))


def session(verbose: bool = True) -> BudgetedSession:
    key = get("FOOTBALL_DATA_API_KEY")
    if not key:
        raise RuntimeError("FOOTBALL_DATA_API_KEY missing — add it to data/.env")
    daily = get("FOOTBALL_DATA_DAILY_LIMIT", "")
    budget = Budget(PROVIDER, per_day=int(daily) if daily.isdigit() else None,
                    per_minute=FREE_PER_MINUTE)
    return BudgetedSession(budget, BASE_URL, {"X-Auth-Token": key}, verbose=verbose)


def _rows(matches: list) -> pd.DataFrame:
    recs = []
    for m in matches or []:
        comp = m.get("competition") or {}
        known = BY_FDO_CODE.get(comp.get("code"))
        kickoff = pd.to_datetime(m.get("utcDate"), errors="coerce", utc=True)
        score = ((m.get("score") or {}).get("fullTime")) or {}
        recs.append({
            "kickoff_utc": kickoff,
            "status": _status(m.get("status"), kickoff, score),
            "minute": (m.get("minute") if isinstance(m.get("minute"), int) else None),
            "league": known.name if known else comp.get("name"),
            "country": known.country if known else (m.get("area") or {}).get("name"),
            "fdo_code": comp.get("code"),
            "season": season_label(kickoff) if pd.notna(kickoff) else None,
            "home_team": (m.get("homeTeam") or {}).get("name"),
            "away_team": (m.get("awayTeam") or {}).get("name"),
            "home_score": score.get("home"),
            "away_score": score.get("away"),
            "venue": None,
            "source": SOURCE,
            "source_match_id": m.get("id"),
        })
    df = pd.DataFrame(recs)
    return df.dropna(subset=["home_team", "away_team"]) if not df.empty else df


def _tracked_only(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["fdo_code"].isin(BY_FDO_CODE.keys())].copy()


def to_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=schema.FIXTURE_COLUMNS)
    out = df.copy()
    out["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for col in schema.FIXTURE_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[schema.FIXTURE_COLUMNS]


def to_matches(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return schema.empty()
    done = df[df["status"].isin(FINISHED)].copy()
    if done.empty:
        return schema.empty()
    done["date"] = pd.to_datetime(done["kickoff_utc"], utc=True).dt.tz_localize(None).dt.normalize()
    done["outcome"] = [schema.result_to_outcome(h, a)
                       for h, a in zip(done["home_score"], done["away_score"])]
    return schema.conform(done.dropna(subset=["outcome"]))


def _slice(sess, start: date, end: date, all_leagues: bool, force: bool):
    """One request for one <=10-day window, ACROSS all competitions.

    Kept only for `all_leagues`, because it is the sole way to see a competition
    we do not already name. On the free tier it is close to useless on its own —
    see `_competition_slice` for why.
    """
    key = f"matches_{start.isoformat()}_{end.isoformat()}"
    try:
        payload, cached = cache.fetch(
            sess, PROVIDER, key, cache.ttl_for_window(start, end), "matches",
            {"dateFrom": start.isoformat(), "dateTo": end.isoformat()}, force=force)
    except (QuotaExhausted, RuntimeError) as exc:
        return pd.DataFrame(), [f"matches {start}..{end}: {exc}"]
    rows = _rows(payload.get("matches") or [])
    total = len(rows)
    if not all_leagues:
        rows = _tracked_only(rows)
    return rows, [f"matches {start}..{end}: {len(rows)} in our leagues of {total}"
                  + (" (cache)" if cached else "")]


def _competition_slice(sess, code: str, start: date, end: date, force: bool):
    """One request: ONE competition, one <=10-day window.

    WHY PER COMPETITION, when /v4/matches would fetch them all in one request:
    on the free tier (`filters.permission: TIER_ONE`) the cross-competition
    endpoint answers a dated range with almost nothing — a query for 2026-09-06
    returned a single Brazilian fixture while Serie A, Eredivisie and the rest
    played a full card that day. Asking each competition directly returns the
    complete list for exactly the same dates and the same free token.

    That silent near-empty response is what left finished matches frozen at
    `in_play` in Turso: the backfill window was running, finding nothing, and
    reporting success. The extra requests are affordable because this provider
    has no daily cap at all — only 10/minute, which BudgetedSession paces.
    """
    key = f"comp_{code}_{start.isoformat()}_{end.isoformat()}"
    try:
        payload, cached = cache.fetch(
            sess, PROVIDER, key, cache.ttl_for_window(start, end),
            f"competitions/{code}/matches",
            {"dateFrom": start.isoformat(), "dateTo": end.isoformat()}, force=force)
    except (QuotaExhausted, RuntimeError) as exc:
        return pd.DataFrame(), [f"{code} {start}..{end}: {exc}"]
    rows = _rows(payload.get("matches") or [])
    return rows, [f"{code} {start}..{end}: {len(rows)}" + (" (cache)" if cached else "")]


def windows(start: date, end: date, size: int = MAX_WINDOW_DAYS):
    """Split an arbitrary date range into consecutive <=`size`-day windows."""
    out, cursor = [], start
    while cursor <= end:
        stop = min(cursor + timedelta(days=size - 1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


def matches_between(sess: BudgetedSession, start: date, end: date,
                    all_leagues: bool = False, force: bool = False):
    """Every match between two dates, chunked to fit the API's 10-day ceiling.

    Walks our tracked competitions one at a time — the free tier will not serve a
    dated range across all of them at once (see `_competition_slice`). That is
    len(codes) x len(windows) requests, which this provider's budget absorbs:
    no daily cap, 10/minute, paced by BudgetedSession.
    """
    codes = sorted(BY_FDO_CODE)
    frames, notes = [], []
    for win_start, win_end in windows(start, end):
        for code in codes:
            rows, note = _competition_slice(sess, code, win_start, win_end, force)
            notes += note
            if not rows.empty:
                frames.append(rows)
        # Only a cross-competition sweep can surface a league we do not name.
        if all_leagues:
            rows, note = _slice(sess, win_start, win_end, True, force)
            notes += note
            if not rows.empty:
                frames.append(rows)
    if not frames:
        return pd.DataFrame(), notes
    out = pd.concat(frames, ignore_index=True)
    # The two paths can overlap when all_leagues is on.
    out = out.drop_duplicates(subset=["kickoff_utc", "home_team", "away_team"])
    return out.reset_index(drop=True), notes


def upcoming(sess: BudgetedSession, days: int = 7, all_leagues: bool = False,
             force: bool = False):
    """The lookahead API-Football's free plan will not give us."""
    today = utc_today()
    return matches_between(sess, today, today + timedelta(days=days),
                           all_leagues=all_leagues, force=force)
