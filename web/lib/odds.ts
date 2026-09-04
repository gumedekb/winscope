/**
 * Market odds integration (The Odds API).
 *
 * Pulls live FIFA World Cup 1X2 (h2h) odds across many bookmakers, removes the
 * bookmaker margin ("de-vig"), averages into a consensus implied probability, and
 * exposes a helper to blend that market signal into the model's probabilities.
 *
 * The market is the single sharpest public predictor, so for WC fixtures where odds
 * exist we nudge the model toward it. One upstream call returns every upcoming WC
 * match, so we cache aggressively to stay inside the free 500-credits/month budget.
 */

// TODO(odds): The Odds API uses per-league keys (soccer_epl, soccer_spain_la_liga, ...).
// Map each league to its key; single default for now. See [[03 - Data Sources]].
const SPORT_KEY = process.env.ODDS_SPORT_KEY || 'soccer_epl';
const CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6h -> ~4 calls/day worst case
const DEFAULT_MODEL_WEIGHT = 0.45; // 0 = pure market, 1 = pure model

export interface Probs { home: number; draw: number; away: number }

interface MarketEvent {
  teams: Set<string>;           // normalized team names in the fixture
  probByTeam: Record<string, number>;
  draw: number;
  books: number;
  commence_time: string;
}

let cache: { ts: number; events: MarketEvent[] } | null = null;

// --- team-name normalization so The Odds API names meet our fixture names ---
const ALIASES: Record<string, string> = {
  'united states': 'usa', 'united states of america': 'usa', 'usa': 'usa',
  'korea republic': 'south korea', 'south korea': 'south korea',
  'ir iran': 'iran', 'iran': 'iran',
  'turkiye': 'turkey', 'turkey': 'turkey',
  'cote divoire': 'ivory coast', 'ivory coast': 'ivory coast',
  'czechia': 'czech republic', 'czech republic': 'czech republic',
  'congo dr': 'dr congo', 'dr congo': 'dr congo',
};

function norm(name: string): string {
  if (!name) return '';
  let s = name.normalize('NFKD').replace(/[̀-ͯ]/g, ''); // strip accents
  s = s.toLowerCase().replace(/\bfc\b|\bcf\b|\bsc\b/g, '').replace(/[^a-z\s]/g, '').trim();
  s = s.replace(/\s+/g, ' ');
  return ALIASES[s] || s;
}

/** De-vig one bookmaker's 3-way prices into normalized probabilities. */
function devig(outcomes: { name: string; price: number }[]): { name: string; p: number }[] | null {
  if (!outcomes || outcomes.length < 3) return null;
  const implied = outcomes.map(o => ({ name: o.name, raw: o.price > 0 ? 1 / o.price : 0 }));
  const total = implied.reduce((s, o) => s + o.raw, 0);
  if (total <= 0) return null;
  return implied.map(o => ({ name: o.name, p: o.raw / total }));
}

async function fetchWorldCupOdds(): Promise<MarketEvent[]> {
  if (cache && Date.now() - cache.ts < CACHE_TTL_MS) return cache.events;

  const key = process.env.THE_ODDS_API_KEY;
  if (!key) return cache?.events || [];

  try {
    const url = `https://api.the-odds-api.com/v4/sports/${SPORT_KEY}/odds?regions=eu&markets=h2h&oddsFormat=decimal&apiKey=${key}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(12000) });
    if (!resp.ok) {
      console.error('[ODDS] upstream', resp.status, await resp.text().catch(() => ''));
      return cache?.events || [];
    }
    const data: any[] = await resp.json();
    const remaining = resp.headers.get('x-requests-remaining');
    console.log(`[ODDS] fetched ${data.length} WC events (credits remaining: ${remaining})`);

    const events: MarketEvent[] = data.map(ev => {
      const homeN = norm(ev.home_team);
      const awayN = norm(ev.away_team);
      const acc: Record<string, number> = {};
      let drawSum = 0;
      let books = 0;
      for (const bk of ev.bookmakers || []) {
        const market = (bk.markets || []).find((m: any) => m.key === 'h2h');
        const dv = market && devig(market.outcomes);
        if (!dv) continue;
        let homeP = 0, awayP = 0, drawP = 0;
        for (const o of dv) {
          const n = norm(o.name);
          if (o.name.toLowerCase() === 'draw') drawP = o.p;
          else if (n === homeN) homeP = o.p;
          else if (n === awayN) awayP = o.p;
        }
        if (homeP && awayP && drawP) {
          acc[homeN] = (acc[homeN] || 0) + homeP;
          acc[awayN] = (acc[awayN] || 0) + awayP;
          drawSum += drawP;
          books++;
        }
      }
      const probByTeam: Record<string, number> = {};
      if (books > 0) {
        probByTeam[homeN] = acc[homeN] / books;
        probByTeam[awayN] = acc[awayN] / books;
      }
      return {
        teams: new Set([homeN, awayN]),
        probByTeam,
        draw: books > 0 ? drawSum / books : 0,
        books,
        commence_time: ev.commence_time,
      };
    }).filter(e => e.books > 0);

    cache = { ts: Date.now(), events };
    return events;
  } catch (e: any) {
    console.error('[ODDS] fetch failed:', e?.message || e);
    return cache?.events || [];
  }
}

/** Consensus de-vigged market probabilities for a specific fixture, or null. */
export async function getMarketProbs(homeTeam: string, awayTeam: string): Promise<(Probs & { books: number }) | null> {
  const h = norm(homeTeam);
  const a = norm(awayTeam);
  if (!h || !a) return null;
  const events = await fetchWorldCupOdds();
  const ev = events.find(e => e.teams.has(h) && e.teams.has(a) && e.teams.size === 2);
  if (!ev) return null;
  const home = ev.probByTeam[h];
  const away = ev.probByTeam[a];
  if (home == null || away == null) return null;
  return { home, draw: ev.draw, away, books: ev.books };
}

/** Blend model probabilities with market consensus (renormalized). */
export function blend(model: Probs, market: Probs, modelWeight = DEFAULT_MODEL_WEIGHT): Probs {
  const w = Math.min(1, Math.max(0, modelWeight));
  const h = w * model.home + (1 - w) * market.home;
  const d = w * model.draw + (1 - w) * market.draw;
  const a = w * model.away + (1 - w) * market.away;
  const t = h + d + a || 1;
  return { home: h / t, draw: d / t, away: a / t };
}

/**
 * Convenience: given model probs for a WC fixture, return blended probs + the raw
 * market consensus. Falls back to the model untouched when no odds are available.
 */
export async function applyMarket(
  homeTeam: string,
  awayTeam: string,
  model: Probs,
): Promise<{ blended: Probs; market: (Probs & { books: number }) | null }> {
  const market = await getMarketProbs(homeTeam, awayTeam);
  if (!market) return { blended: model, market: null };
  const mw = Number(process.env.ODDS_MODEL_WEIGHT ?? DEFAULT_MODEL_WEIGHT);
  return { blended: blend(model, market, mw), market };
}
