import { predictionsDb } from './db';
import type { FixtureRow } from './fixtures';

/**
 * Market odds (The Odds API) — consensus 1X2 probabilities per fixture.
 *
 * Two consumers:
 *   1. the model. Trained with USE_ODDS it has three `odds_p_*` features, and
 *      the batch request carries the market consensus per fixture. Measured
 *      +1.6pp accuracy on the hold-out — the biggest single gain available.
 *   2. the track record, which snapshots the pre-match market to benchmark
 *      the model against the bookmakers' favourite.
 *
 * The free tier is 500 credits a MONTH and one call = one league's upcoming
 * matches = one credit. So the cache lives in Turso (`market_odds`), not in
 * process memory — Vercel functions do not share memory, and a per-instance
 * cache would spend the budget many times over — and two guards bound it:
 * a league is refreshed at most every ODDS_TTL_HOURS, and never more than
 * ODDS_DAILY_CAP fetches happen per day in total. Only leagues with a fixture
 * inside the next 48h are fetched at all. The SA league is not on the API;
 * its fixtures simply carry no market.
 */

export interface Probs { home: number; draw: number; away: number }
export type Market = Probs & { books: number };

/** The Odds API sport keys per league. No key = the API does not cover it. */
const SPORT_KEYS: Record<string, string | null> = {
  'Premier League':       'soccer_epl',
  'Championship':         'soccer_efl_champ',
  'League One':           'soccer_england_league1',
  'Bundesliga':           'soccer_germany_bundesliga',
  'Serie A':              'soccer_italy_serie_a',
  'La Liga':              'soccer_spain_la_liga',
  'Ligue 1':              'soccer_france_ligue_one',
  'Eredivisie':           'soccer_netherlands_eredivisie',
  'Liga Portugal':        'soccer_portugal_primeira_liga',
  'Scottish Premiership': 'soccer_spl',
  'Betway Premiership':   null,
};

const TTL_MS = Number(process.env.ODDS_TTL_HOURS || 12) * 3_600_000;
const DAILY_CAP = Number(process.env.ODDS_DAILY_CAP || 14);      // ~420 credits a month
const RESERVE = 25;                                              // stop before the last credits
const LOOKAHEAD_MS = 48 * 3_600_000;
const DEFAULT_MODEL_WEIGHT = 0.45;                               // for models WITHOUT odds features

interface MarketEvent {
  home: string;        // normalised names as the API gives them
  away: string;
  probs: Market;
  commence_time: string;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS market_odds (
    league      TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    events_json TEXT NOT NULL,     -- MarketEvent[]
    remaining   INTEGER            -- x-requests-remaining after that fetch
)`;
const BUDGET_KEY = '_budget';       // one row: fetched_at = day, events_json = count used

let ready: Promise<void> | null = null;
function ensureTable(): Promise<void> {
  if (!ready) {
    ready = predictionsDb.execute(SCHEMA).then(() => undefined).catch((e) => { ready = null; throw e; });
  }
  return ready;
}

// ---------------------------------------------------------------- names
/** Spellings The Odds API uses that our canonical names do not share a token set with. */
const ALIASES: Record<string, string> = {
  'bayern munich': 'bayern munchen',
  'fc cologne': 'koln', 'cologne': 'koln',
  'inter milan': 'inter milan', 'internazionale': 'inter milan',
  'psg': 'paris saint germain', 'paris saint-germain': 'paris saint germain',
  'sporting cp': 'sporting', 'sporting lisbon': 'sporting',
  'athletic bilbao': 'athletic club', 'atletico madrid': 'atletico madrid',
  'psv eindhoven': 'psv', 'az alkmaar': 'az',
  'wolves': 'wolverhampton wanderers',
  'nottingham forest': 'nottingham forest',
};

function norm(name: string): string {
  if (!name) return '';
  let s = name.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
  s = s.replace(/\b(fc|afc|cf|sc|ac|sv|bv|cd|ud|sd|ssc|as|us|og|rc|\d{2,4})\b/g, ' ');
  s = s.replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim();
  return ALIASES[s] || s;
}

const tokens = (s: string) => new Set(s.split(' ').filter(Boolean));
const subset = (a: Set<string>, b: Set<string>) => Array.from(a).every((t) => b.has(t));

/** Same club? Exact, or one name's tokens inside the other's ("brighton" ⊂ "brighton and hove albion"). */
function sameClub(ours: string, theirs: string): boolean {
  if (ours === theirs) return true;
  const a = tokens(ours);
  const b = tokens(theirs);
  if (a.size === 0 || b.size === 0) return false;
  // A one-word name must be a real word of the other, not a generic like "united".
  const generic = new Set(['united', 'city', 'town', 'real', 'club', 'sporting', 'athletic', 'racing', 'deportivo']);
  const short = a.size <= b.size ? a : b;
  if (Array.from(short).every((t) => generic.has(t))) return false;
  return subset(a, b) || subset(b, a);
}

// ---------------------------------------------------------------- de-vig
function devig(outcomes: { name: string; price: number }[]): { name: string; p: number }[] | null {
  if (!outcomes || outcomes.length < 3) return null;
  const implied = outcomes.map((o) => ({ name: o.name, raw: o.price > 0 ? 1 / o.price : 0 }));
  const total = implied.reduce((s, o) => s + o.raw, 0);
  if (total <= 0) return null;
  return implied.map((o) => ({ name: o.name, p: o.raw / total }));
}

// ---------------------------------------------------------------- cache + budget
async function readCache(league: string): Promise<{ events: MarketEvent[]; age: number } | null> {
  await ensureTable();
  const rs = await predictionsDb.execute({
    sql: 'SELECT fetched_at, events_json FROM market_odds WHERE league = ?', args: [league],
  });
  const row = rs.rows[0] as unknown as { fetched_at: string; events_json: string } | undefined;
  if (!row) return null;
  return { events: JSON.parse(row.events_json), age: Date.now() - new Date(row.fetched_at).getTime() };
}

async function writeCache(league: string, events: MarketEvent[], remaining: number | null): Promise<void> {
  await predictionsDb.execute({
    sql: `INSERT INTO market_odds (league, fetched_at, events_json, remaining) VALUES (?,?,?,?)
          ON CONFLICT(league) DO UPDATE SET fetched_at = excluded.fetched_at,
            events_json = excluded.events_json, remaining = excluded.remaining`,
    args: [league, new Date().toISOString(), JSON.stringify(events), remaining],
  });
}

const today = () => new Date().toISOString().slice(0, 10);

/** How many fetches today, and the last known credits-remaining. */
async function budget(): Promise<{ used: number; remaining: number | null }> {
  await ensureTable();
  const rs = await predictionsDb.execute({
    sql: 'SELECT fetched_at, events_json, remaining FROM market_odds WHERE league = ?', args: [BUDGET_KEY],
  });
  const row = rs.rows[0] as unknown as { fetched_at: string; events_json: string; remaining: number | null } | undefined;
  if (!row) return { used: 0, remaining: null };
  return { used: row.fetched_at === today() ? Number(row.events_json) : 0, remaining: row.remaining };
}

async function spend(used: number, remaining: number | null): Promise<void> {
  await predictionsDb.execute({
    sql: `INSERT INTO market_odds (league, fetched_at, events_json, remaining) VALUES (?,?,?,?)
          ON CONFLICT(league) DO UPDATE SET fetched_at = excluded.fetched_at,
            events_json = excluded.events_json, remaining = excluded.remaining`,
    args: [BUDGET_KEY, today(), String(used), remaining],
  });
}

// ---------------------------------------------------------------- fetch
async function fetchLeague(league: string, sportKey: string): Promise<{ events: MarketEvent[]; remaining: number | null } | null> {
  const key = process.env.THE_ODDS_API_KEY;
  if (!key) return null;
  try {
    const url = `https://api.the-odds-api.com/v4/sports/${sportKey}/odds?regions=eu&markets=h2h&oddsFormat=decimal&apiKey=${key}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(12_000) });
    const remaining = Number(resp.headers.get('x-requests-remaining') ?? NaN);
    if (!resp.ok) {
      console.error('[ODDS] upstream', league, resp.status, await resp.text().catch(() => ''));
      return { events: [], remaining: Number.isFinite(remaining) ? remaining : null };
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any[] = await resp.json();
    const events: MarketEvent[] = [];
    for (const ev of data) {
      const home = norm(ev.home_team);
      const away = norm(ev.away_team);
      let h = 0, d = 0, a = 0, books = 0;
      for (const bk of ev.bookmakers || []) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const market = (bk.markets || []).find((m: any) => m.key === 'h2h');
        const dv = market && devig(market.outcomes);
        if (!dv) continue;
        let hp = 0, dp = 0, ap = 0;
        for (const o of dv) {
          if (o.name.toLowerCase() === 'draw') dp = o.p;
          else if (norm(o.name) === home) hp = o.p;
          else if (norm(o.name) === away) ap = o.p;
        }
        if (hp && dp && ap) { h += hp; d += dp; a += ap; books++; }
      }
      if (books > 0) {
        events.push({ home, away, commence_time: ev.commence_time,
                      probs: { home: h / books, draw: d / books, away: a / books, books } });
      }
    }
    console.log(`[ODDS] ${league}: ${events.length} events (credits remaining: ${remaining})`);
    return { events, remaining: Number.isFinite(remaining) ? remaining : null };
  } catch (e) {
    console.error('[ODDS] fetch failed:', league, e instanceof Error ? e.message : e);
    return null;
  }
}

/**
 * Consensus market per fixture, for a whole slate, spending as little as
 * possible: cached leagues are served from Turso, and only a league with a
 * fixture in the next 48h and a stale cache costs a credit.
 */
export async function getMarketForFixtures(fixtures: FixtureRow[]): Promise<Map<string, Market>> {
  const out = new Map<string, Market>();
  if (fixtures.length === 0) return out;
  const hasKey = Boolean(process.env.THE_ODDS_API_KEY);

  const soon = Date.now() + LOOKAHEAD_MS;
  const byLeague = new Map<string, FixtureRow[]>();
  for (const f of fixtures) {
    if (!(f.league in SPORT_KEYS) || !SPORT_KEYS[f.league]) continue;
    (byLeague.get(f.league) ?? byLeague.set(f.league, []).get(f.league)!).push(f);
  }

  let { used, remaining } = await budget().catch(() => ({ used: 0, remaining: null as number | null }));

  for (const [league, rows] of byLeague) {
    let cached: { events: MarketEvent[]; age: number } | null = null;
    try { cached = await readCache(league); } catch (e) { console.error('[ODDS] cache read', e); }

    const wantsFetch = hasKey
      && (!cached || cached.age > TTL_MS)
      && rows.some((f) => new Date(f.kickoff_utc).getTime() < soon)
      && used < DAILY_CAP
      && (remaining === null || remaining > RESERVE);

    let events = cached?.events ?? [];
    if (wantsFetch) {
      const fresh = await fetchLeague(league, SPORT_KEYS[league]!);
      used++;
      if (fresh) {
        remaining = fresh.remaining ?? remaining;
        if (fresh.events.length) events = fresh.events;
        try { await writeCache(league, events, remaining); } catch (e) { console.error('[ODDS] cache write', e); }
      }
      try { await spend(used, remaining); } catch { /* budget row is advisory */ }
    }

    for (const f of rows) {
      const h = norm(f.home_team);
      const a = norm(f.away_team);
      const hits = events.filter((ev) => sameClub(h, ev.home) && sameClub(a, ev.away));
      if (hits.length === 1) out.set(f.match_key, hits[0].probs);
    }
  }
  return out;
}

/** One fixture — the detail view. */
export async function getMarketProbs(homeTeam: string, awayTeam: string, league: string, kickoffUtc: string): Promise<Market | null> {
  const m = await getMarketForFixtures([{
    match_key: '_', home_team: homeTeam, away_team: awayTeam, league, kickoff_utc: kickoffUtc,
  } as FixtureRow]);
  return m.get('_') ?? null;
}

/** Blend model probabilities with market consensus (renormalised). */
export function blend(model: Probs, market: Probs, modelWeight = DEFAULT_MODEL_WEIGHT): Probs {
  const w = Math.min(1, Math.max(0, modelWeight));
  const h = w * model.home + (1 - w) * market.home;
  const d = w * model.draw + (1 - w) * market.draw;
  const a = w * model.away + (1 - w) * market.away;
  const t = h + d + a || 1;
  return { home: h / t, draw: d / t, away: a / t };
}

/**
 * What the dashboard shows. A model that already consumed the odds as features
 * (`usedOdds`) is shown as-is — blending the market in a second time would
 * just drag it toward the bookmakers. An older prediction made without odds
 * still gets the blend.
 */
export function applyMarket(model: Probs, market: Market | null, usedOdds: boolean): Probs {
  if (!market || usedOdds) return model;
  const mw = Number(process.env.ODDS_MODEL_WEIGHT ?? DEFAULT_MODEL_WEIGHT);
  return blend(model, market, mw);
}
