import { NextResponse } from 'next/server';
import { fixturesDb } from '../../../lib/db';
import {
  argmaxOutcome, ensurePredictionsTable, getPredictions, savePrediction,
} from '../../../lib/fixtures';
import { applyMarket } from '../../../lib/odds';
import { fetchFixtureRecent } from '../../../lib/recentMatches';
import { config } from '../../../lib/config';

export const dynamic = 'force-dynamic';

/**
 * Detail view for one fixture: model probabilities, market blend and both clubs'
 * recent form. Opening a match costs nothing but a database read.
 *
 * Deliberately NO AI here. This route runs every time a card is opened, and both
 * AI providers are free tiers — generating an insight on open would burn the
 * daily allowance on matches nobody asked about. The AI lives behind an explicit
 * button at /api/insight and is cached in Turso.
 *
 * Keyed on `match_key` — the same key the ETL and model_data.csv use — so the
 * prediction saved here is exactly what the track record scores later.
 */

const OUTCOME_LETTER: Record<number, 'H' | 'D' | 'A'> = { 1: 'H', 2: 'D', 3: 'A' };

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const matchKey: string | undefined = body.match_key || body.match_id;
    const forceRefresh: boolean = Boolean(body.force_refresh);
    if (!matchKey) {
      return NextResponse.json({ error: 'match_key is required' }, { status: 400 });
    }

    await ensurePredictionsTable();
    const rs = await fixturesDb.execute({
      sql: 'SELECT * FROM fixtures WHERE match_key = ?',
      args: [matchKey],
    });
    const fixture = rs.rows[0] as unknown as Record<string, unknown> | undefined;
    if (!fixture) return NextResponse.json({ error: 'Match not found' }, { status: 404 });

    const homeTeam = String(fixture.home_team);
    const awayTeam = String(fixture.away_team);
    const league = String(fixture.league);

    // --- model probabilities (cached unless forced) ---------------------
    const stored = await getPredictions([matchKey]);
    let pred = stored.get(matchKey);
    let payload: Record<string, unknown> | null = pred?.payload_json
      ? JSON.parse(pred.payload_json)
      : null;

    if (!pred || forceRefresh) {
      try {
        const res = await fetch(`${config.modelServerUrl}/predict`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ home_team: homeTeam, away_team: awayTeam, neutral: false, league }),
          signal: AbortSignal.timeout(20000),
        });
        if (res.ok) {
          const data = await res.json();
          const home = Number(data.home_win ?? data.home_win_pct ?? 0);
          const draw = Number(data.draw ?? data.draw_pct ?? 0);
          const away = Number(data.away_win ?? data.away_win_pct ?? 0);
          if (home + draw + away > 0) {
            payload = data;
            await savePrediction({
              matchKey, homeWin: home, draw, awayWin: away, payload: data,
            });
            pred = {
              match_key: matchKey, home_win: home, draw, away_win: away,
              predicted_outcome: argmaxOutcome(home, draw, away),
              market_home: null, market_draw: null, market_away: null,
              ai_summary: null, payload_json: JSON.stringify(data),
              updated_at: new Date().toISOString(),
            };
          }
        }
      } catch (e: unknown) {
        console.error('[PREDICT] model server:', e instanceof Error ? e.message : e);
      }
    }

    if (!pred) {
      return NextResponse.json(
        { error: 'No prediction available — is the model server running at ' + config.modelServerUrl + '?' },
        { status: 503 }
      );
    }

    // --- recent form, straight from the fixtures table -------------------
    const { home: homeHistory, away: awayHistory } =
      await fetchFixtureRecent(homeTeam, awayTeam, 5);

    // --- market blend ----------------------------------------------------
    const modelProbs = { home: pred.home_win, draw: pred.draw, away: pred.away_win };
    const { blended, market } = await applyMarket(homeTeam, awayTeam, modelProbs);

    // Snapshot the pre-match market once — odds vanish after kickoff, and the
    // track record needs them to benchmark model against market.
    if (market && pred.market_home === null) {
      await savePrediction({
        matchKey, homeWin: pred.home_win, draw: pred.draw, awayWin: pred.away_win,
        market: { home: market.home, draw: market.draw, away: market.away },
      });
    }

    const outcome = argmaxOutcome(blended.home, blended.draw, blended.away);
    return NextResponse.json({
      match_key: matchKey,
      home_team: homeTeam,
      away_team: awayTeam,
      league,
      kickoff_utc: fixture.kickoff_utc,
      status: fixture.status,
      home_score: fixture.home_score,
      away_score: fixture.away_score,
      home_win: blended.home,
      draw: blended.draw,
      away_win: blended.away,
      outcome: OUTCOME_LETTER[outcome],
      model_probs: modelProbs,
      market_probs: market,
      home_elo: payload?.home_elo,
      away_elo: payload?.away_elo,
      model_version: payload?.model_version,
      // How much of the prediction came from real club history vs training-set
      // averages — lets the UI mark a thin call rather than showing it with the
      // same confidence as a well-known derby.
      coverage: payload?.coverage,
      home_form: (homeHistory ?? []).map((m) => m.result).filter(Boolean),
      away_form: (awayHistory ?? []).map((m) => m.result).filter(Boolean),
      home_team_history: homeHistory ?? [],
      away_team_history: awayHistory ?? [],
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[PREDICT] failed:', message);
    return NextResponse.json({ error: 'Internal server error', detail: message }, { status: 500 });
  }
}
