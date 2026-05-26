import axios from 'axios';
import { config } from '../config';

// Clients for different providers
const footballDataClient = axios.create({
  baseURL: 'https://api.football-data.org/v4',
});

const apiSportsClient = axios.create({
  baseURL: 'https://v3.football.api-sports.io',
});

const theSportsDbClient = axios.create({
  baseURL: `https://www.thesportsdb.com/api/v1/json/${config.theSportsDbKey}`,
});

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Rate limiting: football-data.org (10 requests per minute)
const FOOTBALL_DATA_DELAY = 6500;

async function throttledFootballDataGet(url: string, params?: any) {
  try {
    console.log(`[Football-Data] Fetching: ${url} ${JSON.stringify(params || {})}`);
    const response = await footballDataClient.get(url, {
      params,
      headers: { 'X-Auth-Token': config.apiKey },
    });
    await sleep(FOOTBALL_DATA_DELAY);
    return response.data;
  } catch (error: any) {
    if (error.response?.status === 429) {
      console.warn('[Football-Data] Rate limit hit, sleeping for 60 seconds...');
      await sleep(60000);
      return throttledFootballDataGet(url, params);
    }
    throw error;
  }
}

async function apiSportsGet(url: string, params?: any) {
  try {
    console.log(`[API-Sports] Fetching: ${url} ${JSON.stringify(params || {})}`);
    const response = await apiSportsClient.get(url, {
      params,
      headers: { 'x-apisports-key': config.apiSportsKey },
    });
    return response.data;
  } catch (error: any) {
    console.error(`[API-Sports] Error: ${error.message}`);
    throw error;
  }
}

async function theSportsDbGet(url: string, params?: any) {
  try {
    console.log(`[TheSportsDB] Fetching: ${url} ${JSON.stringify(params || {})}`);
    const response = await theSportsDbClient.get(url, { params });
    return response.data;
  } catch (error: any) {
    console.error(`[TheSportsDB] Error: ${error.message}`);
    throw error;
  }
}

// League mappings across providers
export const LEAGUE_MAP: Record<string, { fd: string; tsdb: string; as: number }> = {
  'PL':  { fd: 'PL',   tsdb: '4328', as: 39 },
  'BL1': { fd: 'BL1',  tsdb: '4331', as: 78 },
  'SA':  { fd: 'SA',   tsdb: '4335', as: 135 },
  'PD':  { fd: 'PD',   tsdb: '4332', as: 140 },
  'FL1': { fd: 'FL1',  tsdb: '4334', as: 61 },
  'DED': { fd: 'DED',  tsdb: '4337', as: 88 },
  'PPL': { fd: 'PPL',  tsdb: '4344', as: 94 },
  'RSA': { fd: '',     tsdb: '4417', as: 288 }, // Betway Premiership
};

export const apiClient = {
  // Strategy 1: Historical Data (FD -> TSDB -> AS)
  getHistoricalMatches: async (leagueCode: string, season: number) => {
    const mapping = LEAGUE_MAP[leagueCode];
    if (!mapping) throw new Error(`League ${leagueCode} not mapped.`);

    // 1. Football-Data.org
    if (mapping.fd) {
      try {
        const data = await throttledFootballDataGet(`/competitions/${mapping.fd}/matches`, { season });
        if (data && data.matches) return { source: 'Football-Data', data: data.matches };
      } catch (e: any) {
        console.warn(`[Historical] Football-Data failed for ${leagueCode}: ${e.message}`);
      }
    }

    // 2. TheSportsDB
    if (mapping.tsdb) {
      try {
        // TheSportsDB often uses season strings like "2023-2024" or just year
        const seasonStr = `${season}-${season + 1}`;
        const data = await theSportsDbGet('/eventsseason.php', { id: mapping.tsdb, s: seasonStr });
        if (data && data.events) return { source: 'TheSportsDB', data: data.events };
      } catch (e: any) {
        console.warn(`[Historical] TheSportsDB failed for ${leagueCode}: ${e.message}`);
      }
    }

    // 3. API-Sports
    if (mapping.as) {
      try {
        const data = await apiSportsGet('/fixtures', { league: mapping.as, season });
        if (data && data.response) return { source: 'API-Sports', data: data.response };
      } catch (e: any) {
        console.warn(`[Historical] API-Sports failed for ${leagueCode}: ${e.message}`);
      }
    }

    throw new Error(`Failed to fetch historical matches for ${leagueCode} ${season} from all sources.`);
  },

  // Strategy 2: Upcoming Matches (AS -> FD)
  getUpcomingMatches: async (leagueCode: string, days: number = 3) => {
    const mapping = LEAGUE_MAP[leagueCode];
    const from = new Date().toISOString().split('T')[0];
    const to = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

    // 1. API-Sports
    if (mapping?.as) {
      try {
        const data = await apiSportsGet('/fixtures', { league: mapping.as, from, to });
        if (data && data.response && data.response.length > 0) return { source: 'API-Sports', data: data.response };
      } catch (e: any) {
        console.warn(`[Upcoming] API-Sports failed for ${leagueCode}: ${e.message}`);
      }
    }

    // 2. Football-Data
    if (mapping?.fd) {
      try {
        const data = await throttledFootballDataGet(`/competitions/${mapping.fd}/matches`, { dateFrom: from, dateTo: to });
        if (data && data.matches) return { source: 'Football-Data', data: data.matches };
      } catch (e: any) {
        console.warn(`[Upcoming] Football-Data failed for ${leagueCode}: ${e.message}`);
      }
    }

    return { source: 'None', data: [] };
  },

  // Other utilities
  apiSports: {
    getTeamStats: (league: number, season: number, team: number) => apiSportsGet('/teams/statistics', { league, season, team }),
  }
};
