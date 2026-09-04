import { NextResponse } from 'next/server';
import { getFreshness } from '../../../lib/fixtures';

export const dynamic = 'force-dynamic';

/**
 * Refresh — reports what is currently in Turso.
 *
 * It deliberately does NOT fetch fixtures from football-data.org or
 * API-Football any more. That job belongs to the data/ ETL, which caps every
 * provider at 90% of its free tier and shares one budget across all runs
 * (data/output/.quota.json). A "Sync" button here that hit those APIs directly
 * would bypass that cap entirely — one impatient click could spend a chunk of
 * the 100/day API-Football allowance, and repeated clicks would get the key
 * banned, which is exactly what the cap exists to prevent.
 *
 * To pull new matches:  cd data && python pipeline.py --live
 * (or let the GitHub Actions cron do it twice a day).
 */
export async function POST() {
  try {
    const freshness = await getFreshness();
    const staleness = freshness.lastUpdated
      ? Date.now() - new Date(freshness.lastUpdated).getTime()
      : null;
    const hours = staleness === null ? null : staleness / 3_600_000;

    return NextResponse.json({
      message:
        `${freshness.scheduled} upcoming · ${freshness.inPlay} live · ` +
        `${freshness.finished} finished across ${freshness.leagues.length} leagues`,
      freshness,
      stale: hours !== null && hours > 12,
      hint:
        hours !== null && hours > 12
          ? 'Data is over 12h old — run `python pipeline.py --live` in data/ to refresh.'
          : null,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function GET() {
  return POST();
}
