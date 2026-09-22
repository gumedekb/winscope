"""Telegram digest: today's confident picks and perm advice, on your phone.

    python digest.py                 # build today's (SAST) message and send it
    python digest.py --dry-run       # print it, send nothing
    python digest.py --if-due        # send once per day, after DIGEST_HOUR (the ETL loop calls this)
    python digest.py --date 2026-09-19

Reads Turso only — the same `fixtures` + `predictions` join the dashboard
shows — so it costs no API credit and cannot disagree with the site. The ETL
calls `--if-due` after every pass; the first pass after DIGEST_HOUR (SAST)
sends, and a marker file in output/ (kept in the Actions cache) stops the
later passes sending again.

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (GitHub secrets, or data/.env).

Perm thresholds are the web app's (web/lib/perm.ts) — keep the two in step.
"""
import argparse
import html
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from ingest.env import get, load_env
from sinks.turso import TABLE, Turso, TursoError, configured

SAST = ZoneInfo("Africa/Johannesburg")
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
MARKER = os.path.join(OUTPUT, ".digest_sent")

CONFIDENT_MIN = 0.50      # web/lib/confidence.ts
PERM_DRAW_MIN = 0.30      # web/lib/perm.ts
PERM_TOP_MAX = 0.40
MIN_LEAGUE_SAMPLE = 10    # per-league model-vs-market needs this many scored matches
MAX_LINES = 25            # Telegram messages cap at 4096 chars

CODE = {1: "1", 2: "X", 3: "2"}


def day_bounds_utc(day: datetime.date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=SAST)
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end.astimezone(timezone.utc).isoformat(timespec="seconds"))


def todays_fixtures(client: Turso, day) -> list[dict]:
    lo, hi = day_bounds_utc(day)
    return client.rows(
        f"""SELECT f.match_key, f.kickoff_utc, f.league, f.home_team, f.away_team, f.status_group,
                   p.home_win, p.draw, p.away_win, p.predicted_outcome
              FROM {TABLE} f LEFT JOIN predictions p ON p.match_key = f.match_key
             WHERE f.kickoff_utc >= ? AND f.kickoff_utc < ?
               AND f.status_group IN ('scheduled', 'in_play')
             ORDER BY f.kickoff_utc, f.league""", [lo, hi])


def league_edge(client: Turso) -> tuple[list[str], list[str]]:
    """Leagues where the model beats / trails the bookmakers' favourite on real results."""
    rows = client.rows(
        f"""SELECT f.league, f.outcome, p.predicted_outcome,
                   p.market_home, p.market_draw, p.market_away
              FROM {TABLE} f JOIN predictions p ON p.match_key = f.match_key
             WHERE f.status_group = 'finished' AND f.outcome IS NOT NULL
               AND p.market_home IS NOT NULL""")
    tally: dict[str, list[int]] = {}
    for r in rows:
        mh, md, ma = r["market_home"], r["market_draw"], r["market_away"]
        fav = 1 if mh >= md and mh >= ma else (2 if md >= ma else 3)
        t = tally.setdefault(r["league"], [0, 0, 0])
        t[0] += 1
        t[1] += int(r["predicted_outcome"] == r["outcome"])
        t[2] += int(fav == r["outcome"])
    ahead = sorted(lg for lg, (n, m, k) in tally.items() if n >= MIN_LEAGUE_SAMPLE and m > k)
    behind = sorted(lg for lg, (n, m, k) in tally.items() if n >= MIN_LEAGUE_SAMPLE and m < k)
    return ahead, behind


def perm_for(h: float, d: float, a: float, pick: int) -> tuple[str, str] | None:
    """-> (codes like '1X', reason) when this match deserves two selections.

    Always the pick plus the draw: the model under-rates draws, so in a tight
    game "the two most likely" would skip the very result tight games produce.
    """
    top = max(h, d, a)
    if d >= PERM_DRAW_MIN:
        reason = f"draw {round(d * 100)}%"
    elif top < PERM_TOP_MAX:
        reason = f"no clear favourite {round(top * 100)}%"
    else:
        return None
    if pick == 2:
        return ("1X" if h >= a else "X2"), reason
    return ("1X" if pick == 1 else "X2"), reason


def build_message(day, fixtures: list[dict], ahead: list[str], behind: list[str]) -> str:
    e = html.escape
    title = f"<b>WinScope — {day.strftime('%a %d %b')}</b>"
    if not fixtures:
        return f"{title}\nNo fixtures today."

    predicted = [f for f in fixtures if f["home_win"] is not None]
    missing = len(fixtures) - len(predicted)

    def ko(f):
        return datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00")).astimezone(SAST).strftime("%H:%M")

    confident, perms = [], []
    for f in predicted:
        h, d, a = float(f["home_win"]), float(f["draw"]), float(f["away_win"])
        pick = int(f["predicted_outcome"] or (1 if h >= d and h >= a else 2 if d >= a else 3))
        top = max(h, d, a)
        name = {1: f["home_team"], 2: "Draw", 3: f["away_team"]}[pick]
        line = f"{ko(f)} {e(f['league'])} · {e(f['home_team'])} v {e(f['away_team'])}"
        if top > CONFIDENT_MIN:
            confident.append((top, f"• {line} — <b>{CODE[pick]}</b> {e(name)} {round(top * 100)}%"))
        p = perm_for(h, d, a, pick)
        if p:
            perms.append(f"• {line} — <b>{p[0]}</b> ({p[1]})")

    parts = [title, f"{len(fixtures)} fixtures · {len(predicted)} predicted"]
    if confident:
        confident.sort(key=lambda t: -t[0])
        parts += ["", f"<b>Confident (&gt;{round(CONFIDENT_MIN * 100)}%)</b>"]
        parts += [s for _, s in confident[:MAX_LINES]]
        if len(confident) > MAX_LINES:
            parts.append(f"… and {len(confident) - MAX_LINES} more on the dashboard")
    else:
        parts += ["", "No pick above 50% today."]
    if perms:
        parts += ["", "<b>Perm these</b> (live draw / no clear favourite)"]
        parts += perms[:MAX_LINES]
    if ahead or behind:
        parts.append("")
        if ahead:
            parts.append(f"▲ Ahead of the market in: {e(', '.join(ahead))}")
        if behind:
            parts.append(f"▼ Behind the market in: {e(', '.join(behind))}")
    if missing:
        parts += ["", f"<i>{missing} fixture(s) still without a prediction — the ETL retries every 20 min.</i>"]
    return "\n".join(parts)


def send(text: str) -> None:
    token, chat = get("TELEGRAM_BOT_TOKEN"), get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json={"chat_id": chat, "text": text[:4096], "parse_mode": "HTML",
                            "disable_web_page_preview": True})
    if r.status_code != 200:
        raise RuntimeError(f"Telegram HTTP {r.status_code}: {r.text[:200]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", default=None, help="SAST day, YYYY-MM-DD (default today)")
    parser.add_argument("--dry-run", action="store_true", help="print the message, do not send")
    parser.add_argument("--if-due", action="store_true",
                        help="send only once per day, and only from DIGEST_HOUR (SAST) on")
    args = parser.parse_args(argv)
    load_env()

    now = datetime.now(SAST)
    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now.date()

    if args.if_due:
        hour = int(get("DIGEST_HOUR", "6"))
        if now.hour < hour:
            return 0
        try:
            if open(MARKER, encoding="utf-8").read().strip() == str(day):
                return 0
        except FileNotFoundError:
            pass
        if not get("TELEGRAM_BOT_TOKEN"):
            return 0                                # not set up yet: silently skip

    if not configured():
        print("Turso is not configured — set TURSO_FIXTURES_URL/_TOKEN")
        return 1
    try:
        client = Turso()
        fixtures = todays_fixtures(client, day)
        ahead, behind = league_edge(client)
    except TursoError as exc:
        print(f"  ! {exc}")
        return 1

    text = build_message(day, fixtures, ahead, behind)
    if args.dry_run:
        print(text)
        return 0
    try:
        send(text)
    except RuntimeError as exc:
        print(f"  ! digest not sent: {exc}")
        return 1
    print(f"  digest sent: {len(fixtures)} fixture(s) for {day}")
    if args.if_due:
        os.makedirs(OUTPUT, exist_ok=True)
        with open(MARKER, "w", encoding="utf-8") as fh:
            fh.write(str(day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
