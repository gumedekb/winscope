import { db } from './schema';

export const repository = {
  upsertCompetition: (id: number, code: string, name: string) => {
    // football_master.sqlite uses 'code' as identifier
    const stmt = db.prepare(`
      INSERT INTO competitions (code, name)
      VALUES (?, ?)
      ON CONFLICT(code) DO UPDATE SET name=excluded.name
    `);
    stmt.run(code, name);
  },

  upsertTeam: (id: number, name: string, shortName: string, tla: string) => {
    const stmt = db.prepare(`
      INSERT INTO teams (team_id, team_name, short_name, tla)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(team_id) DO UPDATE SET team_name=excluded.team_name, short_name=excluded.short_name, tla=excluded.tla
    `);
    stmt.run(id, name, shortName, tla);
  },

  upsertMatch: (
    id: number,
    utcDate: string,
    status: string,
    matchday: number,
    competitionCode: string,
    season: number,
    homeTeamId: number,
    awayTeamId: number,
    homeScore: number | null,
    awayScore: number | null
  ) => {
    const dateOnly = utcDate.substring(0, 10);
    const existing = db.prepare(`
      SELECT match_id FROM matches 
      WHERE SUBSTR(match_date, 1, 10) = ? 
      AND home_id = ? 
      AND away_id = ?
    `).get(dateOnly, homeTeamId, awayTeamId) as { match_id: number } | undefined;

    if (existing) {
      const stmt = db.prepare(`
        UPDATE matches SET 
          match_date = ?, 
          status = ?, 
          matchday = ?, 
          home_goals = ?, 
          away_goals = ?
        WHERE match_id = ?
      `);
      stmt.run(utcDate, status, matchday, homeScore, awayScore, existing.match_id);
      return;
    }

    const stmt = db.prepare(`
      INSERT INTO matches (match_id, match_date, status, matchday, competition, season, home_id, away_id, home_goals, away_goals)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(match_id) DO UPDATE SET 
        match_date=excluded.match_date, 
        status=excluded.status, 
        matchday=excluded.matchday, 
        home_goals=excluded.home_goals, 
        away_goals=excluded.away_goals
    `);
    stmt.run(id, utcDate, status, matchday, competitionCode, season, homeTeamId, awayTeamId, homeScore, awayScore);
  },

  getElo: (teamId: number): number => {
    // team_elo columns: team_id, elo_rating, team_name, competition
    const row = db.prepare('SELECT elo_rating FROM team_elo WHERE team_id = ?').get(teamId) as { elo_rating: number } | undefined;
    return row ? row.elo_rating : 1000;
  },

  updateElo: (teamId: number, elo: number) => {
    db.prepare('INSERT INTO team_elo (team_id, elo_rating) VALUES (?, ?) ON CONFLICT(team_id) DO UPDATE SET elo_rating=excluded.elo_rating').run(teamId, elo);
  },

  getRecentMatches: (teamId: number, limit: number = 5, venue: 'home' | 'away' | 'any' = 'any') => {
    let query = `SELECT * FROM matches WHERE status = 'FINISHED' AND (home_id = ? OR away_id = ?)`;
    if (venue === 'home') query = `SELECT * FROM matches WHERE status = 'FINISHED' AND home_id = ?`;
    if (venue === 'away') query = `SELECT * FROM matches WHERE status = 'FINISHED' AND away_id = ?`;
    
    query += ` ORDER BY match_date DESC LIMIT ?`;
    const params = venue === 'any' ? [teamId, teamId, limit] : [teamId, limit];
    return db.prepare(query).all(...params) as any[];
  },

  getPrevSeasonStanding: (competitionCode: string, season: number, teamId: number) => {
    // standings: competition, season, team_id, position, norm_position...
    return db.prepare('SELECT position, norm_position, (SELECT COUNT(*) FROM standings WHERE competition = ? AND season = ?) as total_teams FROM standings WHERE competition = ? AND season = ? AND team_id = ?')
             .get(competitionCode, season - 1, competitionCode, season - 1, teamId) as { position: number, norm_position: number, total_teams: number } | undefined;
  },

  getMaxMatchday: (competitionCode: string, season: number) => {
    const row = db.prepare('SELECT MAX(matchday) as maxMd FROM matches WHERE competition = ? AND season = ?').get(competitionCode, season) as { maxMd: number } | undefined;
    return row ? row.maxMd : 38;
  },

  getMatchFeatures: (matchId: number) => {
    return db.prepare('SELECT * FROM match_features WHERE match_id = ?').get(matchId);
  }
};
