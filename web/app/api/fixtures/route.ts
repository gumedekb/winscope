import { NextResponse } from 'next/server';
import {
  argmaxOutcome, buildFormIndex, getFinished, getFreshness, getPredictions,
  getTeamBadges, getUpcoming, savePrediction, type PredictionRow,
} from '../../../lib/fixtures';
import { missingPredictions, predictSlate, storeSlate } from '../../../lib/predictSlate';
import { applyMarket, getMarketForFixtures, type Market } from '../../../lib/odds';

export const dynamic = 'force-dynamic';

/**
 * The dashboard's data source: live + upcoming fixtures out of Turso, joined to
 * this app's predictions.
 *
 * It makes NO football-API calls. The data/ ETL is the only thing allowed to,
 * because it caps each provider at 90% of its free tier; a second uncapped
 * caller here would blow through that and get the key banned. If a fixture is
 * missing, the fix is to run the ETL, not to fetch it from here.
 */

const OUTCOME_LETTER: Record<number, 'H' | 'D' | 'A'> = { 1: 'H', 2: 'D', 3: 'A' };

/** Did the model consume odds when it made this stored call? */
const usedOdds = (pred: PredictionRow): boolean => {
  if (!pred.payload_json) return false;
  try { return Boolean(JSON.parse(pred.payload_json).used_odds); } catch { return false; }
};

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const league = searchParams.get('league');
  const limit = Number(searchParams.get('limit') || 300);

  try {
    const [all, freshness, badges] = await Promise.all([
      getUpcoming(limit), getFreshness(), getTeamBadges(),
    ]);
    const fixtures = league && league !== 'all'
      ? all.filter((f) => f.league === league)
      : all;

    const stored = await getPredictions(fixtures.map((f) => f.match_key));

    // Anything without a stored prediction goes to the model in one batch.
    // This is the page-view gap filler; /api/predict/backfill does the same
    // on the ETL's schedule so a fixture is covered even if nobody loads this.
    const missing = missingPredictions(fixtures, stored);
    // Market consensus for the whole slate: a feature for the model on the
    // missing ones, the benchmark snapshot and the display blend on all.
    const marketByKey = await getMarketForFixtures(fixtures);
    const slate = await predictSlate(missing, marketByKey);
    for (const [key, row] of await storeSlate(missing, slate.predictions, marketByKey)) stored.set(key, row);
    const modelForm = new Map<string, { home?: string[]; away?: string[] }>();
    for (const [key, p] of slate.predictions) {
      modelForm.set(key, { home: p.home_form, away: p.away_form });
    }

    // Form strips. Two sources, best first:
    //   1. the model server, which keeps a rolling W/D/L per club over its full
    //      38k-match training history;
    //   2. finished matches in Turso, which only go back as far as the ETL has
    //      been running.
    // One query for all of (2), indexed in memory — a per-club query would be
    // ~200 round trips to render a single page.
    const formByTeam = buildFormIndex(await getFinished(1000), 5);

    /** Model form if we have it, else whatever Turso can show. */
    const formFor = (key: string, team: string, side: 'home' | 'away') => {
      const fresh = modelForm.get(key);
      if (fresh) {
        const strip = side === 'home' ? fresh.home : fresh.away;
        if (strip?.length) return strip as ('W' | 'D' | 'L')[];
      }
      const cached = stored.get(key)?.payload_json;
      if (cached) {
        try {
          const payload = JSON.parse(cached);
          const strip = side === 'home' ? payload.home_form : payload.away_form;
          if (Array.isArray(strip) && strip.length) return strip as ('W' | 'D' | 'L')[];
        } catch { /* fall through to the Turso index */ }
      }
      return formByTeam.get(team) ?? [];
    };

    const matches = await Promise.all(
      fixtures.map(async (f) => {
        const pred = stored.get(f.match_key);
        let prediction = null;

        if (pred) {
          const model = { home: pred.home_win, draw: pred.draw, away: pred.away_win };
          // Live consensus if we have it, else the snapshot taken with the call.
          let market: Market | null = marketByKey.get(f.match_key) ?? null;
          if (!market && pred.market_home !== null && pred.market_draw !== null && pred.market_away !== null) {
            market = { home: pred.market_home, draw: pred.market_draw, away: pred.market_away, books: 0 };
          } else if (market && pred.market_home === null) {
            // An older call with no snapshot yet: take it now, before kickoff.
            await savePrediction({
              matchKey: f.match_key, homeWin: pred.home_win, draw: pred.draw, awayWin: pred.away_win,
              market: { home: market.home, draw: market.draw, away: market.away },
            });
          }
          const blended = applyMarket(model, market, usedOdds(pred));
          const outcome = argmaxOutcome(blended.home, blended.draw, blended.away);
          prediction = {
            home_win: blended.home,
            draw: blended.draw,
            away_win: blended.away,
            outcome: OUTCOME_LETTER[outcome],
            model_probs: model,
            market_probs: market,
          };
        }

        return {
          id: f.match_key,
          matchKey: f.match_key,
          utcDate: f.kickoff_utc,
          status: f.status,
          statusGroup: f.status_group,
          // When the ETL last wrote this row. The UI needs it to tell a
          // genuinely live match from one frozen by a cron that fell behind.
          updatedAt: f.updated_at,
          minute: f.minute,
          competition: f.league,
          competitionId: f.league,
          country: f.country,
          season: f.season,
          venue: f.venue,
          source: f.source,
          homeScore: f.home_score,
          awayScore: f.away_score,
          homeTeamId: 0,
          awayTeamId: 0,
          homeTeam: { name: f.home_team, shortName: f.home_team },
          awayTeam: { name: f.away_team, shortName: f.away_team },
          homeCrest: badges.get(f.home_team) ?? null,
          awayCrest: badges.get(f.away_team) ?? null,
          // Form renders independently of the model server being reachable.
          homeForm: formFor(f.match_key, f.home_team, 'home'),
          awayForm: formFor(f.match_key, f.away_team, 'away'),
          prediction,
        };
      })
    );

    return NextResponse.json({ matches, freshness });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[FIXTURES] read failed:', message);
    return NextResponse.json(
      {
        matches: [],
        freshness: null,
        error: message.includes('not configured')
          ? 'Turso is not configured — set TURSO_FIXTURES_URL and TURSO_FIXTURES_TOKEN in web/.env.local'
          : message,
      },
      { status: 200 }
    );
  }
}
