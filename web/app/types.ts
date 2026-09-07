export interface Team {
  name: string;
  shortName: string;
}

/** Same vocabulary the ETL writes into Turso. */
export type StatusGroup = 'scheduled' | 'in_play' | 'finished' | 'off';

export interface Match {
  /** `YYYY-MM-DD|Home|Away` — the key shared with model_data.csv. */
  id: string;
  matchKey: string;
  utcDate: string;
  status: string;
  statusGroup: StatusGroup;
  /** ISO time the ETL last wrote this row — drives the staleness check. */
  updatedAt?: string;
  minute: number | null;
  competition: string;
  competitionId: string;
  country: string | null;
  season: string | null;
  venue: string | null;
  source: string | null;
  homeScore: number | null;
  awayScore: number | null;
  homeTeamId: number;
  awayTeamId: number;
  homeTeam?: Team;
  awayTeam?: Team;
  /** Club badge URL, or null to fall back to a generated initials badge. */
  homeCrest?: string | null;
  awayCrest?: string | null;
  /** Last-5 W/D/L, from results in Turso. Independent of the model server. */
  homeForm?: ('W' | 'D' | 'L')[];
  awayForm?: ('W' | 'D' | 'L')[];
  prediction?: Prediction | null;
}

export interface MarketProbs {
  home: number;
  draw: number;
  away: number;
  books: number;
}

export type TierName = 'low' | 'medium' | 'high';
export type MarketCode = '1' | 'X' | '2' | '1X' | 'X2' | '12';

/** One suggested bet. Every number is the model's; only `rationale` is the AI's. */
export interface BetTier {
  tier: TierName;
  label: string;
  market: MarketCode;
  selection: string;
  probability: number;
  impliedOdds: number;
  edge: number | null;
  rationale?: string;
}

/** A cached AI insight, as stored in Turso `ai_insights`. */
export interface AiInsight {
  match_key: string;
  summary: string;
  key_factors: string[];
  tiers: BetTier[];
  model_commentary: string;
  confidence_notes: string;
  provider: string;
  model: string;
  refresh_count: number;
  created_at: string;
  updated_at: string;
}

export interface AiAnalysis {
  summary: string;
  safe_predictions: string[];
  risky_predictions: string[];
  key_factors: string[];
  model_commentary: string;
  confidence_notes: string;
}

export interface RecentMatch {
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  match_date: string;
  competition: string;
  result?: 'W' | 'D' | 'L';
}

/** How much real club history backed a prediction (from the model server). */
export interface PredictionCoverage {
  home_known: boolean;
  away_known: boolean;
  league_known: boolean;
  h2h_matches: number;
  home_matches_played: number;
  away_matches_played: number;
  defaults_used: number;
}

export interface Prediction {
  home_win: number;
  draw: number;
  away_win: number;
  model_version?: string;
  coverage?: PredictionCoverage | null;
  outcome?: 'H' | 'D' | 'A';
  model_probs?: { home: number; draw: number; away: number };
  market_probs?: MarketProbs | null;
  home_elo?: number;
  away_elo?: number;
  home_form?: ('W' | 'D' | 'L')[];
  away_form?: ('W' | 'D' | 'L')[];
  home_team_history?: RecentMatch[];
  away_team_history?: RecentMatch[];
  ai_analysis?: AiAnalysis | null;
}

/** Header stats so the dashboard can show whether the ETL data is stale. */
export interface Freshness {
  total: number;
  scheduled: number;
  inPlay: number;
  finished: number;
  leagues: string[];
  lastUpdated: string | null;
  /** Earliest kickoff still in the future, or null if there is none. */
  nextKickoff: string | null;
  /** Kicked off but never advanced by the ETL — see lib/fixtures getFreshness. */
  stranded: number;
}

export interface FixturesResponse {
  matches: Match[];
  freshness: Freshness | null;
  error?: string;
}

export interface ScoredMatch {
  match_id: string;
  match_key: string;
  kickoff_utc: string;
  league: string;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  predicted_outcome: 'H' | 'D' | 'A' | 'N/A';
  actual_outcome: 'H' | 'D' | 'A' | 'N/A';
  probs: { h: number; d: number; a: number } | null;
  market: { h: number; d: number; a: number } | null;
  hit: boolean | null;
}

export interface HistorySummary {
  total: number;
  evaluated: number;
  correct: number;
  accuracy: number | null;
  avg_confidence_on_actual: number | null;
  benchmark: { matches: number; model_log_loss: number; market_log_loss: number } | null;
  /** What backing the bookmakers' favourite would have scored on the same matches. */
  market_accuracy: number | null;
  market_evaluated: number;
  range: { from: string | null; to: string | null };
}

export interface LeagueRecord {
  league: string;
  evaluated: number;
  correct: number;
  accuracy: number | null;
  market_evaluated: number;
  market_correct: number;
  market_accuracy: number | null;
}

export interface History {
  summary: HistorySummary;
  per_league: LeagueRecord[];
  matches: ScoredMatch[];
}


/** One row on a day's betslip, graded once the match finishes. */
export interface BetslipRow {
  date: string;
  match_key: string;
  position: number;
  league: string;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  /** 1 home, 2 draw, 3 away — snapshotted when it was added. */
  pick: number;
  prob_home: number;
  prob_draw: number;
  prob_away: number;
  added_at: string;
  actual: number | null;
  home_score: number | null;
  away_score: number | null;
  status_group: string | null;
  hit: boolean | null;
}

export interface BetslipResponse {
  date: string;
  rows: BetslipRow[];
  dates: string[];
  summary: { picks: number; settled: number; correct: number };
}
