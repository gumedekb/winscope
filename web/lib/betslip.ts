import { predictionsDb } from './db';

/**
 * The betslip — one list per day, synced through Turso so the slip you build on
 * the desktop is on your phone at the counter.
 *
 * Snapshot-on-add is the important bit: the teams, the pick and the
 * probabilities are copied onto the row at the moment you add it. A fixture
 * leaves the "upcoming" view the second it kicks off, and the model's numbers
 * move when it re-predicts — the slip has to stay exactly what you decided to
 * bet, and stay gradeable afterwards.
 */

export interface BetslipRow {
  date: string;            // YYYY-MM-DD, the slip this row belongs to
  match_key: string;
  position: number;
  league: string;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  pick: number;            // 1 home, 2 draw, 3 away — same encoding as fixtures.outcome
  prob_home: number;
  prob_draw: number;
  prob_away: number;
  added_at: string;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS betslip (
    date        TEXT NOT NULL,
    match_key   TEXT NOT NULL,
    position    INTEGER NOT NULL,
    league      TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    pick        INTEGER NOT NULL,
    prob_home   REAL NOT NULL,
    prob_draw   REAL NOT NULL,
    prob_away   REAL NOT NULL,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (date, match_key)
)`;

let ready: Promise<void> | null = null;

export function ensureBetslipTable(): Promise<void> {
  if (!ready) {
    ready = (async () => {
      await predictionsDb.execute(SCHEMA);
      await predictionsDb.execute(
        'CREATE INDEX IF NOT EXISTS idx_betslip_date ON betslip(date, position)'
      );
    })().catch((e) => {
      ready = null;
      throw e;
    });
  }
  return ready;
}

/** Today in South Africa — the slip is for a matchday, not a UTC day. */
export function slipDate(now = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Johannesburg',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(now);
}

const toRow = (r: Record<string, unknown>): BetslipRow => ({
  date: String(r.date),
  match_key: String(r.match_key),
  position: Number(r.position),
  league: String(r.league),
  kickoff_utc: String(r.kickoff_utc),
  home_team: String(r.home_team),
  away_team: String(r.away_team),
  pick: Number(r.pick),
  prob_home: Number(r.prob_home),
  prob_draw: Number(r.prob_draw),
  prob_away: Number(r.prob_away),
  added_at: String(r.added_at),
});

export async function getSlip(date: string): Promise<BetslipRow[]> {
  await ensureBetslipTable();
  const rs = await predictionsDb.execute({
    sql: 'SELECT * FROM betslip WHERE date = ? ORDER BY position ASC',
    args: [date],
  });
  return (rs.rows as unknown as Record<string, unknown>[]).map(toRow);
}

export async function addToSlip(
  date: string,
  entry: Omit<BetslipRow, 'date' | 'position' | 'added_at'>
): Promise<void> {
  await ensureBetslipTable();
  const rs = await predictionsDb.execute({
    sql: 'SELECT COALESCE(MAX(position), 0) AS max_pos FROM betslip WHERE date = ?',
    args: [date],
  });
  const nextPos = Number((rs.rows[0] as unknown as Record<string, unknown>).max_pos) + 1;
  await predictionsDb.execute({
    sql: `INSERT INTO betslip
            (date, match_key, position, league, kickoff_utc, home_team, away_team,
             pick, prob_home, prob_draw, prob_away, added_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(date, match_key) DO NOTHING`,
    args: [
      date, entry.match_key, nextPos, entry.league, entry.kickoff_utc,
      entry.home_team, entry.away_team, entry.pick,
      entry.prob_home, entry.prob_draw, entry.prob_away,
      new Date().toISOString(),
    ],
  });
}

export async function removeFromSlip(date: string, matchKey: string): Promise<void> {
  await ensureBetslipTable();
  await predictionsDb.execute({
    sql: 'DELETE FROM betslip WHERE date = ? AND match_key = ?',
    args: [date, matchKey],
  });
  await resequence(date);
}

/** Close the gaps left by a removal so positions stay 1..n. */
async function resequence(date: string): Promise<void> {
  const rows = await getSlip(date);
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].position !== i + 1) {
      await predictionsDb.execute({
        sql: 'UPDATE betslip SET position = ? WHERE date = ? AND match_key = ?',
        args: [i + 1, date, rows[i].match_key],
      });
    }
  }
}

/** Swap a row with its neighbour. Up/down arrows, no drag-and-drop dependency. */
export async function moveInSlip(
  date: string,
  matchKey: string,
  direction: 'up' | 'down'
): Promise<void> {
  const rows = await getSlip(date);
  const index = rows.findIndex((r) => r.match_key === matchKey);
  if (index === -1) return;
  const target = direction === 'up' ? index - 1 : index + 1;
  if (target < 0 || target >= rows.length) return;

  const a = rows[index];
  const b = rows[target];
  await predictionsDb.batch([
    { sql: 'UPDATE betslip SET position = ? WHERE date = ? AND match_key = ?', args: [b.position, date, a.match_key] },
    { sql: 'UPDATE betslip SET position = ? WHERE date = ? AND match_key = ?', args: [a.position, date, b.match_key] },
  ]);
}

export interface GradedSlipRow extends BetslipRow {
  actual: number | null;
  home_score: number | null;
  away_score: number | null;
  status_group: string | null;
  hit: boolean | null;
}

/**
 * Grade a day's slip against real results — the "7/10 on 22 Aug" view.
 * Joins to `fixtures`, which keeps finished matches forever.
 */
export async function gradeSlip(date: string): Promise<GradedSlipRow[]> {
  await ensureBetslipTable();
  const rs = await predictionsDb.execute({
    sql: `SELECT b.*, f.outcome AS actual, f.home_score, f.away_score, f.status_group
          FROM betslip b
          LEFT JOIN fixtures f ON f.match_key = b.match_key
          WHERE b.date = ?
          ORDER BY b.position ASC`,
    args: [date],
  });
  return (rs.rows as unknown as Record<string, unknown>[]).map((r) => {
    const base = toRow(r);
    const actual = r.actual === null || r.actual === undefined ? null : Number(r.actual);
    return {
      ...base,
      actual,
      home_score: r.home_score === null || r.home_score === undefined ? null : Number(r.home_score),
      away_score: r.away_score === null || r.away_score === undefined ? null : Number(r.away_score),
      status_group: r.status_group ? String(r.status_group) : null,
      hit: actual === null ? null : actual === base.pick,
    };
  });
}

/** Which dates have a slip — for the drawer's day picker. */
export async function slipDates(limit = 30): Promise<string[]> {
  await ensureBetslipTable();
  const rs = await predictionsDb.execute({
    sql: 'SELECT DISTINCT date FROM betslip ORDER BY date DESC LIMIT ?',
    args: [limit],
  });
  return (rs.rows as unknown as Record<string, unknown>[]).map((r) => String(r.date));
}
