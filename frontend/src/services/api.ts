import axios from 'axios';
import { Competition, Match, Team, Prediction } from '../types';

const API_BASE_URL = 'http://localhost:3001/api';

const client = axios.create({
  baseURL: API_BASE_URL,
});

export const api = {
  getCompetitions: () => client.get<Competition[]>('/competitions').then(res => res.data),
  
  getTeams: (competitionId: number) => 
    client.get<Team[]>(`/teams/${competitionId}`).then(res => res.data),
  
  getMatches: (params: { competitionId?: number; season?: number; status?: string }) => 
    client.get<Match[]>('/matches', { params }).then(res => res.data),

  getUpcomingMatchesList: () => 
    client.get<Match[]>('/matches/upcoming/list').then(res => res.data),

  fetchUpcomingMatches: () => client.get('/matches/upcoming').then(res => res.data),

  syncHistoricalResults: () => client.get('/matches/sync-results').then(res => res.data),

  getPrediction: async (match: Match): Promise<Prediction> => {
    const res = await client.post<Prediction>('/predict', {
      matchId: match.id,
      homeTeamId: match.homeTeamId,
      awayTeamId: match.awayTeamId,
      competitionId: match.competitionId,
      season: match.season,
      matchday: match.matchday,
      competitionCode: match.competitionId.toString()
    });
    
    const p = res.data;
    let outcome: 'H' | 'D' | 'A' = 'H';
    if (p.draw > p.home_win && p.draw > p.away_win) outcome = 'D';
    else if (p.away_win > p.home_win && p.away_win > p.draw) outcome = 'A';
    
    return { ...p, outcome };
  },

  getTeamCrestUrl: (teamId: number) => `https://crests.football-data.org/${teamId}.png`
};
