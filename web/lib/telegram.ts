import { fixturesDb } from './db';
import { CONFIDENT_MIN } from './confidence';
import { config } from './config';
import { ensurePredictionsTable, getFreshness, getPredictions, getScoredHistory, getUpcoming } from './fixtures';
import { agoLabel } from './liveness';
import { labelFor, permAdvice, pickCode } from './perm';
import { missingPredictions } from './predictSlate';

/**
 * The Telegram bot's replies — plain Turso reads, no AI, no football API.
 *
 * Every command answers from the same `fixtures` + `predictions` join the
 * dashboard shows, so the bot cannot disagree with the site. /scope renders
 * the same message as the daily digest (data/digest.py) — keep the two in step.
 *
 * Days are SAST days: "today" and "yesterday" are what they are on your phone,
 * not in UTC. South Africa has no daylight saving, so a fixed +2h is exact.
 */

const SAST_MS = 2 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_LINES = 25;              // Telegram messages cap at 4096 chars
const MIN_LEAGUE_SAMPLE = 10;      // per-league model-vs-market needs this many scored matches

const CODE: Record<number, string> = { 1: '1', 2: 'X', 3: '2' };
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const COMMANDS: { command: string; description: string }[] = [
  { command: 'scope', description: "Today's picks (the daily digest, again)" },
  { command: 'next', description: 'Picks for the next day with fixtures' },
  { command: 'top10', description: '10 most confident upcoming picks, any date' },
  { command: 'top20', description: '20 most confident upcoming picks, any date' },
  { command: 'prev', description: "Yesterday's >50% picks and how they landed" },
  { command: 'record', description: 'Hit rate so far, by confidence' },
  { command: 'check', description: 'Is the pipeline alive?' },
  { command: 'help', description: 'What I can do' },
];

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const pct = (p: number) => `${Math.round(p * 100)}%`;

/** Turso stores kickoffs as `2026-09-26T14:00:00+00:00`; bounds use the same shape so text comparison holds. */
const isoUtc = (ms: number) => new Date(ms).toISOString().slice(0, 19) + '+00:00';

/** A SAST calendar day `offset` days from today, as UTC bounds plus a label. */
function sastDay(offset = 0, now = Date.now()) {
  const local = new Date(now + SAST_MS);
  const start = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate() + offset) - SAST_MS;
  return { start, end: start + DAY_MS, label: dayLabel(start) };
}

function dayLabel(ms: number): string {
  const d = new Date(ms + SAST_MS);
  return `${WEEKDAYS[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, '0')} ${MONTHS[d.getUTCMonth()]}`;
}

function kickoff(iso: string): string {
  const d = new Date(Date.parse(iso) + SAST_MS);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

interface Row {
  kickoff_utc: string;
  league: string;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  outcome: number | null;
  home_win: number | null;
  draw: number | null;
  away_win: number | null;
  predicted_outcome: number | null;
}

const numOrNull = (v: unknown) => (v === null || v === undefined ? null : Number(v));

async function rows(sql: string, args: (string | number)[] = []): Promise<Row[]> {
  await ensurePredictionsTable();
  const rs = await fixturesDb.execute({ sql, args });
  return (rs.rows as unknown as Record<string, unknown>[]).map((r) => ({
    kickoff_utc: String(r.kickoff_utc),
    league: String(r.league),
    home_team: String(r.home_team),
    away_team: String(r.away_team),
    home_score: numOrNull(r.home_score),
    away_score: numOrNull(r.away_score),
    outcome: numOrNull(r.outcome),
    home_win: numOrNull(r.home_win),
    draw: numOrNull(r.draw),
    away_win: numOrNull(r.away_win),
    predicted_outcome: numOrNull(r.predicted_outcome),
  }));
}

const JOINED = `SELECT f.kickoff_utc, f.league, f.home_team, f.away_team, f.home_score, f.away_score, f.outcome,
                       p.home_win, p.draw, p.away_win, p.predicted_outcome
                  FROM fixtures f LEFT JOIN predictions p ON p.match_key = f.match_key`;

/** The model's call and the probability on it; null when nothing is on file. */
function callOf(r: Row): { pick: number; top: number } | null {
  if (r.home_win === null || r.draw === null || r.away_win === null) return null;
  const h = r.home_win, d = r.draw, a = r.away_win;
  const pick = r.predicted_outcome ?? (h >= d && h >= a ? 1 : d >= a ? 2 : 3);
  return { pick, top: Math.max(h, d, a) };
}

const fixtureLine = (r: Row) =>
  `${kickoff(r.kickoff_utc)} ${esc(r.league)} · ${esc(r.home_team)} v ${esc(r.away_team)}`;

const pickLabel = (r: Row, pick: number) => esc(labelFor(pickCode(pick), r.home_team, r.away_team));

/** Leagues where the model beats / trails the bookmakers' favourite on real results. */
async function leagueEdge(): Promise<{ ahead: string[]; behind: string[] }> {
  const rs = await fixturesDb.execute(
    `SELECT f.league, f.outcome, p.predicted_outcome, p.market_home, p.market_draw, p.market_away
       FROM fixtures f JOIN predictions p ON p.match_key = f.match_key
      WHERE f.status_group = 'finished' AND f.outcome IS NOT NULL AND p.market_home IS NOT NULL`
  );
  const tally = new Map<string, [number, number, number]>();
  for (const r of rs.rows as unknown as Record<string, unknown>[]) {
    const mh = Number(r.market_home), md = Number(r.market_draw), ma = Number(r.market_away);
    const fav = mh >= md && mh >= ma ? 1 : md >= ma ? 2 : 3;
    const t = tally.get(String(r.league)) ?? [0, 0, 0];
    t[0]++;
    if (Number(r.predicted_outcome) === Number(r.outcome)) t[1]++;
    if (fav === Number(r.outcome)) t[2]++;
    tally.set(String(r.league), t);
  }
  const ahead: string[] = [], behind: string[] = [];
  for (const [league, [n, model, market]] of tally) {
    if (n < MIN_LEAGUE_SAMPLE) continue;
    if (model > market) ahead.push(league);
    else if (model < market) behind.push(league);
  }
  return { ahead: ahead.sort(), behind: behind.sort() };
}

/** The digest for one SAST day — the same message data/digest.py sends at 06:00. */
async function digestFor(day: { start: number; end: number; label: string }): Promise<string> {
  const fixtures = await rows(
    `${JOINED} WHERE f.kickoff_utc >= ? AND f.kickoff_utc < ?
                 AND f.status_group IN ('scheduled', 'in_play')
               ORDER BY f.kickoff_utc, f.league`,
    [isoUtc(day.start), isoUtc(day.end)]
  );
  const title = `<b>WinScope — ${day.label}</b>`;
  if (fixtures.length === 0) return `${title}\nNo fixtures today.`;

  const predicted = fixtures.filter((r) => callOf(r));
  const confident: [number, string][] = [];
  const perms: string[] = [];
  for (const r of predicted) {
    const { pick, top } = callOf(r)!;
    if (top > CONFIDENT_MIN) {
      confident.push([top, `• ${fixtureLine(r)} — <b>${CODE[pick]}</b> ${pickLabel(r, pick)} ${pct(top)}`]);
    }
    const advice = permAdvice({ home: r.home_win!, draw: r.draw!, away: r.away_win! }, pick);
    if (advice.perm) perms.push(`• ${fixtureLine(r)} — <b>${advice.selections.join('')}</b> (${advice.reason})`);
  }

  const parts = [title, `${fixtures.length} fixtures · ${predicted.length} predicted`];
  if (confident.length) {
    confident.sort((a, b) => b[0] - a[0]);
    parts.push('', `<b>Confident (&gt;${pct(CONFIDENT_MIN)})</b>`, ...confident.slice(0, MAX_LINES).map(([, s]) => s));
    if (confident.length > MAX_LINES) parts.push(`… and ${confident.length - MAX_LINES} more on the dashboard`);
  } else {
    parts.push('', predicted.length ? 'No pick above 50% today.' : 'No predictions yet.');
  }
  if (perms.length) parts.push('', '<b>Perm these</b> (live draw / no clear favourite)', ...perms.slice(0, MAX_LINES));

  const { ahead, behind } = await leagueEdge();
  if (ahead.length || behind.length) {
    parts.push('');
    if (ahead.length) parts.push(`▲ Ahead of the market in: ${esc(ahead.join(', '))}`);
    if (behind.length) parts.push(`▼ Behind the market in: ${esc(behind.join(', '))}`);
  }
  const missing = fixtures.length - predicted.length;
  if (missing) parts.push('', `<i>${missing} fixture(s) still without a prediction — the ETL retries every 20 min.</i>`);
  return parts.join('\n');
}

/** /scope — today's digest, on demand. */
export const scope = () => digestFor(sastDay(0));

/** /next — the digest for the first day after today that has fixtures. */
export async function next(): Promise<string> {
  const rs = await fixturesDb.execute({
    sql: `SELECT MIN(kickoff_utc) AS k FROM fixtures WHERE status_group = 'scheduled' AND kickoff_utc >= ?`,
    args: [isoUtc(sastDay(1).start)],
  });
  const k = (rs.rows[0] as unknown as Record<string, unknown> | undefined)?.k;
  if (!k) return 'No fixtures on file after today.';
  const days = Math.round((sastDay(0, Date.parse(String(k))).start - sastDay(0).start) / DAY_MS);
  return digestFor(sastDay(days));
}

/** /top10, /top20 — the most confident upcoming calls, whatever the date. */
export async function top(n: number): Promise<string> {
  const picks = await rows(
    `${JOINED} WHERE f.status_group = 'scheduled' AND f.kickoff_utc >= ? AND p.home_win IS NOT NULL
               ORDER BY MAX(p.home_win, p.draw, p.away_win) DESC, f.kickoff_utc
               LIMIT ?`,
    [isoUtc(Date.now()), n]
  );
  if (picks.length === 0) return 'No upcoming predictions on file.';
  const lines = picks.map((r, i) => {
    const { pick, top: p } = callOf(r)!;
    return `${i + 1}. ${dayLabel(Date.parse(r.kickoff_utc))} ${fixtureLine(r)} — <b>${CODE[pick]}</b> ${pickLabel(r, pick)} ${pct(p)}`;
  });
  return [`<b>Top ${n} upcoming</b>`, ...lines].join('\n');
}

/** /prev — yesterday's confident calls against the results. */
export async function prev(): Promise<string> {
  const day = sastDay(-1);
  const finished = await rows(
    `${JOINED} WHERE f.kickoff_utc >= ? AND f.kickoff_utc < ?
                 AND f.status_group = 'finished' AND f.outcome IS NOT NULL
               ORDER BY f.kickoff_utc, f.league`,
    [isoUtc(day.start), isoUtc(day.end)]
  );
  const title = `<b>WinScope — ${day.label} results</b>`;
  const confident = finished.filter((r) => (callOf(r)?.top ?? 0) > CONFIDENT_MIN);
  if (confident.length === 0) {
    return `${title}\n${finished.length ? `${finished.length} finished, none called above 50%.` : 'No finished matches yesterday.'}`;
  }
  let hits = 0;
  const lines = confident.map((r) => {
    const { pick, top: p } = callOf(r)!;
    const hit = pick === r.outcome;
    if (hit) hits++;
    return `${hit ? '✅' : '❌'} ${esc(r.home_team)} ${r.home_score}–${r.away_score} ${esc(r.away_team)} — called <b>${CODE[pick]}</b> ${pct(p)}`;
  });
  return [title, `<b>${hits}/${confident.length}</b> confident picks landed`, '', ...lines.slice(0, MAX_LINES)].join('\n');
}

/** /record — the Track Record's headline numbers. */
export async function record(): Promise<string> {
  const history = (await getScoredHistory(1000)).filter((m) => m.hit !== null && m.probs && m.predicted_outcome);
  if (history.length === 0) return 'No scored predictions yet.';
  const key = { 1: 'h', 2: 'd', 3: 'a' } as const;
  const conf = (m: (typeof history)[number]) => m.probs![key[m.predicted_outcome as 1 | 2 | 3]];

  const rate = (ms: typeof history) => {
    const hit = ms.filter((m) => m.hit).length;
    return ms.length ? `${hit}/${ms.length} (${pct(hit / ms.length)})` : '—';
  };
  const bands: [string, number, number][] = [
    ['50–55%', 0.5, 0.55], ['55–60%', 0.55, 0.6], ['60–70%', 0.6, 0.7], ['70%+', 0.7, 1.01],
  ];
  const week = Date.now() - 7 * DAY_MS;
  const confident = history.filter((m) => conf(m) > CONFIDENT_MIN);

  const parts = [
    '<b>WinScope track record</b>',
    `All calls: ${rate(history)}`,
    `Confident (&gt;50%): ${rate(confident)}`,
    `Confident, last 7 days: ${rate(confident.filter((m) => Date.parse(m.kickoff_utc) >= week))}`,
    '',
    '<b>By confidence</b>',
  ];
  for (const [label, lo, hi] of bands) {
    const inBand = confident.filter((m) => conf(m) >= lo && conf(m) < hi);
    if (inBand.length) parts.push(`${label}: ${rate(inBand)}`);
  }
  return parts.join('\n');
}

/** /check — is every part of the pipeline answering? */
export async function check(): Promise<string> {
  const lines = ['<b>WinScope status</b>', '✅ Bot: answering'];
  try {
    const fresh = await getFreshness();
    const lastMs = fresh.lastUpdated ? Date.now() - Date.parse(fresh.lastUpdated) : Infinity;
    // The ETL writes every 20 minutes; two hours without a write means it stopped.
    lines.push(`${lastMs < 2 * 60 * 60 * 1000 ? '✅' : '⚠️'} ETL: last write ${agoLabel(lastMs)}`);
    const upcoming = await getUpcoming(300);
    const missing = missingPredictions(upcoming, await getPredictions(upcoming.map((f) => f.match_key)));
    lines.push(`${missing.length ? '⚠️' : '✅'} Predictions: ${upcoming.length - missing.length}/${upcoming.length} upcoming on file`);
    if (fresh.nextKickoff) lines.push(`Next kickoff: ${dayLabel(Date.parse(fresh.nextKickoff))} ${kickoff(fresh.nextKickoff)}`);
    if (fresh.stranded) lines.push(`⚠️ ${fresh.stranded} match(es) stuck past kickoff`);
  } catch (e) {
    lines.push(`❌ Database: ${esc(e instanceof Error ? e.message : String(e))}`);
  }
  // Render's free tier sleeps; a cold start is ~50s, so a short timeout means
  // "asleep", not "broken" — the ETL wakes it before every backfill.
  try {
    const res = await fetch(`${config.modelServerUrl.replace(/\/$/, '')}/health`, { signal: AbortSignal.timeout(8000) });
    lines.push(`${res.ok ? '✅' : '❌'} Model server: HTTP ${res.status}`);
  } catch {
    lines.push('💤 Model server: asleep (the next ETL pass wakes it)');
  }
  return lines.join('\n');
}

export const help = () =>
  ['<b>ZeusScope commands</b>', ...COMMANDS.map((c) => `/${c.command} — ${esc(c.description)}`)].join('\n');
