import { NextResponse } from 'next/server';
import {
  argmaxOutcome, buildFormIndex, getFinished, getFreshness, getPredictions,
  getTeamBadges, getUpcoming, savePrediction, type FixtureRow,
} from '../../../lib/fixtures';
import { applyMarket } from '../../../lib/odds';
import { config } from '../../../lib/config';

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

type Probs = { home: number; draw: number; away: number };

interface ModelPrediction {
  index: number;
  home_win: number;
  draw: number;
  away_win: number;
  home_elo?: number | null;
  away_elo?: number | null;
  home_form?: ('W' | 'D' | 'L')[];
  away_form?: ('W' | 'D' | 'L')[];
  coverage?: { defaults_used?: number };
}

/**
 * Predict a whole slate in ONE call.
 *
 * The model server builds a single DMatrix for the batch, so ~90 fixtures come
 * back in well under a second. The previous shape — one HTTP request per
 * fixture, six at a time — was the slowest part of loading the dashboard.
 * A failure here is non-fatal: fixtures and live scores still render without
 * predictions.
 */
async function predictSlate(fixtures: FixtureRow[]): Promise<Map<string, ModelPrediction>> {
  const out = new Map<string, ModelPrediction>();
  if (fixtures.length === 0) return out;
  try {
    const res = await fetch(`${config.modelServerUrl}/predict/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fixtures: fixtures.map((f) => ({
          home_team: f.home_team,
          away_team: f.away_team,
          league: f.league,
          kickoff: f.kickoff_utc,
        })),
      }),
      signal: AbortSignal.timeout(30000),
    });
    if (!res.ok) return out;
    const data = await res.json();
    for (const p of (data.predictions ?? []) as ModelPrediction[]) {
      const fixture = fixtures[p.index];
      if (fixture) out.set(fixture.match_key, p);
    }
  } catch {
    /* model server offline -> no predictions, everything else still works */
  }
  return out;
}

const OUTCOME_LETTER: Record<number, 'H' | 'D' | 'A'> = { 1: 'H', 2: 'D', 3: 'A' };

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
    const missing = fixtures.filter(
      (f) => !stored.has(f.match_key) && f.home_team && f.away_team
    );
    const fresh = await predictSlate(missing);
    const modelForm = new Map<string, { home?: string[]; away?: string[] }>();
    for (const f of missing) {
      const p = fresh.get(f.match_key);
      if (!p) continue;
      await savePrediction({
        matchKey: f.match_key,
        homeWin: p.home_win, draw: p.draw, awayWin: p.away_win,
        payload: p,
      });
      stored.set(f.match_key, {
        match_key: f.match_key,
        home_win: p.home_win, draw: p.draw, away_win: p.away_win,
        predicted_outcome: argmaxOutcome(p.home_win, p.draw, p.away_win),
        market_home: null, market_draw: null, market_away: null,
        ai_summary: null, payload_json: JSON.stringify(p),
        updated_at: new Date().toISOString(),
      });
      modelForm.set(f.match_key, { home: p.home_form, away: p.away_form });
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
          // Odds are the one external call this app still makes, and it is
          // optional — no key means the model probabilities pass through.
          const { blended, market } = await applyMarket(f.home_team, f.away_team, model);
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
