import dotenv from 'dotenv';
import path from 'path';

dotenv.config();

export const config = {
  apiKey: process.env.FOOTBALL_DATA_API_KEY || '',
  apiSportsKey: process.env.API_SPORTS_KEY || '',
  theSportsDbKey: process.env.THE_SPORTS_DB_KEY || '3',
  dbPath: process.env.DATABASE_PATH || './data/football.sqlite',
  leagues: ['PL', 'BL1', 'SA', 'PD', 'FL1', 'DED', 'PPL', 'RSA'],
  seasons: [2022, 2023, 2024, 2025],
};

if (!config.apiKey) {
  console.warn('WARNING: FOOTBALL_DATA_API_KEY is not set in .env');
}
