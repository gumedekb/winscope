import { argmaxOutcome, savePrediction, type FixtureRow, type PredictionRow } from './fixtures';
import { config } from './config';
import type { Market } from './odds';

/**
 * Predict a whole slate in ONE call and store what comes back.
 *
 * Shared by the dashboard's fixture list (which fills gaps on every page view)
 * and /api/predict/backfill (which the ETL calls every pass, so gaps get filled
 * even when nobody opens the page). The model server builds a single DMatrix
 * for the batch, so ~90 fixtures come back in well under a second once it is
 * awake.
 *
 * "Once it is awake" is the catch. The model runs on Render's free tier, which
 * sleeps after 15 idle minutes and takes ~50s to come back. A call that lands
 * in that window fails as a whole — no fixture in the batch gets a prediction —
 * and a fixture that kicks off before the next successful call never gets one.
 * That, and club names the model did not recognise, is why finished matches
 * turn up in the track record marked NOT PREDICTED. The backfill route is the
 * fix for the first; the ETL's alias table and the model's default-feature
 * fallback for unknown clubs are the fix for the second.
 */

export interface ModelPrediction {
  index: number;
  home_win: number;
  draw: number;
  away_win: number;
  home_elo?: number | null;
  away_elo?: number | null;
  home_form?: ('W' | 'D' | 'L')[];
  away_form?: ('W' | 'D' | 'L')[];
  coverage?: { defaults_used?: number; home_known?: boolean; away_known?: boolean };
  /** The model consumed this fixture's market odds as features (see lib/odds.ts). */
  used_odds?: boolean;
}

export interface ModelFailure {
  index: number;
  home_team: string;
  away_team: string;
  error: string;
}

export interface SlateResult {
  predictions: Map<string, ModelPrediction>;
  /** Fixtures the model refused, by match_key, with its reason. */
  failed: Map<string, ModelFailure>;
  /** False when the server could not be reached at all (asleep, down, timed out). */
  reachable: boolean;
}

/**
 * What a page load can afford to wait. NOT enough for a Render cold start
 * (~50s) — deliberately: the dashboard should render without predictions rather
 * than hang. The backfill route, which nobody is watching, passes 55s.
 */
export const MODEL_TIMEOUT_MS = 30_000;

export async function predictSlate(
  fixtures: FixtureRow[],
  market: Map<string, Market> = new Map(),
  timeoutMs = MODEL_TIMEOUT_MS
): Promise<SlateResult> {
  const out: SlateResult = { predictions: new Map(), failed: new Map(), reachable: true };
  if (fixtures.length === 0) return out;
  try {
    const res = await fetch(`${config.modelServerUrl}/predict/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fixtures: fixtures.map((f) => {
          const m = market.get(f.match_key);
          return {
            home_team: f.home_team,
            away_team: f.away_team,
            league: f.league,
            kickoff: f.kickoff_utc,
            // Consumed only by a model trained with odds features; ignored otherwise.
            market: m ? { home: m.home, draw: m.draw, away: m.away } : undefined,
          };
        }),
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
      out.reachable = false;
      console.warn(`[MODEL] /predict/batch -> HTTP ${res.status}`);
      return out;
    }
    const data = await res.json();
    for (const p of (data.predictions ?? []) as ModelPrediction[]) {
      const fixture = fixtures[p.index];
      if (fixture) out.predictions.set(fixture.match_key, p);
    }
    for (const f of (data.failed ?? []) as ModelFailure[]) {
      const fixture = fixtures[f.index];
      if (fixture) out.failed.set(fixture.match_key, f);
    }
    if (out.failed.size) {
      // Surfaced, not swallowed: this is the list to grow team_aliases.json from.
      console.warn(
        `[MODEL] refused ${out.failed.size} fixture(s): ` +
        Array.from(out.failed.values()).map((f) => `${f.home_team} v ${f.away_team} (${f.error})`).join('; ')
      );
    }
  } catch (err) {
    out.reachable = false;
    console.warn('[MODEL] unreachable:', err instanceof Error ? err.message : err);
  }
  return out;
}

/**
 * Store a batch of fresh model predictions and return them in the shape the
 * `predictions` table hands back, so callers can treat fresh and stored alike.
 */
export async function storeSlate(
  fixtures: FixtureRow[],
  fresh: Map<string, ModelPrediction>,
  market: Map<string, Market> = new Map()
): Promise<Map<string, PredictionRow>> {
  const stored = new Map<string, PredictionRow>();
  for (const f of fixtures) {
    const p = fresh.get(f.match_key);
    if (!p) continue;
    // The pre-match market is snapshotted with the call: odds vanish after
    // kickoff, and the track record benchmarks against them.
    const m = market.get(f.match_key) ?? null;
    await savePrediction({
      matchKey: f.match_key,
      homeWin: p.home_win, draw: p.draw, awayWin: p.away_win,
      market: m ? { home: m.home, draw: m.draw, away: m.away } : null,
      payload: p,
    });
    stored.set(f.match_key, {
      match_key: f.match_key,
      home_win: p.home_win, draw: p.draw, away_win: p.away_win,
      predicted_outcome: argmaxOutcome(p.home_win, p.draw, p.away_win),
      market_home: m?.home ?? null, market_draw: m?.draw ?? null, market_away: m?.away ?? null,
      ai_summary: null, payload_json: JSON.stringify(p),
      updated_at: new Date().toISOString(),
    });
  }
  return stored;
}

/** Fixtures with both clubs named and no stored prediction yet. */
export function missingPredictions(
  fixtures: FixtureRow[],
  stored: Map<string, PredictionRow>
): FixtureRow[] {
  return fixtures.filter((f) => !stored.has(f.match_key) && f.home_team && f.away_team);
}
