"""One definition of "today", in UTC.

Every fixture timestamp from every source is UTC, and API-Football's date filter
is UTC — so the pipeline's idea of today must be too. Using the machine's local
date silently breaks the evening run in any timezone ahead of UTC: in South
Africa (UTC+2) the local date rolls over at 22:00 UTC, so from then until
midnight the pipeline asked for *tomorrow* and never fetched the day still being
played. Matches sat frozen at half-time until someone noticed.

It only misbehaves on a developer's machine — GitHub Actions runs in UTC — which
is exactly the kind of bug that survives CI.
"""
import datetime as dt


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_today() -> dt.date:
    return utc_now().date()
