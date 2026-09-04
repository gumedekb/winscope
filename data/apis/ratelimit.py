"""Budgeted HTTP for the free API tiers.

Every provider gets a hard budget derived from its published free-tier limit and
then *capped to 90%* of it (CAP_RATIO), so the pipeline stops well short of the
number that gets an account throttled or banned. API-Football is the one that
matters most: 100 requests/day free -> we will never issue more than 90.

Three layers of protection, in order:
  1. Persistent counters in output/.quota.json — daily usage survives across runs
     and across GitHub Actions jobs on the same checkout, so ten runs in a day
     share ONE budget rather than getting a fresh 100 each.
  2. Provider-reported headers (API-Football sends x-ratelimit-requests-remaining)
     are treated as authoritative and overwrite the local count — that catches
     usage from other machines/keys sharing the same account.
  3. A per-minute sliding window, also capped to 90%, with an in-process sleep
     rather than a burst.

When the cap is reached the session raises QuotaExhausted; collectors catch it,
log, and degrade gracefully instead of taking the whole run down.
"""
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests

from ingest.env import DATA_DIR, offline

CAP_RATIO = 0.90                 # never spend more than 90% of a free tier
QUOTA_FILE = os.path.join(DATA_DIR, "output", ".quota.json")
MINUTE = 60.0


class QuotaExhausted(RuntimeError):
    """The 90% cap for this provider has been reached — stop calling it."""


class OfflineError(RuntimeError):
    """WINSCOPE_OFFLINE=1. Set by the test suite; no request may leave the box."""


@dataclass(frozen=True)
class Budget:
    provider: str
    per_day: int | None = None
    per_minute: int | None = None
    cap_ratio: float = CAP_RATIO

    @staticmethod
    def _cap(limit, ratio):
        if limit is None:
            return None
        return max(1, int(limit * ratio))       # floor, but never below 1

    @property
    def day_cap(self):
        return self._cap(self.per_day, self.cap_ratio)

    @property
    def minute_cap(self):
        return self._cap(self.per_minute, self.cap_ratio)

    def describe(self) -> str:
        bits = []
        if self.per_day:
            bits.append(f"{self.day_cap}/{self.per_day} per day")
        if self.per_minute:
            bits.append(f"{self.minute_cap}/{self.per_minute} per min")
        return f"{self.provider}: " + ", ".join(bits) + f" (cap {int(self.cap_ratio*100)}%)"


class QuotaStore:
    """output/.quota.json — usage counters that survive between runs."""

    def __init__(self, path: str = QUOTA_FILE):
        self.path = path
        self.data: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def _today(self) -> str:
        # Providers reset their daily quota on the UTC day — using the local date
        # would hand us a fresh budget hours early and blow through the real cap.
        from ingest.clock import utc_today
        return utc_today().isoformat()

    def state(self, provider: str) -> dict:
        st = self.data.setdefault(provider, {})
        if st.get("day") != self._today():        # new UTC-local day -> reset
            st.update(day=self._today(), day_used=0)
        st.setdefault("day_used", 0)
        st.setdefault("minute_window", [])
        return st

    def used_today(self, provider: str) -> int:
        return int(self.state(provider).get("day_used", 0))

    def note_request(self, provider: str) -> None:
        st = self.state(provider)
        st["day_used"] = int(st["day_used"]) + 1
        st["minute_window"] = [t for t in st["minute_window"] if time.time() - t < MINUTE]
        st["minute_window"].append(time.time())
        st["last_request"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def sync_day_used(self, provider: str, used: int) -> None:
        """Provider-reported usage wins over our local count."""
        st = self.state(provider)
        st["day_used"] = max(int(st.get("day_used", 0)), int(used))
        st["reported_by_provider"] = int(used)

    def minute_window(self, provider: str) -> list:
        st = self.state(provider)
        st["minute_window"] = [t for t in st["minute_window"] if time.time() - t < MINUTE]
        return st["minute_window"]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)


class BudgetedSession:
    """A requests session that refuses to exceed 90% of a free tier."""

    def __init__(self, budget: Budget, base_url: str = "", headers: dict | None = None,
                 store: QuotaStore | None = None, timeout: int = 30, verbose: bool = True):
        self.budget = budget
        self.base_url = base_url.rstrip("/")
        self.store = store if store is not None else QuotaStore()
        self.timeout = timeout
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "winscope-etl/1.0"})
        if headers:
            self.session.headers.update(headers)

    # -- budget checks ----------------------------------------------------
    def remaining_today(self) -> int | None:
        if self.budget.day_cap is None:
            return None
        return max(0, self.budget.day_cap - self.store.used_today(self.budget.provider))

    def can_spend(self, n: int = 1) -> bool:
        left = self.remaining_today()
        return left is None or left >= n

    def _await_minute_slot(self) -> None:
        cap = self.budget.minute_cap
        if cap is None:
            return
        window = self.store.minute_window(self.budget.provider)
        if len(window) < cap:
            return
        wait = min(MINUTE - (time.time() - min(window)) + 0.5, MINUTE + 5)
        if wait > 0:
            if self.verbose:
                print(f"    · {self.budget.provider}: per-minute cap ({cap}) hit, "
                      f"pausing {wait:.0f}s")
            time.sleep(wait)

    def _sync_from_headers(self, headers) -> None:
        """API-Football reports the day's true remaining count — trust it over ours."""
        limit = headers.get("x-ratelimit-requests-limit")
        remaining = headers.get("x-ratelimit-requests-remaining")
        if limit is None or remaining is None:
            return
        try:
            used = int(limit) - int(remaining)
        except (TypeError, ValueError):
            return
        self.store.sync_day_used(self.budget.provider, used)

    # -- the one entry point ----------------------------------------------
    def get_json(self, path: str, params: dict | None = None) -> dict:
        if offline():
            raise OfflineError(
                f"WINSCOPE_OFFLINE=1 — refusing to call {self.budget.provider}")
        if not self.can_spend(1):
            raise QuotaExhausted(
                f"{self.budget.provider}: 90% daily cap reached "
                f"({self.store.used_today(self.budget.provider)}/{self.budget.day_cap} "
                f"of {self.budget.per_day}) — skipping")
        self._await_minute_slot()

        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        self.store.note_request(self.budget.provider)
        self.store.save()                       # persist BEFORE the call, not after
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"{self.budget.provider} request failed: {exc}") from exc
        self._sync_from_headers(resp.headers)
        self.store.save()

        if resp.status_code == 429:
            raise QuotaExhausted(f"{self.budget.provider}: HTTP 429 rate limited")
        if resp.status_code >= 400:
            raise RuntimeError(f"{self.budget.provider}: HTTP {resp.status_code} "
                               f"for {url} — {resp.text[:180]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise RuntimeError(f"{self.budget.provider}: non-JSON response") from exc

    def status_line(self) -> str:
        used = self.store.used_today(self.budget.provider)
        cap = self.budget.day_cap
        if cap is None:
            return f"{self.budget.provider}: {used} requests today (no daily limit)"
        pct = 100 * used / cap if cap else 0
        return (f"{self.budget.provider}: {used}/{cap} used today "
                f"({pct:.0f}% of the 90% cap, hard tier limit {self.budget.per_day})")
