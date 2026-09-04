/**
 * WinScope web config.
 *
 * Note what is NOT here any more: football-data.org and API-Football keys.
 * This app no longer calls either. Fixtures, results and live scores all come
 * from the Turso `fixtures` table written by the data/ ETL, which caps every
 * provider at 90% of its free tier and shares one persisted budget across runs.
 * A second, uncapped caller in the web app would defeat that — API-Football's
 * free tier is 100 requests A DAY, and a few impatient clicks could spend it.
 *
 * The odds API is the one external call that remains, and it is optional:
 * with no key the model's own probabilities pass straight through.
 */

export interface LeagueConfig {
  name: string;
  country: string;
}

/**
 * The leagues WinScope covers, for labels and ordering.
 * The authoritative registry — including every provider's league ids — is
 * data/leagues.py; this is display metadata only.
 */
export const LEAGUES: LeagueConfig[] = [
  { name: 'Premier League',       country: 'England' },
  { name: 'Championship',         country: 'England' },
  { name: 'League One',           country: 'England' },
  { name: 'Bundesliga',           country: 'Germany' },
  { name: 'Serie A',              country: 'Italy' },
  { name: 'La Liga',              country: 'Spain' },
  { name: 'Ligue 1',              country: 'France' },
  { name: 'Eredivisie',           country: 'Netherlands' },
  { name: 'Liga Portugal',        country: 'Portugal' },
  { name: 'Scottish Premiership', country: 'Scotland' },
  { name: 'Betway Premiership',   country: 'South Africa' },
];

export const COUNTRY_BY_LEAGUE: Record<string, string> = Object.fromEntries(
  LEAGUES.map((l) => [l.name, l.country])
);

export const config = {
  modelServerUrl:  process.env.MODEL_SERVER_URL || 'http://127.0.0.1:8000',
  oddsApiKey:      process.env.THE_ODDS_API_KEY || '',
  oddsModelWeight: Number(process.env.ODDS_MODEL_WEIGHT || 0.35),
};
