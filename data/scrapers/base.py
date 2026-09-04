"""Polite HTML fetching for the one-off scrapers.

Rules this enforces, so no individual scraper has to remember them:
  * robots.txt is consulted for every URL and a disallowed path is refused
    outright — not warned about, refused. BetExplorer, for instance, disallows
    its `?stage=`/`?year=` season archives, so those are simply off the table.
  * one request at a time with a real delay between them; these are one-off
    backfills, not something that needs to be fast.
  * every page is cached on disk, so re-running the parser while developing
    costs the site nothing.
"""
import hashlib
import os
import re
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from ingest.env import DATA_DIR

CACHE_DIR = os.path.join(DATA_DIR, "output", "cache", "scrape")
USER_AGENT = "WinScopeBot/1.0 (personal football-data project; contact via repo)"
DELAY_SECONDS = 2.0


class Disallowed(RuntimeError):
    """robots.txt says no. Not something to work around."""


def _rule_to_regex(pattern: str) -> re.Pattern:
    """Translate a robots.txt path pattern into a regex.

    `*` matches any run of characters and a trailing `$` anchors the end — the
    de-facto standard every major crawler follows. Python's stdlib
    RobotFileParser ignores these wildcards, so `Disallow: /*?year=` reads as
    "allowed" there. That is not a detail we can shrug at: it is exactly the
    rule covering BetExplorer's dated archive pages.
    """
    anchored_end = pattern.endswith("$")
    if anchored_end:
        pattern = pattern[:-1]
    escaped = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
    return re.compile("^" + escaped + ("$" if anchored_end else ""))


def parse_wildcard_rules(text: str) -> list[tuple[str, re.Pattern]]:
    """Disallow/Allow rules from the `User-agent: *` group, in file order.

    Longest-match-wins is the standard tie-break between a Disallow and a more
    specific Allow, so the raw pattern is kept alongside the regex.
    """
    rules: list[tuple[str, re.Pattern]] = []
    in_star_group = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            in_star_group = value == "*"
        elif field in ("disallow", "allow") and in_star_group and value:
            rules.append((field, value, _rule_to_regex(value)))
    return rules


class PoliteFetcher:
    def __init__(self, delay: float = DELAY_SECONDS, verbose: bool = True):
        self.delay = delay
        self.verbose = verbose
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._wildcard: dict[str, list] = {}
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en",
        })
        self.fetched = 0
        self.from_cache = 0

    # -- robots -----------------------------------------------------------
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{origin}/robots.txt")
            try:
                # Fetch it ourselves so the UA header is sent.
                resp = self.session.get(f"{origin}/robots.txt", timeout=20)
                rp.parse(resp.text.splitlines() if resp.ok else [])
                self._wildcard[origin] = parse_wildcard_rules(resp.text if resp.ok else "")
            except requests.RequestException:
                rp.parse([])                # unreachable robots.txt -> allow
                self._wildcard[origin] = []
            self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        """Allowed only if BOTH the stdlib parser and the wildcard rules say so.

        The two can disagree, in both directions: the stdlib ignores wildcard
        patterns (so it under-blocks `/*?year=`), and it applies rules in file
        order rather than longest-match (so it over-blocks a specific `Allow`
        nested under a broader `Disallow`). Taking the stricter verdict means
        occasionally skipping a page we were welcome to fetch — the harmless
        direction to be wrong in.
        """
        rp = self._robots_for(url)
        if not rp.can_fetch(USER_AGENT, url):
            return False

        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        path = parts.path + (f"?{parts.query}" if parts.query else "")

        # Most specific matching rule wins; Allow beats Disallow at equal length.
        best_len, verdict = -1, True
        for field, pattern, rx in self._wildcard.get(origin, []):
            if rx.match(path):
                weight = len(pattern)
                if weight > best_len or (weight == best_len and field == "allow"):
                    best_len, verdict = weight, field == "allow"
        return verdict

    # -- fetching ---------------------------------------------------------
    def _cache_path(self, url: str) -> str:
        host = urlparse(url).netloc.replace(":", "_")
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return os.path.join(CACHE_DIR, host, f"{digest}.html")

    def get(self, url: str, force: bool = False) -> str:
        if not self.allowed(url):
            raise Disallowed(f"robots.txt disallows {url}")

        path = self._cache_path(url)
        if not force and os.path.exists(path):
            self.from_cache += 1
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()

        wait = self.delay - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        if self.verbose:
            print(f"    · GET {url}")
        resp = self.session.get(url, timeout=30, allow_redirects=True)
        self._last_request = time.time()
        self.fetched += 1
        resp.raise_for_status()

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(resp.text)
        return resp.text
