import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';

const upcomingDbPath = './data/upcoming_matches.sqlite';
const dbDir = path.dirname(upcomingDbPath);

if (!fs.existsSync(dbDir)) {
  fs.mkdirSync(dbDir, { recursive: true });
}

const upcomingDb: Database.Database = new Database(upcomingDbPath);
export { upcomingDb };

export function initializeUpcomingDatabase() {
  upcomingDb.exec(`
    CREATE TABLE IF NOT EXISTS upcoming_matches (
      match_id INTEGER PRIMARY KEY,
      competition TEXT,
      season INTEGER,
      matchday INTEGER,
      home_id INTEGER,
      away_id INTEGER,
      match_date TEXT,
      status TEXT,
      home_team_name TEXT,
      away_team_name TEXT,
      venue TEXT
    );
  `);
}
