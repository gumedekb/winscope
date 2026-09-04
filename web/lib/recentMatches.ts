/**
 * Recent-form lookups.
 *
 * Previously this hit football-data.org's World Cup endpoint — left over from
 * v2 and simply wrong for club football (a Premier League club has no WC
 * results, so every form strip came back empty). It now reads the ETL's
 * `fixtures` table, which already holds finished club matches for all eleven
 * leagues and keeps them forever.
 *
 * That also removes an uncapped external call from the request path: the ETL
 * owns the API budget, and this app should never spend it.
 */
import { getTeamRecent, type FixtureRow } from './fixtures';

export interface RecentMatch {
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  match_date: string;
  competition: string;
  result?: 'W' | 'D' | 'L';
}

/** Kept for the predict route's cache-staleness check. */
export const RECENT_MATCHES_TTL_MS = 6 * 60 * 60 * 1000; // 6h

function toRecent(team: string, r: FixtureRow): RecentMatch {
  let result: 'W' | 'D' | 'L' | undefined;
  if (r.outcome === 2) result = 'D';
  else if (r.outcome === 1 || r.outcome === 3) {
    const isHome = r.home_team === team;
    result = (r.outcome === 1) === isHome ? 'W' : 'L';
  }
  return {
    home_team: r.home_team,
    away_team: r.away_team,
    home_score: r.home_score,
    away_score: r.away_score,
    match_date: r.kickoff_utc,
    competition: r.league,
    result,
  };
}

/**
 * A club's most recent finished matches, newest first.
 * `null` means the lookup itself failed (so callers can keep a prior snapshot);
 * an empty array means "looked fine, no results stored yet".
 */
export async function fetchTeamRecent(teamName: string, count = 5): Promise<RecentMatch[] | null> {
  if (!teamName || teamName === 'TBD') return null;
  try {
    const rows = await getTeamRecent(teamName, count);
    return rows.map((r) => toRecent(teamName, r));
  } catch (e: unknown) {
    console.error('[RECENT] lookup failed:', e instanceof Error ? e.message : e);
    return null;
  }
}

/** Latest finished matches for both sides of a fixture. */
export async function fetchFixtureRecent(homeTeam: string, awayTeam: string, count = 5) {
  const [home, away] = await Promise.all([
    fetchTeamRecent(homeTeam, count),
    fetchTeamRecent(awayTeam, count),
  ]);
  return { home, away };
}
