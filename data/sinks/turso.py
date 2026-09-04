"""Sink: push live/upcoming fixtures to Turso (libSQL).

Talks to Turso's HTTP v2 pipeline API with plain `requests`, so the pipeline
gains NO new dependency — that keeps `pip install` in GitHub Actions fast and
avoids a native build step.

THE RULE THAT SHAPES EVERYTHING HERE: a finished match is never deleted and never
walked backwards. The table is an append-and-advance log, because the whole point
is to come back later and ask "did the model call this one right?". So:

  * writes are UPSERTs keyed on `match_key`, never INSERT-then-DELETE;
  * every row carries a `progress` rank (scheduled 0 -> postponed 1 -> in play 2
    -> finished 3) and the UPDATE only fires when the incoming row is at least as
    advanced, so a stale "scheduled" row from a slower source can never overwrite
    a final score;
  * `finished_at` is stamped once, on the first write that reports a result, and
    is never overwritten after that.

`match_key` is `clean.normalize.match_key` — the exact key model_data.csv uses —
so a prediction row can be joined to its result with a plain equality join.
"""
import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests

from clean.normalize import match_key
from ingest import schema
from ingest.env import get

TABLE = "fixtures"
BATCH_SIZE = 64                  # statements per HTTP round trip

# How far along a match is. The UPSERT refuses to move a row to a lower rank.
PROGRESS = {"scheduled": 0, "off": 1, "in_play": 2, "finished": 3}

SCHEMA_SQL = [
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        match_key       TEXT PRIMARY KEY,   -- 'YYYY-MM-DD|Home Team|Away Team'
        kickoff_utc     TEXT NOT NULL,
        status          TEXT NOT NULL,      -- raw source status (FT, NS, HT, TIMED…)
        status_group    TEXT NOT NULL,      -- scheduled | in_play | finished | off
        progress        INTEGER NOT NULL,   -- 0..3, guards against going backwards
        minute          INTEGER,
        league          TEXT NOT NULL,
        country         TEXT,
        season          TEXT,
        home_team       TEXT NOT NULL,
        away_team       TEXT NOT NULL,
        home_score      INTEGER,
        away_score      INTEGER,
        outcome         INTEGER,            -- 1 home, 2 draw, 3 away; NULL until FT
        venue           TEXT,
        source          TEXT,
        source_match_id TEXT,
        first_seen_at   TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        finished_at     TEXT                -- stamped once, never overwritten
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_kickoff ON {TABLE}(kickoff_utc)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_group ON {TABLE}(status_group, kickoff_utc)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_league ON {TABLE}(league, kickoff_utc)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_finished ON {TABLE}(finished_at)",
]

UPSERT_SQL = f"""
INSERT INTO {TABLE} (
    match_key, kickoff_utc, status, status_group, progress, minute,
    league, country, season, home_team, away_team,
    home_score, away_score, outcome, venue, source, source_match_id,
    first_seen_at, updated_at, finished_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(match_key) DO UPDATE SET
    kickoff_utc     = excluded.kickoff_utc,
    status          = excluded.status,
    status_group    = excluded.status_group,
    progress        = excluded.progress,
    minute          = excluded.minute,
    league          = excluded.league,
    country         = COALESCE(excluded.country,  {TABLE}.country),
    season          = COALESCE(excluded.season,   {TABLE}.season),
    home_score      = COALESCE(excluded.home_score, {TABLE}.home_score),
    away_score      = COALESCE(excluded.away_score, {TABLE}.away_score),
    outcome         = COALESCE(excluded.outcome,  {TABLE}.outcome),
    venue           = COALESCE(excluded.venue,    {TABLE}.venue),
    source          = excluded.source,
    source_match_id = COALESCE(excluded.source_match_id, {TABLE}.source_match_id),
    updated_at      = excluded.updated_at,
    -- stamped on the first write that reports a result, then frozen
    finished_at     = COALESCE({TABLE}.finished_at, excluded.finished_at)
WHERE excluded.progress >= {TABLE}.progress
"""


class TursoError(RuntimeError):
    pass


def configured() -> bool:
    return bool(url()) and bool(token())


def url() -> str:
    """Accepts the libsql:// form Turso shows you and converts it to HTTPS."""
    raw = (get("TURSO_FIXTURES_URL") or get("TURSO_MATCHES_URL")
           or get("TURSO_DATABASE_URL"))
    if not raw:
        return ""
    raw = raw.strip().rstrip("/")
    for prefix in ("libsql://", "wss://", "ws://"):
        if raw.startswith(prefix):
            raw = "https://" + raw[len(prefix):]
            break
    if not raw.startswith("http"):
        raw = "https://" + raw
    return raw


def token() -> str:
    return (get("TURSO_FIXTURES_TOKEN") or get("TURSO_MATCHES_TOKEN")
            or get("TURSO_AUTH_TOKEN"))


# ---------------------------------------------------------------- client
def _arg(value):
    """Python value -> the pipeline API's tagged-value encoding.

    Integers go over the wire as strings — that is the protocol, not a typo; it
    is how libSQL keeps 64-bit values exact through JSON.
    """
    if value is None or value is pd.NA:
        return {"type": "null"}
    try:
        if pd.isna(value):
            return {"type": "null"}
    except (TypeError, ValueError):
        pass                                   # arrays/strings: not a NA scalar
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if pd.api.types.is_integer(value):
        return {"type": "integer", "value": str(int(value))}
    if pd.api.types.is_float(value):
        return {"type": "float", "value": float(value)}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict):
    """Tagged wire value -> Python value."""
    kind = cell.get("type")
    if kind == "null":
        return None
    value = cell.get("value")
    if kind == "integer":
        return int(value)
    if kind == "float":
        return float(value)
    return value


class Turso:
    """Minimal libSQL HTTP client — execute/batch, and nothing we do not use."""

    def __init__(self, base_url: str = "", auth_token: str = "", timeout: int = 30):
        self.base_url = base_url or url()
        self.token = auth_token or token()
        self.timeout = timeout
        if not self.base_url or not self.token:
            raise TursoError(
                "Turso is not configured — set TURSO_FIXTURES_URL and "
                "TURSO_FIXTURES_TOKEN in data/.env (see .env.example)")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "winscope-etl/1.0",
        })

    def _pipeline(self, requests_payload: list) -> list:
        endpoint = f"{self.base_url}/v2/pipeline"
        body = json.dumps({"requests": requests_payload + [{"type": "close"}]})
        try:
            resp = self.session.post(endpoint, data=body, timeout=self.timeout)
        except requests.RequestException as exc:
            raise TursoError(f"cannot reach Turso at {self.base_url}: {exc}") from exc
        if resp.status_code == 401:
            raise TursoError("Turso rejected the auth token (401) — check "
                             "TURSO_FIXTURES_TOKEN")
        if resp.status_code >= 400:
            raise TursoError(f"Turso HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        out = []
        for result in payload.get("results", []):
            if result.get("type") == "error":
                raise TursoError(f"SQL error: {result['error'].get('message')}")
            if result.get("type") == "ok" and "result" in result.get("response", {}):
                out.append(result["response"]["result"])
        return out

    def execute(self, sql: str, args: list | None = None):
        stmt = {"sql": sql}
        if args is not None:
            stmt["args"] = [_arg(a) for a in args]
        results = self._pipeline([{"type": "execute", "stmt": stmt}])
        return results[0] if results else None

    def batch(self, statements: list[tuple]) -> int:
        """[(sql, args), ...] in chunks. Returns rows affected."""
        affected = 0
        for start in range(0, len(statements), BATCH_SIZE):
            chunk = statements[start:start + BATCH_SIZE]
            payload = [{"type": "execute",
                        "stmt": {"sql": sql, "args": [_arg(a) for a in args]}}
                       for sql, args in chunk]
            for result in self._pipeline(payload):
                affected += int(result.get("affected_row_count") or 0)
        return affected

    def ensure_schema(self) -> None:
        self._pipeline([{"type": "execute", "stmt": {"sql": sql}}
                        for sql in SCHEMA_SQL])

    def rows(self, sql: str, args: list | None = None) -> list[dict]:
        """Query -> list of dicts with real Python types.

        The wire protocol sends every integer as a string, so decode by the
        cell's declared type instead of handing callers "2" where 2 is meant.
        """
        result = self.execute(sql, args)
        if not result:
            return []
        cols = [c["name"] for c in result["cols"]]
        return [dict(zip(cols, [_decode(cell) for cell in row]))
                for row in result["rows"]]


# ---------------------------------------------------------------- mapping
def status_group(status) -> str:
    text = str(status).strip().upper()
    if text in schema.STATUS_FINISHED:
        return "finished"
    if text in schema.STATUS_IN_PLAY:
        return "in_play"
    if text in schema.STATUS_OFF:
        return "off"
    return "scheduled"


def _int_or_none(value):
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_rows(fixtures: pd.DataFrame) -> list[tuple]:
    """fixtures.csv shape -> UPSERT parameter tuples."""
    if fixtures is None or fixtures.empty:
        return []
    df = fixtures.copy()
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
    df = df.dropna(subset=["kickoff_utc", "home_team", "away_team"])
    if df.empty:
        return []

    # The join key model_data.csv uses. Built from the UTC kickoff DATE, which is
    # what makes a fixture row and a historical result row line up.
    keyed = df.assign(date=df["kickoff_utc"].dt.tz_convert("UTC").dt.tz_localize(None))
    df["match_key"] = match_key(keyed)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    statements = []
    for row in df.itertuples(index=False):
        group = status_group(row.status)
        home, away = _int_or_none(row.home_score), _int_or_none(row.away_score)
        outcome = None
        if group == "finished" and home is not None and away is not None:
            outcome = 1 if home > away else (3 if away > home else 2)
        statements.append((UPSERT_SQL, [
            row.match_key,
            row.kickoff_utc.isoformat(),
            str(row.status),
            group,
            PROGRESS[group],
            _int_or_none(getattr(row, "minute", None)),
            row.league,
            getattr(row, "country", None),
            getattr(row, "season", None),
            row.home_team,
            row.away_team,
            home,
            away,
            outcome,
            getattr(row, "venue", None),
            getattr(row, "source", None),
            getattr(row, "source_match_id", None),
            now,                                  # first_seen_at (insert only)
            now,                                  # updated_at
            now if group == "finished" else None,  # finished_at, stamped once
        ]))
    return statements


def push(fixtures: pd.DataFrame, client: Turso | None = None, verbose: bool = True) -> dict:
    """Upsert the fixtures frame. Nothing is ever deleted."""
    client = client or Turso()
    client.ensure_schema()
    before = counts(client)
    statements = to_rows(fixtures)
    if not statements:
        if verbose:
            print("  no fixtures to push")
        return {"pushed": 0, **before}
    written = client.batch(statements)
    after = counts(client)
    if verbose:
        print(f"  upserted {written} of {len(statements)} fixture rows -> "
              f"{client.base_url}")
        print(f"  table now: {after['total']} total · {after['scheduled']} scheduled · "
              f"{after['in_play']} in play · {after['finished']} finished (kept)")
        gained = after["total"] - before["total"]
        if gained:
            print(f"  {gained} new match(es) tracked")
    return {"pushed": written, **after}


def counts(client: Turso) -> dict:
    rows = client.rows(
        f"SELECT status_group, COUNT(*) AS n FROM {TABLE} GROUP BY status_group")
    out = {"scheduled": 0, "in_play": 0, "finished": 0, "off": 0}
    for row in rows:
        out[row["status_group"]] = int(row["n"])
    out["total"] = sum(out.values())
    return out


def print_status(client: Turso | None = None) -> None:
    client = client or Turso()
    client.ensure_schema()
    tally = counts(client)
    print(f"\nTurso: {client.base_url}")
    print(f"  table `{TABLE}` — {tally['total']} matches tracked")
    print(f"    scheduled {tally['scheduled']:>5}")
    print(f"    in play   {tally['in_play']:>5}")
    print(f"    finished  {tally['finished']:>5}   (kept for scoring the model)")
    print(f"    off       {tally['off']:>5}   (postponed/cancelled)")
    upcoming = client.rows(
        f"SELECT kickoff_utc, league, home_team, away_team, status FROM {TABLE} "
        f"WHERE status_group IN ('scheduled','in_play') "
        f"ORDER BY kickoff_utc LIMIT 5")
    if upcoming:
        print("\n  next up:")
        for row in upcoming:
            print(f"    {row['kickoff_utc'][:16]}  {row['league']:<22} "
                  f"{row['home_team']} v {row['away_team']}  [{row['status']}]")
    recent = client.rows(
        f"SELECT kickoff_utc, league, home_team, away_team, home_score, away_score "
        f"FROM {TABLE} WHERE status_group='finished' "
        f"ORDER BY finished_at DESC LIMIT 5")
    if recent:
        print("\n  most recently finished (retained):")
        for row in recent:
            print(f"    {row['kickoff_utc'][:16]}  {row['league']:<22} "
                  f"{row['home_team']} {row['home_score']}-{row['away_score']} "
                  f"{row['away_team']}")


# ---------------------------------------------------------------- read back
def fetch_finished(client: Turso | None = None, since: str | None = None) -> pd.DataFrame:
    """Finished matches out of Turso, in the canonical model_data schema.

    This is the loop-closer. Once the cron job stops committing model_data.csv,
    Turso becomes the place new results accumulate — so before a retrain you fold
    them back in with `python pipeline.py --from-turso`. No odds come back (the
    APIs do not carry them), which is fine: those columns are already NULL for
    every API-sourced row in the dataset.
    """
    client = client or Turso()
    client.ensure_schema()
    sql = (f"SELECT kickoff_utc, league, country, season, home_team, away_team, "
           f"home_score, away_score, outcome, source FROM {TABLE} "
           f"WHERE status_group = 'finished' AND outcome IS NOT NULL")
    args = None
    if since:
        sql += " AND kickoff_utc >= ?"
        args = [since]
    rows = client.rows(sql + " ORDER BY kickoff_utc", args)
    if not rows:
        return schema.empty()
    df = pd.DataFrame(rows)
    df["date"] = (pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
                    .dt.tz_localize(None).dt.normalize())
    df = df.drop(columns=["kickoff_utc"]).dropna(subset=["date"])
    return schema.conform(df)


def migrate_team_names(reference: pd.DataFrame, client: Turso | None = None,
                       verbose: bool = True) -> dict:
    """One-time repair for rows written before a team-name fix.

    Renaming a club changes its `match_key`, so without this the corrected
    fixtures would be INSERTed alongside the old misspelled rows and the same
    match would appear twice. Rows are UPDATEd in place; if a rename collides
    with a row that already exists, the LESS advanced of the two duplicates is
    dropped — that removes a redundant copy of a match, never a match.
    """
    from clean.normalize import align_to_vocabulary

    client = client or Turso()
    client.ensure_schema()
    rows = client.rows(
        f"SELECT match_key, kickoff_utc, league, home_team, away_team, progress "
        f"FROM {TABLE}")
    if not rows:
        return {"checked": 0, "renamed": 0, "merged": 0}

    current = pd.DataFrame(rows)
    fixed = align_to_vocabulary(
        current.rename(columns={}).assign(source="TheSportsDB"), reference)
    existing = set(current["match_key"])
    renamed = merged = 0

    for old, new_home, new_away, kickoff, old_home, old_away, progress in zip(
            current["match_key"], fixed["home_team"], fixed["away_team"],
            current["kickoff_utc"], current["home_team"], current["away_team"],
            current["progress"]):
        if new_home == old_home and new_away == old_away:
            continue
        new_key = f"{str(kickoff)[:10]}|{new_home}|{new_away}"
        if new_key == old:
            continue
        if new_key in existing:
            # The corrected row already exists; drop whichever copy is behind.
            other = client.rows(
                f"SELECT progress FROM {TABLE} WHERE match_key = ?", [new_key])
            if other and int(other[0]["progress"]) >= int(progress):
                client.execute(f"DELETE FROM {TABLE} WHERE match_key = ?", [old])
                merged += 1
                if verbose:
                    print(f"    · duplicate dropped: '{old_home} v {old_away}' "
                          f"(superseded by '{new_home} v {new_away}')")
                continue
        client.execute(
            f"UPDATE {TABLE} SET match_key = ?, home_team = ?, away_team = ? "
            f"WHERE match_key = ?", [new_key, new_home, new_away, old])
        existing.discard(old)
        existing.add(new_key)
        renamed += 1
        if verbose:
            print(f"    · renamed: '{old_home} v {old_away}' -> "
                  f"'{new_home} v {new_away}'")
    return {"checked": len(rows), "renamed": renamed, "merged": merged}


# ---------------------------------------------------------------- team assets
ASSETS_TABLE = "team_assets"

ASSETS_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {ASSETS_TABLE} (
    team        TEXT PRIMARY KEY,   -- our canonical club name
    badge_url   TEXT,
    matched_name TEXT,              -- what the source called it, for auditing
    confidence  REAL,
    league      TEXT,
    source      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)"""


def ensure_assets_table(client: Turso) -> None:
    client.execute(ASSETS_SCHEMA)


def save_badges(rows: list[dict], client: Turso | None = None) -> int:
    """Upsert club badges. Keyed on OUR canonical name, so the web can join on
    the same club name the fixtures and model use."""
    client = client or Turso()
    ensure_assets_table(client)
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    statements = [(
        f"""INSERT INTO {ASSETS_TABLE}
              (team, badge_url, matched_name, confidence, league, source, updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(team) DO UPDATE SET
              badge_url    = COALESCE(excluded.badge_url, {ASSETS_TABLE}.badge_url),
              matched_name = excluded.matched_name,
              confidence   = excluded.confidence,
              league       = excluded.league,
              source       = excluded.source,
              updated_at   = excluded.updated_at""",
        [r["team"], r.get("badge_url"), r.get("matched_name"),
         r.get("confidence"), r.get("league"), r.get("source", "TheSportsDB"), now],
    ) for r in rows]
    return client.batch(statements)


def badge_count(client: Turso | None = None) -> dict:
    client = client or Turso()
    ensure_assets_table(client)
    row = client.rows(
        f"SELECT COUNT(*) AS total, SUM(badge_url IS NOT NULL) AS with_badge "
        f"FROM {ASSETS_TABLE}")[0]
    return {"total": int(row["total"] or 0), "with_badge": int(row["with_badge"] or 0)}
