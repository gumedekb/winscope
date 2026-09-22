/**
 * Perm advice for the Betway Soccer Tote (Soccer 6 / 10 / 13).
 *
 * The model almost never calls a draw outright — on the hold-out it picked 2
 * draws in 4,080 matches while ~25% of results were draws — so a slip of
 * single picks loses a line every time a tight game ends level. On the Tote
 * you do not have to pick one outcome: covering two of the three on a match
 * ("perming" it) multiplies the lines and the stake, and multiplies the
 * chance the whole slip lands.
 *
 * The rule here says WHICH matches deserve it: a draw the model rates at 30%+
 * (draws are 25% on average, so that is a live draw), or a favourite under
 * 40% (no outcome is much more likely than the others — where draws happen).
 * Everything else stays a single.
 *
 * And WHAT to cover: the pick plus the DRAW (1X or X2), not "the two most
 * likely". The model under-rates draws by construction, so in a tight game it
 * still ranks X last; covering 1 and 2 would skip the one result a tight game
 * tends to produce. data/digest.py carries the same rules for the Telegram
 * message — keep them in step.
 */

export const PERM_DRAW_MIN = 0.30;
export const PERM_TOP_MAX = 0.40;

export type Code = '1' | 'X' | '2';
export interface Probs { home: number; draw: number; away: number }

export interface PermAdvice {
  /** Cover two outcomes on this match. */
  perm: boolean;
  /** The outcomes to mark, most likely first; one entry when not perming. */
  selections: Code[];
  /** Model probability that the covered outcomes include the result. */
  covered: number;
  reason: string | null;
}

const PROB_OF: Record<Code, (p: Probs) => number> = {
  '1': (p) => p.home, 'X': (p) => p.draw, '2': (p) => p.away,
};
const CODE_OF_PICK: Record<number, Code> = { 1: '1', 2: 'X', 3: '2' };

export function labelFor(code: Code, home: string, away: string): string {
  return code === '1' ? home : code === '2' ? away : 'Draw';
}

/** The slip's pick (1 home, 2 draw, 3 away) as a Tote code. */
export const pickCode = (pick: number): Code => CODE_OF_PICK[pick] ?? '1';

/**
 * The two outcomes to cover: the slip's pick plus the draw. A slip that already
 * picks the draw pairs it with the likelier of the two wins.
 */
export function permPair(probs: Probs, pick: number): Code[] {
  const own = pickCode(pick);
  if (own === 'X') return probs.home >= probs.away ? ['1', 'X'] : ['X', '2'];
  return own === '1' ? ['1', 'X'] : ['X', '2'];
}

/** Model probability that the result is one of `selections`. */
export const coveredBy = (probs: Probs, selections: Code[]): number =>
  selections.reduce((s, c) => s + PROB_OF[c](probs), 0);

/**
 * @param probs the model's three probabilities
 * @param pick  the pick on the slip (1 home, 2 draw, 3 away); the advice always keeps it
 */
export function permAdvice(probs: Probs, pick: number): PermAdvice {
  const top = Math.max(probs.home, probs.draw, probs.away);
  const own = pickCode(pick);

  let reason: string | null = null;
  if (probs.draw >= PERM_DRAW_MIN) reason = `draw at ${Math.round(probs.draw * 100)}%`;
  else if (top < PERM_TOP_MAX) reason = `no clear favourite (${Math.round(top * 100)}%)`;

  if (!reason) {
    return { perm: false, selections: [own], covered: PROB_OF[own](probs), reason: null };
  }
  const selections = permPair(probs, pick);
  return { perm: true, selections, covered: coveredBy(probs, selections), reason };
}

/** Lines, cost and the chance every line-decider lands, for a slip. */
export function slipMaths(
  rows: { selections: Code[]; covered: number }[],
  unitStake: number
): { lines: number; cost: number; chance: number; permed: number } {
  const lines = rows.reduce((n, r) => n * Math.max(1, r.selections.length), 1);
  const chance = rows.reduce((p, r) => p * Math.min(1, Math.max(0, r.covered)), rows.length ? 1 : 0);
  return {
    lines,
    cost: lines * Math.max(0, unitStake),
    chance,
    permed: rows.filter((r) => r.selections.length > 1).length,
  };
}
