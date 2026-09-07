import { fixturesDb, predictionsDb } from './db';

/**
 * Read/write layer over the two Turso tables.
 *
 *   fixtures    — owned by the data/ ETL. This app NEVER writes to it and never
 *                 calls a football API itself: the ETL caps every provider at
 *                 90% of its free tier, and a second uncapped caller would blow
 *                 straight through that. One writer, one budget.
 *   predictions — owned by this app, keyed on the same `match_key` the ETL and
 *                 model_data.csv use, so scoring a prediction against its real
 *                 result is a plain join.
 */

export type StatusGroup = 'scheduled' | 'in_play' | 'finished' | 'off';

export interface FixtureRow {
  match_key: string;
  kickoff_utc: string;
  status: string;
  status_group: StatusGroup;
  minute: number | null;
  league: string;
  country: string | null;
  season: string | null;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  outcome: number | null;      // 1 home, 2 draw, 3 away — null until finished
  venue: string | null;
  source: string | null;
  updated_at: string;
  finished_at: string | null;
}

export interface PredictionRow {
  match_key: string;
  home_win: number;
  draw: number;
  away_win: number;
  predicted_outcome: number;   // 1 | 2 | 3, same encoding as fixtures.outcome
  market_home: number | null;
  market_draw: number | null;
  market_away: number | null;
  ai_summary: string | null;
  payload_json: string | null;
  updated_at: string;
}

const PREDICTIONS_SCHEMA = `
CREATE TABLE IF NOT EXISTS predictions (
    match_key         TEXT PRIMARY KEY,
    home_win          REAL NOT NULL,
    draw              REAL NOT NULL,
    away_win          REAL NOT NULL,
    predicted_outcome INTEGER NOT NULL,
    market_home       REAL,
    market_draw       REAL,
    market_away       REAL,
    ai_summary        TEXT,
    payload_json      TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
)`;

let schemaReady: Promise<void> | null = null;

/** Created once per process, not per request. */
export function ensurePredictionsTable(): Promise<void> {
  if (!schemaReady) {
    schemaReady = (async () => {
      await predictionsDb.execute(PREDICTIONS_SCHEMA);
      await predictionsDb.execute(
        'CREATE INDEX IF NOT EXISTS idx_predictions_updated ON predictions(updated_at)'
      );
    })().catch((e) => {
      schemaReady = null;              // let a later request retry
      throw e;
    });
  }
  return schemaReady;
}

const num = (v: unknown): number | null =>
  v === null || v === undefined || v === '' ? null : Number(v);

const toFixture = (r: Record<string, unknown>): FixtureRow => ({
  match_key: String(r.match_key),
  kickoff_utc: String(r.kickoff_utc),
  status: String(r.status),
  status_group: String(r.status_group) as StatusGroup,
  minute: num(r.minute),
  league: String(r.league),
  country: r.country ? String(r.country) : null,
  season: r.season ? String(r.season) : null,
  home_team: String(r.home_team),
  away_team: String(r.away_team),
  home_score: num(r.home_score),
  away_score: num(r.away_score),
  outcome: num(r.outcome),
  venue: r.venue ? String(r.venue) : null,
  source: r.source ? String(r.source) : null,
  updated_at: String(r.updated_at ?? ''),
  finished_at: r.finished_at ? String(r.finished_at) : null,
});

/** Everything not yet finished: live first, then by kickoff. */
export async function getUpcoming(limit = 300): Promise<FixtureRow[]> {
  const rs = await fixturesDb.execute({
    sql: `SELECT * FROM fixtures
          WHERE status_group IN ('scheduled', 'in_play', 'off')
          ORDER BY CASE status_group WHEN 'in_play' THEN 0 ELSE 1 END,
                   kickoff_utc ASC
          LIMIT ?`,
    args: [limit],
  });
  return (rs.rows as unknown as Record<string, unknown>[]).map(toFixture);
}

export async function getFinished(limit = 500): Promise<FixtureRow[]> {
  const rs = await fixturesDb.execute({
    sql: `SELECT * FROM fixtures
          WHERE status_group = 'finished'
          ORDER BY kickoff_utc DESC
          LIMIT ?`,
    args: [limit],
  });
  return (rs.rows as unknown as Record<string, unknown>[]).map(toFixture);
}

/** A team's most recent finished matches — the basis of the form strip. */
export async function getTeamRecent(team: string, count = 5): Promise<FixtureRow[]> {
  const rs = await fixturesDb.execute({
    sql: `SELECT * FROM fixtures
          WHERE status_group = 'finished' AND (home_team = ? OR away_team = ?)
          ORDER BY kickoff_utc DESC LIMIT ?`,
    args: [team, team, count],
  });
  return (rs.rows as unknown as Record<string, unknown>[]).map(toFixture);
}

/** W/D/L from one team's point of view, newest first. */
export function formLetters(team: string, rows: FixtureRow[]): ('W' | 'D' | 'L')[] {
  return rows
    .filter((r) => r.outcome !== null)
    .map((r) => {
      if (r.outcome === 2) return 'D' as const;
      const homeWon = r.outcome === 1;
      const isHome = r.home_team === team;
      return (homeWon === isHome ? 'W' : 'L') as 'W' | 'L';
    });
}

/**
 * Last-N form for every club at once, from a single list of finished matches.
 *
 * Note this only reflects results the ETL has published to Turso — it keeps
 * finished matches forever, so the strips lengthen on their own as the cron
 * runs; the deep history (30k matches) lives in model_data.csv and is what the
 * model itself was trained on.
 */
export function buildFormIndex(
  finished: FixtureRow[],
  count = 5
): Map<string, ('W' | 'D' | 'L')[]> {
  const byTeam = new Map<string, FixtureRow[]>();
  // `finished` arrives newest-first, so pushing preserves that order.
  for (const row of finished) {
    for (const team of [row.home_team, row.away_team]) {
      const list = byTeam.get(team);
      if (list) {
        if (list.length < count) list.push(row);
      } else {
        byTeam.set(team, [row]);
      }
    }
  }
  const out = new Map<string, ('W' | 'D' | 'L')[]>();
  for (const [team, rows] of byTeam) out.set(team, formLetters(team, rows));
  return out;
}

/**
 * Club badges, keyed on our canonical club name — the same key the fixtures and
 * the model use, so no id mapping is needed anywhere.
 *
 * Written by `data/badges.py` (TheSportsDB, fetched once). Missing rows are
 * normal and not an error: the UI falls back to a generated initials badge, so
 * a club without a crest still renders.
 */
export async function getTeamBadges(): Promise<Map<string, string>> {
  const badges = new Map<string, string>();
  try {
    const rs = await fixturesDb.execute(
      'SELECT team, badge_url FROM team_assets WHERE badge_url IS NOT NULL'
    );
    for (const row of rs.rows as unknown as Record<string, unknown>[]) {
      badges.set(String(row.team), String(row.badge_url));
    }
  } catch {
    // Table not created yet (badges.py never run) — initials everywhere.
  }
  return badges;
}

export interface Freshness {
  total: number;
  scheduled: number;
  inPlay: number;
  finished: number;
  leagues: string[];
  lastUpdated: string | null;
  nextKickoff: string | null;
  /** Rows whose kickoff has passed but which the ETL never advanced. */
  stranded: number;
}

/** Header stats — one round trip, so the dashboard can show whether data is stale. */
export async function getFreshness(): Promise<Freshness> {
  // `next_kickoff` must be in the FUTURE. Some fixtures never advance past
  // `scheduled` — a league no live source covers gets seeded once and is then
  // never revisited — so the earliest scheduled kickoff drifts into the past and
  // the header ends up advertising a match that started days ago. Ask only for
  // kickoffs from now on, and the stat degrades to null instead of lying.
  const now = new Date().toISOString();
  const [groups, meta] = await Promise.all([
    fixturesDb.execute(
      'SELECT status_group, COUNT(*) AS n FROM fixtures GROUP BY status_group'
    ),
    fixturesDb.execute({
      sql: `SELECT MAX(updated_at) AS last_updated,
                   MIN(CASE WHEN status_group = 'scheduled' AND kickoff_utc >= ?
                            THEN kickoff_utc END) AS next_kickoff
            FROM fixtures`,
      args: [now],
    }),
  ]);

  const tally: Record<string, number> = {};
  for (const row of groups.rows as unknown as Record<string, unknown>[]) {
    tally[String(row.status_group)] = Number(row.n);
  }
  const m = (meta.rows[0] ?? {}) as Record<string, unknown>;

  // Kicked off, but still sitting at `scheduled` or `in_play`. A non-zero count
  // means the ETL is behind or a league has no live source at all.
  const strandedRs = await fixturesDb.execute({
    sql: `SELECT COUNT(*) AS n FROM fixtures
          WHERE status_group IN ('scheduled', 'in_play') AND kickoff_utc < ?`,
    args: [now],
  });

  const leagueRs = await fixturesDb.execute(
    `SELECT DISTINCT league FROM fixtures
     WHERE status_group IN ('scheduled', 'in_play', 'off') ORDER BY league`
  );

  return {
    total: Object.values(tally).reduce((a, b) => a + b, 0),
    scheduled: tally.scheduled ?? 0,
    inPlay: tally.in_play ?? 0,
    finished: tally.finished ?? 0,
    leagues: (leagueRs.rows as unknown as Record<string, unknown>[]).map((r) => String(r.league)),
    lastUpdated: m.last_updated ? String(m.last_updated) : null,
    nextKickoff: m.next_kickoff ? String(m.next_kickoff) : null,
    stranded: Number((strandedRs.rows[0] as unknown as Record<string, unknown>)?.n ?? 0),
  };
}

export async function getPredictions(keys: string[]): Promise<Map<string, PredictionRow>> {
  const out = new Map<string, PredictionRow>();
  if (keys.length === 0) return out;
  await ensurePredictionsTable();

  // Chunked so a big fixture list cannot build an oversized SQL statement.
  for (let i = 0; i < keys.length; i += 100) {
    const chunk = keys.slice(i, i + 100);
    const rs = await predictionsDb.execute({
      sql: `SELECT * FROM predictions WHERE match_key IN (${chunk.map(() => '?').join(',')})`,
      args: chunk,
    });
    for (const row of rs.rows as unknown as Record<string, unknown>[]) {
      out.set(String(row.match_key), {
        match_key: String(row.match_key),
        home_win: Number(row.home_win),
        draw: Number(row.draw),
        away_win: Number(row.away_win),
        predicted_outcome: Number(row.predicted_outcome),
        market_home: num(row.market_home),
        market_draw: num(row.market_draw),
        market_away: num(row.market_away),
        ai_summary: row.ai_summary ? String(row.ai_summary) : null,
        payload_json: row.payload_json ? String(row.payload_json) : null,
        updated_at: String(row.updated_at ?? ''),
      });
    }
  }
  return out;
}

export interface SavePrediction {
  matchKey: string;
  homeWin: number;
  draw: number;
  awayWin: number;
  market?: { home: number; draw: number; away: number } | null;
  aiSummary?: string | null;
  payload?: unknown;
}

/** Which of the three the model actually calls. Ties fall to the higher class. */
export const argmaxOutcome = (h: number, d: number, a: number): number =>
  h >= d && h >= a ? 1 : d >= a ? 2 : 3;

export async function savePrediction(p: SavePrediction): Promise<void> {
  await ensurePredictionsTable();
  const now = new Date().toISOString();
  await predictionsDb.execute({
    sql: `INSERT INTO predictions
            (match_key, home_win, draw, away_win, predicted_outcome,
             market_home, market_draw, market_away, ai_summary, payload_json,
             created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(match_key) DO UPDATE SET
            home_win = excluded.home_win,
            draw = excluded.draw,
            away_win = excluded.away_win,
            predicted_outcome = excluded.predicted_outcome,
            market_home = COALESCE(excluded.market_home, predictions.market_home),
            market_draw = COALESCE(excluded.market_draw, predictions.market_draw),
            market_away = COALESCE(excluded.market_away, predictions.market_away),
            ai_summary  = COALESCE(excluded.ai_summary,  predictions.ai_summary),
            payload_json= COALESCE(excluded.payload_json,predictions.payload_json),
            updated_at  = excluded.updated_at`,
    args: [
      p.matchKey, p.homeWin, p.draw, p.awayWin,
      argmaxOutcome(p.homeWin, p.draw, p.awayWin),
      p.market?.home ?? null, p.market?.draw ?? null, p.market?.away ?? null,
      p.aiSummary ?? null,
      p.payload ? JSON.stringify(p.payload) : null,
      now, now,
    ],
  });
}

export interface ScoredMatch extends FixtureRow {
  predicted_outcome: number | null;
  probs: { h: number; d: number; a: number } | null;
  market: { h: number; d: number; a: number } | null;
  hit: boolean | null;
}

/**
 * The track record: every finished match joined to what we predicted BEFORE it.
 * This is the whole reason the ETL never deletes a finished fixture.
 */
export async function getScoredHistory(limit = 500): Promise<ScoredMatch[]> {
  await ensurePredictionsTable();
  const rs = await fixturesDb.execute({
    sql: `SELECT f.*,
                 p.home_win, p.draw, p.away_win, p.predicted_outcome,
                 p.market_home, p.market_draw, p.market_away
          FROM fixtures f
          LEFT JOIN predictions p ON p.match_key = f.match_key
          WHERE f.status_group = 'finished' AND f.outcome IS NOT NULL
          ORDER BY f.kickoff_utc DESC
          LIMIT ?`,
    args: [limit],
  });

  return (rs.rows as unknown as Record<string, unknown>[]).map((r) => {
    const base = toFixture(r);
    const predicted = num(r.predicted_outcome);
    const probs = r.home_win === null || r.home_win === undefined
      ? null
      : { h: Number(r.home_win), d: Number(r.draw), a: Number(r.away_win) };
    const market = r.market_home === null || r.market_home === undefined
      ? null
      : { h: Number(r.market_home), d: Number(r.market_draw), a: Number(r.market_away) };
    return {
      ...base,
      predicted_outcome: predicted,
      probs,
      market,
      hit: predicted === null ? null : predicted === base.outcome,
    };
  });
}
