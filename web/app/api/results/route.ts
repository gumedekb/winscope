import { NextResponse } from 'next/server';
import { getFreshness } from '../../../lib/fixtures';

export const dynamic = 'force-dynamic';

/**
 * RETIRED — results are no longer fetched here.
 *
 * This route used to loop every league against football-data.org to find
 * finished matches. That work now belongs entirely to the data/ ETL, which
 * writes results into the `fixtures` table and never deletes them, so the track
 * record reads them directly (see /api/history).
 *
 * The reason it is not simply "moved" is the rate limit: the ETL caps each
 * provider at 90% of its free tier and shares one persisted budget across every
 * run. A second, uncapped caller in the web app defeats that entirely — and
 * API-Football's free tier is 100 requests A DAY.
 *
 * Kept as a 410 rather than deleted so an old client gets an explanation
 * instead of a 404.
 */
export async function GET() {
  const freshness = await getFreshness().catch(() => null);
  return NextResponse.json(
    {
      error: 'Retired endpoint',
      message:
        'Results are collected by the data/ ETL and read from Turso. ' +
        'Use GET /api/history for the track record.',
      how_to_refresh: 'cd data && python pipeline.py --live',
      freshness,
    },
    { status: 410 }
  );
}
