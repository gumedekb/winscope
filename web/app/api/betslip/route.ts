import { NextResponse } from 'next/server';
import {
  addToSlip, gradeSlip, moveInSlip, removeFromSlip, slipDate, slipDates,
} from '../../../lib/betslip';

export const dynamic = 'force-dynamic';

/**
 * The day's betslip.
 *
 *   GET  ?date=YYYY-MM-DD   -> that day's slip, graded where results exist.
 *   POST { action, ... }    -> add | remove | move
 *
 * Defaults to today in South Africa, not UTC — a slip is a matchday, and an
 * 21:00 SAST kickoff is still "today" to the person holding the slip.
 */
export async function GET(request: Request) {
  try {
    const date = new URL(request.url).searchParams.get('date') || slipDate();
    const [rows, dates] = await Promise.all([gradeSlip(date), slipDates()]);
    const settled = rows.filter((r) => r.hit !== null);
    return NextResponse.json({
      date,
      rows,
      dates,
      summary: {
        picks: rows.length,
        settled: settled.length,
        correct: settled.filter((r) => r.hit).length,
      },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[BETSLIP] read failed:', message);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const date: string = body.date || slipDate();
    const matchKey: string | undefined = body.match_key;
    if (!matchKey) {
      return NextResponse.json({ error: 'match_key is required' }, { status: 400 });
    }

    switch (body.action) {
      case 'add': {
        const required = ['league', 'kickoff_utc', 'home_team', 'away_team', 'pick'];
        for (const field of required) {
          if (body[field] === undefined || body[field] === null) {
            return NextResponse.json({ error: `${field} is required to add` }, { status: 400 });
          }
        }
        // Snapshot the numbers as they are right now — see lib/betslip.ts.
        await addToSlip(date, {
          match_key: matchKey,
          league: String(body.league),
          kickoff_utc: String(body.kickoff_utc),
          home_team: String(body.home_team),
          away_team: String(body.away_team),
          pick: Number(body.pick),
          prob_home: Number(body.prob_home ?? 0),
          prob_draw: Number(body.prob_draw ?? 0),
          prob_away: Number(body.prob_away ?? 0),
        });
        break;
      }
      case 'remove':
        await removeFromSlip(date, matchKey);
        break;
      case 'move':
        if (body.direction !== 'up' && body.direction !== 'down') {
          return NextResponse.json({ error: 'direction must be "up" or "down"' }, { status: 400 });
        }
        await moveInSlip(date, matchKey, body.direction);
        break;
      default:
        return NextResponse.json({ error: 'action must be add | remove | move' }, { status: 400 });
    }

    const rows = await gradeSlip(date);
    return NextResponse.json({ date, rows });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[BETSLIP] write failed:', message);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
