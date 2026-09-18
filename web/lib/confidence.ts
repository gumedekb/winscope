import type { Match, Prediction } from '../app/types';

/**
 * One definition of "the model is confident", shared by the filter and anything
 * that labels a card.
 *
 * The model calls three mutually exclusive outcomes, so the three probabilities
 * sum to 1 and the highest of them is the strength of its own pick. Above 50%
 * that pick is the outright favourite by more than the other two combined —
 * which is the shortlist worth betting. It applies to a draw as well as to
 * either win, though in practice a draw almost never clears the bar: draws sit
 * around 25-30% across the training set, so this filter mostly surfaces strong
 * home and away calls.
 *
 * Note it is the BLENDED probability (model plus bookmakers, when odds are
 * configured) that the API sends down on `prediction`, i.e. the same numbers the
 * card's bar shows — not the raw `model_probs`.
 */

/** One outcome must clear this share for a match to count as confident. */
export const CONFIDENT_MIN = 0.5;

/** The probability sitting on the model's own call, or null if it has no numbers. */
export function topProbability(prediction?: Prediction | null): number | null {
  if (!prediction) return null;
  const ps = [prediction.home_win, prediction.draw, prediction.away_win];
  if (ps.some((p) => typeof p !== 'number' || !Number.isFinite(p))) return null;
  return Math.max(...ps);
}

/** Strictly above the threshold — 50% exactly is a coin flip, not a call. */
export function isConfident(match: Match, min: number = CONFIDENT_MIN): boolean {
  const top = topProbability(match.prediction);
  return top !== null && top > min;
}
