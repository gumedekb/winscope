import express, { Request, Response } from 'express';
import cors from 'cors';
import { db, initializeDatabase } from './db/schema';
import { upcomingDb, initializeUpcomingDatabase } from './db/upcoming_schema';
import { repository } from './db/repository';
import { eloService } from './services/eloService';
import { predictionService } from './services/predictionService';
import { apiClient, LEAGUE_MAP } from './api/client';

const app = express();
const PORT = process.env.PORT || 3001;

app.use(cors());
app.use(express.json());

// Initialize Databases on startup
initializeDatabase();
initializeUpcomingDatabase();

function calculateForm(matches: any[], teamId: number) {
  if (matches.length === 0) return { ppg: 0.5, drawRate: 0.25, goalsAvg: 1.5, concAvg: 1.5, goalsStd: 1.0, form: [] };
  
  let pts = 0;
  let draws = 0;
  const goals: number[] = [];
  const conceded: number[] = [];
  const form: string[] = [];

  matches.forEach(m => {
    const isHome = m.home_id === teamId;
    const gf = isHome ? m.home_goals : m.away_goals;
    const ga = isHome ? m.away_goals : m.home_goals;
    goals.push(gf || 0);
    conceded.push(ga || 0);
    if ((gf || 0) > (ga || 0)) {
      pts += 3;
      form.push('W');
    } else if ((gf || 0) === (ga || 0)) {
      pts += 1;
      draws++;
      form.push('D');
    } else {
      form.push('L');
    }
  });

  const avg = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / arr.length;
  const std = (arr: number[]) => {
    if (arr.length < 2) return 1.0;
    const m = avg(arr);
    return Math.sqrt(arr.reduce((a, b) => a + Math.pow(b - m, 2), 0) / arr.length);
  };

  return {
    ppg: (pts / matches.length) / 3, // Normalized to 0-1
    drawRate: draws / matches.length,
    goalsAvg: avg(goals),
    concAvg: avg(conceded),
    goalsStd: std(goals),
    form: form // Matches are DESC, so first item is newest.
  };
}

// 1. Get all competitions
app.get('/api/competitions', (req: Request, res: Response) => {
  const competitions = db.prepare('SELECT * FROM competitions').all();
  res.json(competitions);
});

// 2. Get matches for a competition/season
app.get('/api/matches', (req: Request, res: Response) => {
  const { competitionId, season, status } = req.query;
  let query = `
    SELECT m.*, 
           ht.team_name as homeTeamName, ht.short_name as homeTeamShortName, ht.tla as homeTeamTla,
           at.team_name as awayTeamName, at.short_name as awayTeamShortName, at.tla as awayTeamTla
    FROM matches m
    LEFT JOIN teams ht ON m.home_id = ht.team_id
    LEFT JOIN teams at ON m.away_id = at.team_id
    WHERE 1=1
  `;
  const params: any[] = [];
  if (competitionId) { query += ' AND m.competition = ?'; params.push(competitionId); }
  if (season) { query += ' AND m.season = ?'; params.push(season); }
  if (status) { query += ' AND m.status = ?'; params.push(status); }
  query += ' ORDER BY m.match_date ASC';
  
  const rows = db.prepare(query).all(...params);
  const matches = rows.map((row: any) => ({
    id: row.match_id,
    utcDate: row.match_date,
    status: row.status,
    matchday: row.matchday,
    competitionId: row.competition,
    season: row.season,
    homeTeamId: row.home_id,
    awayTeamId: row.away_id,
    homeScore: row.home_goals,
    awayScore: row.away_goals,
    homeTeam: {
      id: row.home_id,
      name: row.homeTeamName,
      shortName: row.homeTeamShortName,
      tla: row.homeTeamTla
    },
    awayTeam: {
      id: row.away_id,
      name: row.awayTeamName,
      shortName: row.awayTeamShortName,
      tla: row.awayTeamTla
    }
  }));
  res.json(matches);
});

// 3. Upcoming matches endpoints
app.get('/api/matches/upcoming/list', (req: Request, res: Response) => {
  try {
    const rows = upcomingDb.prepare('SELECT * FROM upcoming_matches ORDER BY match_date ASC').all();
    const matches = rows.map((row: any) => ({
      id: row.match_id,
      utcDate: row.match_date,
      status: row.status,
      matchday: row.matchday,
      competitionId: row.competition,
      season: row.season,
      homeTeamId: row.home_id,
      awayTeamId: row.away_id,
      homeTeam: {
        id: row.home_id,
        name: row.home_team_name,
        shortName: row.home_team_name,
        tla: ''
      },
      awayTeam: {
        id: row.away_id,
        name: row.away_team_name,
        shortName: row.away_team_name,
        tla: ''
      }
    }));
    res.json(matches);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/matches/upcoming', async (req: Request, res: Response) => {
  try {
    let totalFetched = 0;
    const dbLeagues = db.prepare('SELECT DISTINCT code FROM competitions').all() as { code: string }[];
    const leagues = dbLeagues.map(l => l.code);

    for (const leagueCode of leagues) {
      const result = await apiClient.getUpcomingMatches(leagueCode, 3);
      if (result.source !== 'None' && result.data) {
        for (const item of result.data) {
          if (result.source === 'API-Sports') {
            const f = item.fixture;
            const l = item.league;
            const t = item.teams;
            
            upcomingDb.prepare(`
              INSERT INTO upcoming_matches (match_id, competition, season, matchday, home_id, away_id, match_date, status, home_team_name, away_team_name, venue)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(match_id) DO UPDATE SET match_date=excluded.match_date, status=excluded.status
            `).run(f.id, leagueCode, l.season, l.round ? parseInt(l.round.replace(/[^0-9]/g, '')) : 0, t.home.id, t.away.id, f.date, f.status.short, t.home.name, t.away.name, f.venue.name);
          } else if (result.source === 'Football-Data') {
            const m = item;
            upcomingDb.prepare(`
              INSERT INTO upcoming_matches (match_id, competition, season, matchday, home_id, away_id, match_date, status, home_team_name, away_team_name, venue)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(match_id) DO UPDATE SET match_date=excluded.match_date, status=excluded.status
            `).run(m.id, leagueCode, m.season.currentMatchday, m.matchday, m.homeTeam.id, m.awayTeam.id, m.utcDate, m.status, m.homeTeam.name, m.awayTeam.name, m.venue || '');
          }
          totalFetched++;
        }
      }
    }
    res.json({ message: `Fetched ${totalFetched} upcoming matches for the next 3 days across all leagues`, count: totalFetched });
  } catch (error: any) {
    console.error('Upcoming matches error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/matches/sync-results', async (req: Request, res: Response) => {
  try {
    let totalUpdated = 0;
    const dbLeagues = db.prepare('SELECT DISTINCT code FROM competitions').all() as { code: string }[];
    const leagues = dbLeagues.map(l => l.code);
    const season = 2025; // Update for current season

    for (const leagueCode of leagues) {
      try {
        const result = await apiClient.getHistoricalMatches(leagueCode, season);
        if (result.data) {
          for (const item of result.data) {
             if (result.source === 'Football-Data' && item.status === 'FINISHED') {
                repository.upsertMatch(
                  item.id, item.utcDate, item.status, item.matchday, leagueCode, season,
                  item.homeTeam.id, item.awayTeam.id, item.score.fullTime.home, item.score.fullTime.away
                );
                totalUpdated++;
             } else if (result.source === 'API-Sports' && item.fixture.status.short === 'FT') {
                const f = item.fixture;
                const g = item.goals;
                const t = item.teams;
                repository.upsertMatch(
                  f.id, f.date, 'FINISHED', item.league.round ? parseInt(item.league.round.replace(/[^0-9]/g, '')) : 0, 
                  leagueCode, season, t.home.id, t.away.id, g.home, g.away
                );
                totalUpdated++;
             }
          }
        }
      } catch (e: any) {
        console.warn(`Failed to sync results for ${leagueCode}: ${e.message}`);
      }
    }
    
    // Recalculate Elo ratings for the season after syncing results
    // This is optional but recommended if new results were added
    // For now, we'll just return the count
    res.json({ message: `Synced ${totalUpdated} finished match results across all leagues.`, count: totalUpdated });
  } catch (error: any) {
    console.error('Sync results error:', error);
    res.status(500).json({ error: error.message });
  }
});

// 4. Prediction Logic
app.post('/api/predict', async (req: Request, res: Response) => {
  const { matchId, homeTeamId, awayTeamId, competitionId, season, matchday, competitionCode } = req.body;
  
  try {
    const existingFeatures = repository.getMatchFeatures(matchId);
    let features: any;
    
    const hO = calculateForm(repository.getRecentMatches(homeTeamId, 5, 'any'), homeTeamId);
    const aO = calculateForm(repository.getRecentMatches(awayTeamId, 5, 'any'), awayTeamId);

    if (existingFeatures) {
      const f = existingFeatures as any;
      features = {
        competition: f.competition,
        home_elo: f.home_elo_raw || f.home_elo,
        away_elo: f.away_elo_raw || f.away_elo,
        season_stage: f.season_stage,
        home_prev_pos: f.home_prev_pos,
        away_prev_pos: f.away_prev_pos,
        home_overall_form: f.home_overall_form,
        home_venue_form: f.home_venue_form,
        away_overall_form: f.away_overall_form,
        away_venue_form: f.away_venue_form,
        home_overall_goals_avg: f.home_overall_goals_avg,
        home_overall_conc_avg: f.home_overall_conc_avg,
        away_overall_goals_avg: f.away_overall_goals_avg,
        away_overall_conc_avg: f.away_overall_conc_avg,
        home_venue_goals_avg: f.home_venue_goals_avg,
        home_venue_conc_avg: f.home_venue_conc_avg,
        away_venue_goals_avg: f.away_venue_goals_avg,
        away_venue_conc_avg: f.away_venue_conc_avg,
        home_overall_goals_std: f.home_overall_goals_std,
        away_overall_goals_std: f.away_overall_goals_std,
        home_overall_draw_rate: f.home_overall_draw_rate,
        away_overall_draw_rate: f.away_overall_draw_rate
      };
    } else {
      const hV = calculateForm(repository.getRecentMatches(homeTeamId, 5, 'home'), homeTeamId);
      const aV = calculateForm(repository.getRecentMatches(awayTeamId, 5, 'away'), awayTeamId);

      const hPrev = repository.getPrevSeasonStanding(competitionCode || competitionId, season, homeTeamId);
      const aPrev = repository.getPrevSeasonStanding(competitionCode || competitionId, season, awayTeamId);
      
      const maxMd = repository.getMaxMatchday(competitionCode || competitionId, season);

      features = {
        competition: competitionCode || 'PL',
        home_elo: eloService.getRating(homeTeamId),
        away_elo: eloService.getRating(awayTeamId),
        season_stage: matchday / maxMd,
        home_prev_pos: hPrev ? hPrev.norm_position : 0.75,
        away_prev_pos: aPrev ? aPrev.norm_position : 0.75,
        home_overall_form: hO.ppg,
        home_venue_form: hV.ppg,
        away_overall_form: aO.ppg,
        away_venue_form: aV.ppg,
        home_overall_goals_avg: hO.goalsAvg,
        home_overall_conc_avg: hO.concAvg,
        away_overall_goals_avg: aO.goalsAvg,
        away_overall_conc_avg: aO.concAvg,
        home_venue_goals_avg: hV.goalsAvg,
        home_venue_conc_avg: hV.concAvg,
        away_venue_goals_avg: aV.goalsAvg,
        away_venue_conc_avg: aV.concAvg,
        home_overall_goals_std: hO.goalsStd,
        away_overall_goals_std: aO.goalsStd,
        home_overall_draw_rate: hO.drawRate,
        away_overall_draw_rate: aO.drawRate
      };
    }

    const prediction = await predictionService.predict(features);
    res.json({
      ...prediction,
      home_form: hO.form,
      away_form: aO.form
    });
  } catch (error: any) {
    console.error('Prediction error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.get('/', (req: Request, res: Response) => {
  res.send('API is running 🚀');
});

app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});
