export interface Competition {
  id: number;
  name: string;
  code: string;
}

export interface Team {
  id: number;
  name: string;
  shortName: string;
  tla: string;
}

export interface Match {
  id: number;
  utcDate: string;
  status: string;
  matchday: number;
  competitionId: number | string;
  season: number;
  homeTeamId: number;
  awayTeamId: number;
  homeScore: number | null;
  awayScore: number | null;
  // UI extended fields
  homeTeam?: Team;
  awayTeam?: Team;
  prediction?: Prediction;
}

export interface Prediction {
  home_win: number;
  draw: number;
  away_win: number;
  home_elo?: number;
  away_elo?: number;
  home_form?: string[];
  away_form?: string[];
  outcome?: 'H' | 'D' | 'A'; // Derived for UI
}

export interface Standing {
  competitionId: number | string;
  season: number;
  teamId: number;
  position: number;
  playedGames: number;
  won: number;
  draw: number;
  lost: number;
  points: number;
  goalsFor: number;
  goalsAgainst: number;
  team?: Team;
}
