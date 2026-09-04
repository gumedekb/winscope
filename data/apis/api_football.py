"""Collector: API-Football v3 (https://v3.football.api-sports.io) — KEYED.

Free tier = 100 requests/day, 10/minute. We cap at 90/day and 9/minute
(apis/ratelimit.py) because blowing through a free tier is how accounts get
banned. Everything is cached on disk, so re-runs inside the TTL cost 0 requests.

Free-plan boundaries discovered against the live account (they are enforced
server-side and come back as errors.plan, not as HTTP errors):
  * `fixtures?season=` only works for seasons 2022-2024.
  * `fixtures?date=` only works for today-1 .. today+1.
  * `next` / `last` parameters are refused entirely.
  * `fixtures?live=all` DOES work — that is our live feed, and it is 1 request
    for every league on earth, which we then filter down to ours.

So this collector does exactly two jobs, which is all the README asks of it:
  1. Betway Premiership (SA PSL, league 288) history — the league the local dump
     has zero rows for.
  2. Live + next-day fixtures for every league we track.
"""
import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from apis import cache
from apis.ratelimit import Budget, BudgetedSession, QuotaExhausted
from ingest import schema
from ingest.clock import utc_today
from ingest.env import get
from ingest.seasons import season_label
from leagues import BY_AF_ID, SA_LEAGUE

PROVIDER = "api-football"
SOURCE = "API-Football"
BASE_URL = "https://v3.football.api-sports.io"

FREE_PER_DAY = 100
FREE_PER_MINUTE = 10

FINISHED = {"FT", "AET", "PEN"}
IN_PLAY = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
SCHEDULED = {"TBD", "NS"}

_PLAN_RANGE = re.compile(r"from\s+(\d{4})\s+to\s+(\d{4})")


def available() -> bool:
    return bool(get("API_SPORTS_KEY"))


def session(verbose: bool = True) -> BudgetedSession:
    key = get("API_SPORTS_KEY")
    if not key:
        raise RuntimeError("API_SPORTS_KEY missing — add it to data/.env")
    budget = Budget(PROVIDER,
                    per_day=int(get("API_SPORTS_DAILY_LIMIT", str(FREE_PER_DAY))),
                    per_minute=FREE_PER_MINUTE)
    return BudgetedSession(budget, BASE_URL, {"x-apisports-key": key}, verbose=verbose)


# ---------------------------------------------------------------- helpers
def _plan_error(payload) -> str | None:
    errors = (payload or {}).get("errors")
    if isinstance(errors, dict):
        return errors.get("plan") or errors.get("requests") or errors.get("token")
    return None


def sync_quota(sess: BudgetedSession) -> dict:
    """Ask the account how many requests it has really used today, and adopt it.

    Costs 1 request but makes the daily counter exact even if the key was used
    elsewhere (another machine, the web dashboard, a previous crashed run).
    """
    payload = sess.get_json("status")
    reqs = ((payload or {}).get("response") or {}).get("requests") or {}
    if "current" in reqs:
        sess.store.sync_day_used(PROVIDER, int(reqs["current"]))
        sess.store.save()
    return {"plan": (((payload or {}).get("response") or {})
                     .get("subscription") or {}).get("plan"),
            "used": reqs.get("current"), "limit": reqs.get("limit_day")}


def _rows(fixtures: list) -> pd.DataFrame:
    """API-Football fixture objects -> a flat frame both outputs are built from."""
    recs = []
    for item in fixtures or []:
        fx, lg, teams, goals = (item.get("fixture") or {}, item.get("league") or {},
                                item.get("teams") or {}, item.get("goals") or {})
        status = ((fx.get("status") or {}).get("short") or "").upper()
        kickoff = pd.to_datetime(fx.get("date"), errors="coerce", utc=True)
        known = BY_AF_ID.get(lg.get("id"))
        recs.append({
            "kickoff_utc": kickoff,
            "status": status,
            "minute": (fx.get("status") or {}).get("elapsed"),
            "league": known.name if known else lg.get("name"),
            "country": known.country if known else lg.get("country"),
            "af_league_id": lg.get("id"),
            "season": season_label(kickoff) if pd.notna(kickoff) else None,
            "home_team": (teams.get("home") or {}).get("name"),
            "away_team": (teams.get("away") or {}).get("name"),
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "venue": (fx.get("venue") or {}).get("name"),
            "source": SOURCE,
            "source_match_id": fx.get("id"),
        })
    return pd.DataFrame(recs)


def _tracked_only(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["af_league_id"].isin(BY_AF_ID.keys())].copy()


def to_matches(df: pd.DataFrame) -> pd.DataFrame:
    """Finished fixtures -> canonical model_data rows."""
    if df.empty:
        return schema.empty()
    done = df[df["status"].isin(FINISHED)].copy()
    if done.empty:
        return schema.empty()
    done["date"] = pd.to_datetime(done["kickoff_utc"], utc=True).dt.tz_localize(None).dt.normalize()
    done["outcome"] = [schema.result_to_outcome(h, a)
                       for h, a in zip(done["home_score"], done["away_score"])]
    return schema.conform(done.dropna(subset=["outcome"]))


def to_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    """Live + scheduled fixtures -> the fixtures.csv shape."""
    if df.empty:
        return pd.DataFrame(columns=schema.FIXTURE_COLUMNS)
    out = df.copy()
    out["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for col in schema.FIXTURE_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[schema.FIXTURE_COLUMNS]


# ---------------------------------------------------------------- collectors
def sa_history(sess: BudgetedSession, seasons=None, force: bool = False):
    """Betway Premiership results, season by season. -> (DataFrame, notes[])

    The free plan window is 2022-2024; if the account is upgraded later this just
    starts working for more seasons with no code change — we read the allowed
    range straight out of the plan error and report it.
    """
    seasons = seasons or [2022, 2023, 2024]
    frames, notes = [], []
    for season in seasons:
        key = f"fixtures_league{SA_LEAGUE.af_id}_season{season}"
        try:
            payload, cached = cache.fetch(
                sess, PROVIDER, key, cache.TTL_SEASON, "fixtures",
                {"league": SA_LEAGUE.af_id, "season": season}, force=force)
        except QuotaExhausted as exc:
            notes.append(f"SA PSL {season}: {exc}")
            break
        except RuntimeError as exc:
            notes.append(f"SA PSL {season}: {exc}")
            continue

        plan = _plan_error(payload)
        if plan:
            hit = _PLAN_RANGE.search(plan)
            window = f" (plan allows {hit.group(1)}-{hit.group(2)})" if hit else ""
            notes.append(f"SA PSL {season}: refused by plan{window}")
            continue
        rows = _rows(payload.get("response") or [])
        matches = to_matches(rows)
        if not matches.empty:
            matches["league"] = SA_LEAGUE.name
            matches["country"] = SA_LEAGUE.country
            frames.append(matches)
        notes.append(f"SA PSL {season}: {len(matches)} finished matches"
                     + (" (cache)" if cached else ""))
    combined = pd.concat(frames, ignore_index=True) if frames else schema.empty()
    return combined, notes


def live(sess: BudgetedSession, all_leagues: bool = False, force: bool = False):
    """Everything kicking about right now — ONE request for the whole world."""
    try:
        payload, cached = cache.fetch(sess, PROVIDER, "fixtures_live_all",
                                      cache.TTL_LIVE, "fixtures", {"live": "all"},
                                      force=force)
    except (QuotaExhausted, RuntimeError) as exc:
        return pd.DataFrame(), [f"live: {exc}"]
    plan = _plan_error(payload)
    if plan:
        return pd.DataFrame(), [f"live: refused by plan — {plan}"]
    rows = _rows(payload.get("response") or [])
    total = len(rows)
    if not all_leagues:
        rows = _tracked_only(rows)
    return rows, [f"live: {len(rows)} in our leagues of {total} worldwide"
                  + (" (cache)" if cached else "")]


def fixtures_on(sess: BudgetedSession, day: date, all_leagues: bool = False,
                force: bool = False):
    """Fixtures for one calendar day. Free plan only serves today-1 .. today+1."""
    iso = day.isoformat()
    try:
        payload, cached = cache.fetch(sess, PROVIDER, f"fixtures_date_{iso}",
                                      cache.ttl_for_date(day), "fixtures", {"date": iso},
                                      force=force)
    except (QuotaExhausted, RuntimeError) as exc:
        return pd.DataFrame(), [f"{iso}: {exc}"]
    plan = _plan_error(payload)
    if plan:
        return pd.DataFrame(), [f"{iso}: refused by plan — {plan}"]
    rows = _rows(payload.get("response") or [])
    total = len(rows)
    if not all_leagues:
        rows = _tracked_only(rows)
    return rows, [f"{iso}: {len(rows)} fixtures in our leagues of {total}"
                  + (" (cache)" if cached else "")]


def upcoming_window(sess: BudgetedSession, days: int = 1, all_leagues: bool = False,
                    force: bool = False):
    """The whole window the free plan serves: yesterday, today and tomorrow, UTC.

    Yesterday matters as much as tomorrow. A match that kicked off at 19:45 and
    finished after the last run only shows its final score on its own date — and
    once it is over it disappears from `live=all`. Without the backward day, an
    evening fixture can stay stuck at half-time forever.
    """
    frames, notes = [], []
    today = utc_today()
    for offset in range(-1, days + 1):
        rows, note = fixtures_on(sess, today + timedelta(days=offset),
                                 all_leagues=all_leagues, force=force)
        notes += note
        if not rows.empty:
            frames.append(rows)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), notes
