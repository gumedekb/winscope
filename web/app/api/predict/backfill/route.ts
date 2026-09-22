import { NextResponse } from 'next/server';
import { getPredictions, getUpcoming } from '../../../../lib/fixtures';
import { missingPredictions, predictSlate, storeSlate } from '../../../../lib/predictSlate';
import { getMarketForFixtures } from '../../../../lib/odds';

export const dynamic = 'force-dynamic';
// Long enough to wait out a Render cold start (~50s) inside one call.
export const maxDuration = 60;

/**
 * The guard: make sure every upcoming fixture has a prediction on file.
 *
 * The dashboard already fills gaps whenever someone loads it, but a fixture
 * nobody looks at before kickoff — or one that was loaded only while the model
 * server was asleep — never gets its prediction, and then it can never be
 * scored. So the data/ ETL calls this after every pass (every ~20 minutes):
 * it finds the upcoming fixtures with nothing in `predictions`, asks the model
 * for them in one batch and stores the answers. Nothing is re-predicted; a
 * fixture that already has a row is left exactly as it was, so the track
 * record always scores the call that was made first.
 *
 * Costs nothing but a model-server call — no football API is touched — so it
 * is safe to run often. Set WINSCOPE_BACKFILL_KEY on Vercel and the same value
 * in the ETL's secrets to stop anyone else triggering it; unset, it is open,
 * which is harmless but noisy.
 */
export async function POST(request: Request) {
  const expected = process.env.WINSCOPE_BACKFILL_KEY;
  if (expected && request.headers.get('x-winscope-key') !== expected) {
    return NextResponse.json({ error: 'unauthorised' }, { status: 401 });
  }

  try {
    const upcoming = await getUpcoming(300);
    const stored = await getPredictions(upcoming.map((f) => f.match_key));
    const missing = missingPredictions(upcoming, stored);
    if (missing.length === 0) {
      return NextResponse.json({
        upcoming: upcoming.length, missing: 0, predicted: 0, refused: [], reachable: true,
        message: 'every upcoming fixture already has a prediction',
      });
    }

    const market = await getMarketForFixtures(missing);
    const slate = await predictSlate(missing, market, 55_000);
    const saved = await storeSlate(missing, slate.predictions, market);
    const refused = Array.from(slate.failed.values()).map((f) => ({
      home_team: f.home_team, away_team: f.away_team, error: f.error,
    }));

    // Still missing after this call = the model was unreachable or refused
    // them. Listed so the ETL log shows exactly which, every pass.
    const stillMissing = missing
      .filter((f) => !saved.has(f.match_key))
      .map((f) => `${f.kickoff_utc.slice(0, 10)} ${f.league}: ${f.home_team} v ${f.away_team}`);

    return NextResponse.json({
      upcoming: upcoming.length,
      missing: missing.length,
      predicted: saved.size,
      reachable: slate.reachable,
      refused,
      still_missing: stillMissing,
      message: slate.reachable
        ? `predicted ${saved.size} of ${missing.length} missing fixture(s)`
        : 'model server unreachable — will retry on the next pass',
    }, { status: slate.reachable ? 200 : 503 });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[BACKFILL] failed:', message);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function GET(request: Request) {
  return POST(request);
}
