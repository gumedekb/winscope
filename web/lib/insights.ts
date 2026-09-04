import { predictionsDb } from './db';
import type { BetTier } from './betTiers';
import type { InsightPayload } from './ai';

/**
 * AI-insight cache, in Turso.
 *
 * Why Turso and not a local better-sqlite3 file: this app deploys to Vercel,
 * where the filesystem is ephemeral and not shared between lambda instances. A
 * local SQLite cache would be wiped on every cold start and invisible to the
 * next instance, so the same match would regenerate again and again — precisely
 * the free-tier burn we are trying to avoid. Turso also means an insight
 * generated on the desktop is already there on the phone.
 *
 * One Turso read (~50ms) versus one Gemini call (~3-6s, and a metered request)
 * is not a close call.
 */

export interface StoredInsight extends InsightPayload {
  match_key: string;
  tiers: BetTier[];
  provider: string;
  model: string;
  created_at: string;
  updated_at: string;
  /** How many times it has been regenerated — visible in the panel. */
  refresh_count: number;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS ai_insights (
    match_key      TEXT PRIMARY KEY,
    summary        TEXT NOT NULL,
    key_factors    TEXT NOT NULL,   -- JSON array
    tiers          TEXT NOT NULL,   -- JSON array of BetTier, rationale included
    model_commentary TEXT,
    confidence_notes TEXT,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    refresh_count  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
)`;

let ready: Promise<void> | null = null;

export function ensureInsightsTable(): Promise<void> {
  if (!ready) {
    ready = predictionsDb
      .execute(SCHEMA)
      .then(() => undefined)
      .catch((e) => {
        ready = null;                 // let the next request retry
        throw e;
      });
  }
  return ready;
}

const parseArray = <T,>(raw: unknown, fallback: T[]): T[] => {
  try {
    const v = JSON.parse(String(raw ?? '[]'));
    return Array.isArray(v) ? (v as T[]) : fallback;
  } catch {
    return fallback;
  }
};

export async function getInsight(matchKey: string): Promise<StoredInsight | null> {
  await ensureInsightsTable();
  const rs = await predictionsDb.execute({
    sql: 'SELECT * FROM ai_insights WHERE match_key = ?',
    args: [matchKey],
  });
  const r = rs.rows[0] as unknown as Record<string, unknown> | undefined;
  if (!r) return null;
  return {
    match_key: String(r.match_key),
    summary: String(r.summary ?? ''),
    key_factors: parseArray<string>(r.key_factors, []),
    tiers: parseArray<BetTier>(r.tiers, []),
    tier_rationales: [],            // already folded into `tiers`
    model_commentary: String(r.model_commentary ?? ''),
    confidence_notes: String(r.confidence_notes ?? ''),
    provider: String(r.provider ?? ''),
    model: String(r.model ?? ''),
    refresh_count: Number(r.refresh_count ?? 0),
    created_at: String(r.created_at ?? ''),
    updated_at: String(r.updated_at ?? ''),
  };
}

export async function saveInsight(params: {
  matchKey: string;
  payload: InsightPayload;
  tiers: BetTier[];
  provider: string;
  model: string;
  isRefresh: boolean;
}): Promise<void> {
  await ensureInsightsTable();
  const now = new Date().toISOString();
  await predictionsDb.execute({
    sql: `INSERT INTO ai_insights
            (match_key, summary, key_factors, tiers, model_commentary,
             confidence_notes, provider, model, refresh_count, created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(match_key) DO UPDATE SET
            summary          = excluded.summary,
            key_factors      = excluded.key_factors,
            tiers            = excluded.tiers,
            model_commentary = excluded.model_commentary,
            confidence_notes = excluded.confidence_notes,
            provider         = excluded.provider,
            model            = excluded.model,
            -- created_at is the FIRST generation and never moves
            refresh_count    = ai_insights.refresh_count + 1,
            updated_at       = excluded.updated_at`,
    args: [
      params.matchKey,
      params.payload.summary,
      JSON.stringify(params.payload.key_factors),
      JSON.stringify(params.tiers),
      params.payload.model_commentary,
      params.payload.confidence_notes,
      params.provider,
      params.model,
      0,
      now,
      now,
    ],
  });
}

/** Which of a set of matches already have a cached insight — one round trip. */
export async function insightKeys(matchKeys: string[]): Promise<Set<string>> {
  const found = new Set<string>();
  if (matchKeys.length === 0) return found;
  await ensureInsightsTable();
  for (let i = 0; i < matchKeys.length; i += 100) {
    const chunk = matchKeys.slice(i, i + 100);
    const rs = await predictionsDb.execute({
      sql: `SELECT match_key FROM ai_insights WHERE match_key IN (${chunk.map(() => '?').join(',')})`,
      args: chunk,
    });
    for (const row of rs.rows as unknown as Record<string, unknown>[]) {
      found.add(String(row.match_key));
    }
  }
  return found;
}
