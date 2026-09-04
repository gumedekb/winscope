import { NextResponse } from 'next/server';
import { getScoredHistory } from '../../../lib/fixtures';

export const dynamic = 'force-dynamic';

/**
 * Track record — every finished match joined to what the model predicted before
 * it was played.
 *
 * This is what the ETL's "never delete a finished fixture" rule was for: the
 * result stays in `fixtures` forever, `predictions` keeps the call, and they
 * join on `match_key`. Nothing has to be archived into a separate table.
 */
export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const from = searchParams.get('from');   // YYYY-MM-DD, inclusive
    const to = searchParams.get('to');       // YYYY-MM-DD, inclusive

    let rows = await getScoredHistory(1000);
    if (from) rows = rows.filter((r) => r.kickoff_utc.slice(0, 10) >= from);
    if (to) rows = rows.filter((r) => r.kickoff_utc.slice(0, 10) <= to);

    let evaluated = 0;
    let correct = 0;
    let confSum = 0;
    let confCount = 0;
    let modelLL = 0;
    let marketLL = 0;
    let benchN = 0;

    const clip = (p: number) => Math.min(0.999, Math.max(1e-6, p));
    const key: Record<number, 'h' | 'd' | 'a'> = { 1: 'h', 2: 'd', 3: 'a' };
    const letter: Record<number, 'H' | 'D' | 'A'> = { 1: 'H', 2: 'D', 3: 'A' };

    // Per-league tallies, plus the market baseline: what a punter who simply
    // backed the bookmakers' favourite would have scored on the same matches.
    // Beating that is the only interesting bar for the model.
    const perLeague = new Map<string, { league: string; evaluated: number; correct: number; market_correct: number; market_evaluated: number }>();
    const bucket = (league: string) => {
      let b = perLeague.get(league);
      if (!b) {
        b = { league, evaluated: 0, correct: 0, market_correct: 0, market_evaluated: 0 };
        perLeague.set(league, b);
      }
      return b;
    };
    let marketCorrect = 0;
    let marketEvaluated = 0;

    const matches = rows.map((r) => {
      const b = bucket(r.league);
      if (r.hit !== null) {
        evaluated++;
        b.evaluated++;
        if (r.hit) { correct++; b.correct++; }
      }
      // Market baseline: the bookmakers' own favourite on this match.
      if (r.market && r.outcome) {
        const favourite = r.market.h >= r.market.d && r.market.h >= r.market.a
          ? 1 : r.market.d >= r.market.a ? 2 : 3;
        marketEvaluated++;
        b.market_evaluated++;
        if (favourite === r.outcome) { marketCorrect++; b.market_correct++; }
      }
      const k = r.outcome ? key[r.outcome] : null;
      if (r.probs && k) {
        confSum += r.probs[k];
        confCount++;
        if (r.market) {
          // Negative log-likelihood of what actually happened: model vs market.
          modelLL += -Math.log(clip(r.probs[k]));
          marketLL += -Math.log(clip(r.market[k]));
          benchN++;
        }
      }
      return {
        match_id: r.match_key,
        match_key: r.match_key,
        kickoff_utc: r.kickoff_utc,
        league: r.league,
        home_team: r.home_team,
        away_team: r.away_team,
        home_score: r.home_score,
        away_score: r.away_score,
        predicted_outcome: r.predicted_outcome ? letter[r.predicted_outcome] : 'N/A',
        actual_outcome: r.outcome ? letter[r.outcome] : 'N/A',
        probs: r.probs,
        market: r.market,
        hit: r.hit,
      };
    });

    return NextResponse.json({
      summary: {
        total: matches.length,
        evaluated,
        correct,
        accuracy: evaluated > 0 ? correct / evaluated : null,
        avg_confidence_on_actual: confCount > 0 ? confSum / confCount : null,
        benchmark: benchN > 0
          ? { matches: benchN, model_log_loss: modelLL / benchN, market_log_loss: marketLL / benchN }
          : null,
        market_accuracy: marketEvaluated > 0 ? marketCorrect / marketEvaluated : null,
        market_evaluated: marketEvaluated,
        range: {
          from: from ?? (rows.length ? rows[rows.length - 1].kickoff_utc.slice(0, 10) : null),
          to: to ?? (rows.length ? rows[0].kickoff_utc.slice(0, 10) : null),
        },
      },
      per_league: Array.from(perLeague.values())
        .map((b) => ({
          ...b,
          accuracy: b.evaluated > 0 ? b.correct / b.evaluated : null,
          market_accuracy: b.market_evaluated > 0 ? b.market_correct / b.market_evaluated : null,
        }))
        .sort((a, b) => b.evaluated - a.evaluated || a.league.localeCompare(b.league)),
      matches,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[HISTORY] failed:', message);
    return NextResponse.json({ error: 'Failed to load track record', detail: message }, { status: 500 });
  }
}
