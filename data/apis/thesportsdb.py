"""Collector: TheSportsDB (https://www.thesportsdb.com/) — free key, no signup.

The free/test key ("3") is generous on rate (30/min, capped to 27 here) but
truncates list responses hard — eventsnextleague returns ~1 event and
eventsseason ~5. That makes it a *gap-filler and cross-check*, not a bulk source.
Set THESPORTSDB_KEY in data/.env to a Patreon key and the same code returns full
lists with no other change.

Its one genuinely unique contribution: it carries the South African Premier
Soccer League (id 4802, "Betway Premiership") including the CURRENT season, which
the API-Football free plan will not serve at all.
"""
from datetime import date, datetime, timezone

import pandas as pd

from apis import cache
from apis.ratelimit import Budget, BudgetedSession, QuotaExhausted
from ingest import schema
from ingest.env import get
from ingest.seasons import current_season_start_year, season_label
from leagues import BY_TSDB_ID, REGISTRY, SA_LEAGUE

PROVIDER = "thesportsdb"
SOURCE = "TheSportsDB"
FREE_PER_MINUTE = 30

FINISHED = {"FT", "AET", "PEN", "MATCH FINISHED"}
IN_PLAY = {"1H", "2H", "HT", "ET", "LIVE"}


def base_url() -> str:
    return f"https://www.thesportsdb.com/api/v1/json/{get('THESPORTSDB_KEY', '3')}"


def available() -> bool:
    return True                                  # the free key needs no signup


def using_free_key() -> bool:
    return get("THESPORTSDB_KEY", "3") in ("3", "1", "123")


def session(verbose: bool = True) -> BudgetedSession:
    budget = Budget(PROVIDER, per_minute=FREE_PER_MINUTE)
    return BudgetedSession(budget, base_url(), verbose=verbose)


def tsdb_season(start_year: int) -> str:
    """2026 -> '2026-2027' (TheSportsDB's season string format)."""
    return f"{start_year}-{start_year + 1}"


def _rows(events: list) -> pd.DataFrame:
    recs = []
    for e in events or []:
        known = BY_TSDB_ID.get(str(e.get("idLeague")))
        stamp = e.get("strTimestamp") or e.get("dateEvent")
        kickoff = pd.to_datetime(stamp, errors="coerce", utc=True)
        status = (e.get("strStatus") or "").strip().upper()
        if not status:
            status = "FT" if e.get("intHomeScore") not in (None, "") else "NS"
        recs.append({
            "kickoff_utc": kickoff,
            "status": status,
            "minute": (e.get("strProgress") or None),
            "league": known.name if known else e.get("strLeague"),
            "country": known.country if known else e.get("strCountry"),
            "tsdb_id": str(e.get("idLeague")),
            "season": season_label(kickoff) if pd.notna(kickoff) else None,
            "home_team": e.get("strHomeTeam"),
            "away_team": e.get("strAwayTeam"),
            "home_score": pd.to_numeric(e.get("intHomeScore"), errors="coerce"),
            "away_score": pd.to_numeric(e.get("intAwayScore"), errors="coerce"),
            "venue": e.get("strVenue"),
            "source": SOURCE,
            "source_match_id": e.get("idEvent"),
        })
    df = pd.DataFrame(recs)
    return df.dropna(subset=["home_team", "away_team"]) if not df.empty else df


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
    done = done.dropna(subset=["home_score", "away_score"])
    if done.empty:
        return schema.empty()
    done["date"] = pd.to_datetime(done["kickoff_utc"], utc=True).dt.tz_localize(None).dt.normalize()
    done["outcome"] = [schema.result_to_outcome(h, a)
                       for h, a in zip(done["home_score"], done["away_score"])]
    return schema.conform(done.dropna(subset=["outcome"]))


def _get(sess, key, ttl, path, params, force):
    try:
        return cache.fetch(sess, PROVIDER, key, ttl, path, params, force=force)
    except (QuotaExhausted, RuntimeError) as exc:
        return {"_error": str(exc)}, False


def next_events(sess: BudgetedSession, leagues=None, force: bool = False):
    """Upcoming fixtures per league — includes the Betway Premiership."""
    leagues = leagues if leagues is not None else [lg for lg in REGISTRY if lg.tsdb_id]
    frames, notes = [], []
    for lg in leagues:
        payload, cached = _get(sess, f"next_{lg.tsdb_id}", cache.TTL_FIXTURES,
                               "eventsnextleague.php", {"id": lg.tsdb_id}, force)
        if "_error" in payload:
            notes.append(f"{lg.name}: {payload['_error']}")
            continue
        rows = _rows(payload.get("events") or [])
        if not rows.empty:
            frames.append(rows)
        notes.append(f"{lg.name}: {len(rows)} upcoming" + (" (cache)" if cached else ""))
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), notes


def past_events(sess: BudgetedSession, leagues=None, force: bool = False):
    """Most recent finished results per league (cross-check / recent top-up)."""
    leagues = leagues if leagues is not None else [lg for lg in REGISTRY if lg.tsdb_id]
    frames, notes = [], []
    for lg in leagues:
        payload, cached = _get(sess, f"past_{lg.tsdb_id}", cache.TTL_FIXTURES,
                               "eventspastleague.php", {"id": lg.tsdb_id}, force)
        if "_error" in payload:
            notes.append(f"{lg.name}: {payload['_error']}")
            continue
        rows = _rows(payload.get("events") or [])
        if not rows.empty:
            frames.append(rows)
        notes.append(f"{lg.name}: {len(rows)} recent results"
                     + (" (cache)" if cached else ""))
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), notes


def sa_history(sess: BudgetedSession, start_years=None, force: bool = False):
    """Betway Premiership season by season — covers the CURRENT season too,
    which is exactly where API-Football's free plan gives up."""
    if start_years is None:
        latest = current_season_start_year()
        start_years = list(range(2018, latest + 1))
    frames, notes = [], []
    for year in start_years:
        season = tsdb_season(year)
        payload, cached = _get(sess, f"season_{SA_LEAGUE.tsdb_id}_{season}",
                               cache.TTL_SEASON, "eventsseason.php",
                               {"id": SA_LEAGUE.tsdb_id, "s": season}, force)
        if "_error" in payload:
            notes.append(f"SA PSL {season}: {payload['_error']}")
            break
        rows = _rows(payload.get("events") or [])
        matches = to_matches(rows)
        if not matches.empty:
            matches["league"] = SA_LEAGUE.name
            matches["country"] = SA_LEAGUE.country
            frames.append(matches)
        notes.append(f"SA PSL {season}: {len(matches)} finished"
                     + (" (cache)" if cached else ""))
    combined = pd.concat(frames, ignore_index=True) if frames else schema.empty()
    if using_free_key():
        notes.append("note: the free key truncates season lists — set THESPORTSDB_KEY "
                     "to a Patreon key for full seasons")
    return combined, notes


# ---------------------------------------------------------------- badges
_BADGE_FIELDS = ("strBadge", "strTeamBadge", "strLogo")


def _badge_of(entry: dict) -> str | None:
    for field in _BADGE_FIELDS:
        url = entry.get(field)
        if url:
            return str(url)
    return None


def _query_variants(team: str) -> list[str]:
    """Spellings worth searching for one canonical club.

    TheSportsDB has its own name for everything, and it is not always ours:
    "Bayern Munchen" returns only a *basketball* club, while "Bayern Munich"
    finds the football one. We already keep a map of how other sources spell
    each club — clean/team_aliases.json — so reuse it as the query list rather
    than inventing a second one.
    """
    import json
    import os

    global _ALIAS_VARIANTS
    if "_ALIAS_VARIANTS" not in globals() or _ALIAS_VARIANTS is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "clean", "team_aliases.json")
        variants: dict[str, list[str]] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                raw = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
            for spelling, canonical in raw.items():
                variants.setdefault(canonical, []).append(spelling)
        except OSError:
            pass
        _ALIAS_VARIANTS = variants

    out = [team]
    for alt in _ALIAS_VARIANTS.get(team, []):
        if alt not in out:
            out.append(alt)
    return out


_ALIAS_VARIANTS = None


def find_badge(sess: BudgetedSession, team: str, league: str | None = None,
               country: str | None = None, force: bool = False):
    """Best-matching club badge for `team`. -> (url, matched_name, confidence)

    Two filters do the real work here:

      * `strSport == "Soccer"` — searching "Bayern Munchen" otherwise returns
        Bayern München *Basketball* first, and the app would show a basketball
        crest on a football card.
      * league/country agreement — club names repeat across countries, so a
        candidate playing in the right competition is preferred over a closer
        string match somewhere else entirely.

    Confidence is reported rather than thresholded, so a caller can store
    everything and eyeball the weak ones instead of silently dropping clubs.
    """
    from clean.normalize import _key

    candidates: list[dict] = []
    for query in _query_variants(team):
        key = f"searchteams_{query}"
        payload, _cached = _get(sess, key, cache.TTL_SEASON,
                                "searchteams.php", {"t": query}, force)
        if "_error" in payload:
            cache.forget(PROVIDER, key)
            continue
        found = [c for c in (payload.get("teams") or [])
                 if str(c.get("strSport", "")).lower() == "soccer"]
        if found:
            candidates = found
            break
        # An empty result here is usually the free tier throttling us, not the
        # club being absent — Chelsea and Celtic both "vanished" mid-run once.
        # Do not let that harden into a week-long cached miss.
        cache.forget(PROVIDER, key)
    if not candidates:
        return None, None, 0.0

    target = _key(team)

    def score(entry: dict) -> float:
        name = entry.get("strTeam") or ""
        value = 0.0
        if _key(name) == target:
            value += 3.0
        elif target and target in _key(name):
            value += 1.5
        if league and str(entry.get("strLeague", "")).strip():
            if _key(entry["strLeague"]) == _key(league):
                value += 2.0
        if country and _key(str(entry.get("strCountry", ""))) == _key(country):
            value += 1.0
        if _badge_of(entry):
            value += 0.5
        return value

    best = max(candidates, key=score)
    url = _badge_of(best)
    if not url:
        return None, best.get("strTeam"), 0.0
    return url, best.get("strTeam"), round(score(best), 2)
