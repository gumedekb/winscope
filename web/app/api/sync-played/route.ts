import { NextResponse } from 'next/server';
import { getFreshness } from '../../../lib/fixtures';

export const dynamic = 'force-dynamic';

/**
 * RETIRED — historical results are no longer fetched here.
 *
 * This route used to pull whole seasons from API-Football (and was still
 * pointed at international friendlies + the World Cup, left over from v2). On
 * the free tier that is 100 requests a day total, shared with the ETL — a
 * couple of clicks could exhaust it and repeated use risks the key being
 * banned.
 *
 * The ETL owns that job now: `python pipeline.py --with-api` builds the training
 * history, and `--from-turso` folds finished matches back in before a retrain.
 */
export async function POST() {
  const freshness = await getFreshness().catch(() => null);
  return NextResponse.json(
    {
      error: 'Retired endpoint',
      message:
        'Historical results are collected by the data/ ETL, which rate-limits ' +
        'every provider to 90% of its free tier. Fetching them from the web app ' +
        'would bypass that cap.',
      how_to_refresh: 'cd data && python pipeline.py --with-api',
      freshness,
    },
    { status: 410 }
  );
}

export async function GET() {
  return POST();
}
