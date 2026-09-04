"""On-disk response cache — the cheapest request is the one you never send.

Lives in output/cache/<provider>/<key>.json. Every collector checks the cache
first, so a re-run inside the TTL costs ZERO quota. Historical seasons get a long
TTL (they never change); live fixtures get a short one.
"""
import hashlib
import json
import os
import time

from ingest.env import DATA_DIR

CACHE_DIR = os.path.join(DATA_DIR, "output", "cache")

TTL_LIVE = 60                    # live scores — 1 minute
TTL_TODAY = 60 * 5               # any window covering today — 5 minutes
TTL_FIXTURES = 60 * 60 * 3       # purely future fixtures — 3 hours
TTL_SEASON = 60 * 60 * 24 * 7    # a finished season's results — 1 week


def ttl_for_date(day, today=None) -> int:
    """TTL for a single-date fixture query.

    Today's date query is NOT a fixture list, it is a live scoreboard: it holds
    matches that are kicking off, in progress and finishing across the evening.
    Caching it for three hours is what left matches frozen at half-time hours
    after full time, because the stale copy kept being served past the final
    whistle. Yesterday and earlier are settled; tomorrow onward barely moves.
    """
    from ingest.clock import utc_today
    today = today or utc_today()
    if day == today:
        return TTL_TODAY
    return TTL_FIXTURES


def ttl_for_window(start, end, today=None) -> int:
    """Same reasoning for a date-range query: if the range covers today, it
    contains matches that are still changing."""
    from ingest.clock import utc_today
    today = today or utc_today()
    return TTL_TODAY if start <= today <= end else TTL_FIXTURES


def _path(provider: str, key: str) -> str:
    safe = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:60]
    return os.path.join(CACHE_DIR, provider, f"{slug}.{safe}.json")


def get(provider: str, key: str, ttl: int):
    path = _path(provider, key)
    if not os.path.exists(path):
        return None
    if ttl >= 0 and time.time() - os.path.getmtime(path) > ttl:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def put(provider: str, key: str, payload) -> None:
    path = _path(provider, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def forget(provider: str, key: str) -> None:
    """Drop a cached response.

    Used when a payload turns out to be a throttling artefact rather than an
    answer. Caching "no results" for a week is worse than not caching at all:
    the miss becomes permanent and a retry silently returns the same emptiness.
    """
    path = _path(provider, key)
    try:
        os.remove(path)
    except OSError:
        pass


def fetch(session, provider: str, key: str, ttl: int, path: str,
          params: dict | None = None, force: bool = False):
    """Cache-first GET. Returns (payload, from_cache)."""
    if not force:
        hit = get(provider, key, ttl)
        if hit is not None:
            return hit, True
    payload = session.get_json(path, params)
    put(provider, key, payload)
    return payload, False
