import axios from 'axios';
import type {
  AiInsight, BetslipResponse, FixturesResponse, History, Match, Prediction,
} from './types';

/**
 * Frontend API client. Backend routes live at the same origin in this combined
 * Next.js app, so the base URL is just "/api".
 *
 * Note what is NOT here: any football-data provider. Fixtures come from Turso,
 * written by the data/ ETL, which is the only thing that talks to those APIs —
 * it caps each at 90% of its free tier and a second caller would break that.
 */
const client = axios.create({ baseURL: '/api' });

export const api = {
  /** Live + upcoming fixtures with predictions, plus data-freshness stats. */
  getFixtures: (league?: string) =>
    client
      .get<FixturesResponse>('/fixtures', {
        params: league && league !== 'all' ? { league } : undefined,
      })
      .then((r) => r.data),

  /** What is currently in Turso, and whether it has gone stale. */
  refresh: () => client.post('/sync').then((r) => r.data),

  getHistory: (range?: { from?: string; to?: string }) =>
    client.get<History>('/history', { params: range }).then((r) => r.data),

  /** Full detail for one fixture: form, market blend, AI read. */
  getPrediction: async (match: Match, forceRefresh = false): Promise<Prediction> => {
    const res = await client.post('/predict', {
      match_key: match.matchKey ?? match.id,
      force_refresh: forceRefresh,
    });
    const p = res.data;
    return {
      home_win: p.home_win ?? 0,
      draw: p.draw ?? 0,
      away_win: p.away_win ?? 0,
      outcome: p.outcome,
      model_probs: p.model_probs,
      market_probs: p.market_probs ?? null,
      home_elo: p.home_elo,
      away_elo: p.away_elo,
      home_form: p.home_form ?? [],
      away_form: p.away_form ?? [],
      home_team_history: p.home_team_history ?? [],
      away_team_history: p.away_team_history ?? [],
      ai_analysis: p.ai_analysis ?? null,
      model_version: p.model_version,
      coverage: p.coverage ?? null,
    };
  },

  /**
   * Read the CACHED insight for a match. Never calls an AI provider — a 204
   * simply means one has not been generated yet.
   */
  getCachedInsight: async (matchKey: string): Promise<AiInsight | null> => {
    const res = await client.get('/insight', {
      params: { match_key: matchKey },
      validateStatus: (s) => s === 200 || s === 204,
    });
    return res.status === 204 ? null : (res.data.insight as AiInsight);
  },

  /**
   * Generate an insight. Returns the cache unless `refresh` is true, so this is
   * safe to call on a click; only a refresh actually spends a free-tier call.
   */
  generateInsight: async (matchKey: string, refresh = false): Promise<AiInsight> => {
    try {
      const res = await client.post('/insight', { match_key: matchKey, refresh });
      return res.data.insight as AiInsight;
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } } };
      throw new Error(err.response?.data?.error || 'AI request failed');
    }
  },

  // --- betslip: one list per day, synced through Turso ---------------------
  getBetslip: (date?: string) =>
    client.get<BetslipResponse>('/betslip', { params: date ? { date } : undefined })
      .then((r) => r.data),

  addToBetslip: (match: Match, pick: number, date?: string) =>
    client.post<BetslipResponse>('/betslip', {
      action: 'add',
      date,
      match_key: match.matchKey ?? match.id,
      league: match.competition,
      kickoff_utc: match.utcDate,
      home_team: match.homeTeam?.name,
      away_team: match.awayTeam?.name,
      pick,
      prob_home: match.prediction?.home_win ?? 0,
      prob_draw: match.prediction?.draw ?? 0,
      prob_away: match.prediction?.away_win ?? 0,
    }).then((r) => r.data),

  removeFromBetslip: (matchKey: string, date?: string) =>
    client.post<BetslipResponse>('/betslip', { action: 'remove', date, match_key: matchKey })
      .then((r) => r.data),

  moveInBetslip: (matchKey: string, direction: 'up' | 'down', date?: string) =>
    client.post<BetslipResponse>('/betslip', { action: 'move', date, match_key: matchKey, direction })
      .then((r) => r.data)
};
