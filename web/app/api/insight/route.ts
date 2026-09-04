import { NextResponse } from 'next/server';
import { fixturesDb } from '../../../lib/db';
import { getPredictions } from '../../../lib/fixtures';
import { getTeamRecent } from '../../../lib/fixtures';
import { computeTiers, describeTiers, type BetTier } from '../../../lib/betTiers';
import { aiConfigured, generateInsight } from '../../../lib/ai';
import { getInsight, saveInsight } from '../../../lib/insights';

export const dynamic = 'force-dynamic';

/**
 * AI insight for ONE match, on demand only.
 *
 *   GET  ?match_key=…            -> the cached insight, or 204 if there is none.
 *                                   Never calls a provider. Safe to poll.
 *   POST { match_key, refresh }  -> generate. Returns the cache unless
 *                                   `refresh` is true, so a second click on the
 *                                   same match costs nothing.
 *
 * Both Gemini and OpenRouter are free tiers, so nothing here is ever triggered
 * by listing fixtures — only by a human pressing the button on one match.
 */

const PROMPT_RULES = `You are WinScope's betting analyst for Betway Soccer Tote pools.

You are given ONE fixture: a trained XGBoost model's probabilities, both clubs' recent
results, any bookmaker market snapshot, and THREE pre-computed bet suggestions.

CRITICAL RULES
- The three bets and every number attached to them are already decided and calculated.
  Do NOT change them, do NOT invent probabilities, odds or percentages of your own.
  Your job is to explain WHY each one is a reasonable play at its risk level.
- Use only the data given. Never invent injuries, suspensions, transfers, weather or news.
- Where the model and recent form disagree, say so plainly. An honest "this is close to a
  coin flip" is far more useful than false confidence.
- If a club has very little history, flag it — the model is weak on newly promoted sides.
- Keep every string under 240 characters. Plain text, no markdown, no emoji.

Return ONLY a JSON object of exactly this shape:
{
  "summary": "2-3 sentences on how the match should go and why.",
  "key_factors": ["the specific run or number that matters most", "..."],
  "tier_rationales": [
    {"tier": "low",    "rationale": "why this is the safe play"},
    {"tier": "medium", "rationale": "why this is the balanced play"},
    {"tier": "high",   "rationale": "why this is worth a punt, and what has to happen"}
  ],
  "model_commentary": "What the model is keying on, and where it might be weak here.",
  "confidence_notes": "How much to trust this: probability spread, history depth, model vs market."
}`;

function buildPrompt(input: {
  league: string;
  kickoff: string;
  homeTeam: string;
  awayTeam: string;
  probs: { home: number; draw: number; away: number };
  market: { home: number; draw: number; away: number } | null;
  tiers: BetTier[];
  homeForm: string[];
  awayForm: string[];
}): string {
  const pct = (n: number) => `${Math.round(n * 100)}%`;
  const lines = [
    PROMPT_RULES,
    '',
    'FIXTURE',
    `${input.homeTeam} vs ${input.awayTeam}`,
    `Competition: ${input.league}`,
    `Kick-off (UTC): ${input.kickoff}`,
    '',
    'MODEL PROBABILITIES',
    `Home win ${pct(input.probs.home)} · Draw ${pct(input.probs.draw)} · Away win ${pct(input.probs.away)}`,
  ];
  if (input.market) {
    lines.push(
      'BOOKMAKER CONSENSUS',
      `Home ${pct(input.market.home)} · Draw ${pct(input.market.draw)} · Away ${pct(input.market.away)}`
    );
  } else {
    lines.push('BOOKMAKER CONSENSUS: none captured for this fixture.');
  }
  lines.push(
    '',
    'RECENT RESULTS (most recent first, from our database)',
    `${input.homeTeam}: ${input.homeForm.length ? input.homeForm.join(', ') : 'no recent results stored'}`,
    `${input.awayTeam}: ${input.awayForm.length ? input.awayForm.join(', ') : 'no recent results stored'}`,
    '',
    'THE THREE BETS (fixed — explain these, do not change them)',
    describeTiers(input.tiers)
  );
  return lines.join('\n');
}

/** Recent results as short readable lines the model can reason over. */
async function formLines(team: string): Promise<string[]> {
  const rows = await getTeamRecent(team, 5);
  return rows.map((r) => {
    const opponent = r.home_team === team ? `vs ${r.away_team}` : `at ${r.home_team}`;
    const scored = r.home_team === team ? r.home_score : r.away_score;
    const conceded = r.home_team === team ? r.away_score : r.home_score;
    const letter = r.outcome === 2 ? 'D' : (r.outcome === 1) === (r.home_team === team) ? 'W' : 'L';
    return `${letter} ${scored}-${conceded} ${opponent} (${r.kickoff_utc.slice(0, 10)})`;
  });
}

async function loadContext(matchKey: string) {
  const rs = await fixturesDb.execute({
    sql: 'SELECT * FROM fixtures WHERE match_key = ?',
    args: [matchKey],
  });
  const f = rs.rows[0] as unknown as Record<string, unknown> | undefined;
  if (!f) return null;

  const preds = await getPredictions([matchKey]);
  const pred = preds.get(matchKey);
  if (!pred) return { fixture: f, pred: null };
  return { fixture: f, pred };
}

export async function GET(request: Request) {
  const matchKey = new URL(request.url).searchParams.get('match_key');
  if (!matchKey) {
    return NextResponse.json({ error: 'match_key is required' }, { status: 400 });
  }
  try {
    const cached = await getInsight(matchKey);
    if (!cached) return new NextResponse(null, { status: 204 });
    return NextResponse.json({ insight: cached, cached: true });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    const matchKey: string | undefined = body.match_key;
    const refresh: boolean = Boolean(body.refresh);
    if (!matchKey) {
      return NextResponse.json({ error: 'match_key is required' }, { status: 400 });
    }

    // Cache first — this is the whole point. Only an explicit refresh spends a call.
    if (!refresh) {
      const cached = await getInsight(matchKey);
      if (cached) return NextResponse.json({ insight: cached, cached: true });
    }

    if (!aiConfigured()) {
      return NextResponse.json(
        { error: 'No AI provider configured — set GEMINI_API_KEY or OPENROUTER_API_KEY in .env.local' },
        { status: 503 }
      );
    }

    const ctx = await loadContext(matchKey);
    if (!ctx) return NextResponse.json({ error: 'Match not found' }, { status: 404 });
    if (!ctx.pred) {
      return NextResponse.json(
        {
          error:
            'No model prediction for this match yet — the AI explains the model, so there is ' +
            'nothing to explain until the model server has run.',
        },
        { status: 409 }
      );
    }

    const { fixture, pred } = ctx;
    const homeTeam = String(fixture.home_team);
    const awayTeam = String(fixture.away_team);
    const probs = { home: pred.home_win, draw: pred.draw, away: pred.away_win };
    const market =
      pred.market_home !== null && pred.market_draw !== null && pred.market_away !== null
        ? { home: pred.market_home, draw: pred.market_draw, away: pred.market_away }
        : null;

    const tiers = computeTiers(probs, homeTeam, awayTeam, market);
    const [homeForm, awayForm] = await Promise.all([formLines(homeTeam), formLines(awayTeam)]);

    const prompt = buildPrompt({
      league: String(fixture.league),
      kickoff: String(fixture.kickoff_utc),
      homeTeam,
      awayTeam,
      probs,
      market,
      tiers,
      homeForm,
      awayForm,
    });

    const generated = await generateInsight(prompt);

    // Fold the AI's rationale into the tiers we computed, so the stored object
    // carries both the numbers and their explanation together.
    const tiersWithRationale: BetTier[] = tiers.map((t) => ({
      ...t,
      rationale: generated.tier_rationales.find((r) => r.tier === t.tier)?.rationale ?? '',
    }));

    await saveInsight({
      matchKey,
      payload: generated,
      tiers: tiersWithRationale,
      provider: generated.provider,
      model: generated.model,
      isRefresh: refresh,
    });

    const stored = await getInsight(matchKey);
    return NextResponse.json({ insight: stored, cached: false });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[INSIGHT] failed:', message);
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
