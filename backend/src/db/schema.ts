import Database from 'better-sqlite3';
import { config } from '../config';
import fs from 'fs';
import path from 'path';

const dbDir = path.dirname(config.dbPath);
if (!fs.existsSync(dbDir)) {
  fs.mkdirSync(dbDir, { recursive: true });
}

const db: Database.Database = new Database(config.dbPath);
export { db };

export function initializeDatabase() {
  // We use the schema from football_master.sqlite
  db.exec(`
    CREATE TABLE IF NOT EXISTS competitions (
      code TEXT PRIMARY KEY,
      name TEXT,
      country TEXT,
      league_draw_rate REAL
    );

    CREATE TABLE IF NOT EXISTS teams (
      team_id INTEGER PRIMARY KEY,
      team_name TEXT,
      short_name TEXT,
      tla TEXT,
      competition TEXT
    );

    CREATE TABLE IF NOT EXISTS matches (
      match_id INTEGER PRIMARY KEY,
      competition TEXT,
      season INTEGER,
      matchday INTEGER,
      home_id INTEGER,
      away_id INTEGER,
      home_goals INTEGER,
      away_goals INTEGER,
      match_date TEXT,
      status TEXT,
      result TEXT,
      FOREIGN KEY (competition) REFERENCES competitions(code),
      FOREIGN KEY (home_id) REFERENCES teams(team_id),
      FOREIGN KEY (away_id) REFERENCES teams(team_id)
    );

    CREATE TABLE IF NOT EXISTS standings (
      competition TEXT,
      season INTEGER,
      team_id INTEGER,
      position INTEGER,
      played_games INTEGER,
      won INTEGER,
      draw INTEGER,
      lost INTEGER,
      points INTEGER,
      goals_for INTEGER,
      goals_against INTEGER,
      goal_diff INTEGER,
      norm_position REAL,
      PRIMARY KEY (competition, season, team_id),
      FOREIGN KEY (competition) REFERENCES competitions(code),
      FOREIGN KEY (team_id) REFERENCES teams(team_id)
    );

    CREATE TABLE IF NOT EXISTS team_elo (
      team_id INTEGER PRIMARY KEY,
      elo_rating REAL,
      team_name TEXT,
      competition TEXT
    );

    CREATE TABLE IF NOT EXISTS match_features (
      match_id INTEGER PRIMARY KEY,
      competition TEXT,
      season INTEGER,
      matchday INTEGER,
      home_id INTEGER,
      away_id INTEGER,
      target INTEGER,
      season_stage REAL,
      home_prev_pos REAL,
      away_prev_pos REAL,
      home_overall_form REAL,
      home_overall_goals_avg REAL,
      home_overall_conc_avg REAL,
      home_overall_goals_std REAL,
      home_overall_draw_rate REAL,
      home_venue_form REAL,
      home_venue_goals_avg REAL,
      home_venue_conc_avg REAL,
      home_venue_goals_std REAL,
      home_venue_draw_rate REAL,
      away_overall_form REAL,
      away_overall_goals_avg REAL,
      away_overall_conc_avg REAL,
      away_overall_goals_std REAL,
      away_overall_draw_rate REAL,
      away_venue_form REAL,
      away_venue_goals_avg REAL,
      away_venue_conc_avg REAL,
      away_venue_goals_std REAL,
      away_venue_draw_rate REAL,
      form_venue_diff REAL,
      form_overall_diff REAL,
      goals_diff REAL,
      conceded_diff REAL,
      net_diff REAL,
      draw_tendency REAL,
      strength_diff REAL,
      league_draw_rate REAL,
      home_elo_raw REAL,
      away_elo_raw REAL,
      elo_diff_raw REAL,
      home_elo REAL,
      away_elo REAL,
      elo_diff REAL
    );
  `);
}
