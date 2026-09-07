import type { Match } from '../app/types';

/**
 * One definition of "actually live", shared by every part of the UI.
 *
 * A fixture row keeps whatever `status_group` the ETL last wrote, and the ETL is
 * a cron job. When a run is late or skipped, a match that finished hours ago is
 * still sitting in Turso marked `in_play` — so status alone is not enough to put
 * a LIVE badge on screen. A row counts as live only while it is fresh enough
 * that the ETL would have corrected it by now.
 */

/**
 * How long a row may claim to be live before the UI stops believing it.
 * Sized to sit just above the ETL's run interval, so an ordinary gap between
 * cron ticks never trips it but a missed run does.
 */
export const LIVE_TRUST_MS = 2 * 60 * 60 * 1000;

/** Milliseconds since the ETL last wrote this row; Infinity if it never said. */
export function staleFor(match: Pick<Match, 'updatedAt'>, now = Date.now()): number {
  const written = match.updatedAt ? Date.parse(match.updatedAt) : NaN;
  return Number.isNaN(written) ? Infinity : now - written;
}

/** In play AND recently confirmed — the only thing that earns a LIVE badge. */
export function isLiveNow(match: Pick<Match, 'statusGroup' | 'updatedAt'>, now = Date.now()): boolean {
  return match.statusGroup === 'in_play' && staleFor(match, now) <= LIVE_TRUST_MS;
}

/** In play per the database, but too old to trust — almost certainly finished. */
export function isStalled(match: Pick<Match, 'statusGroup' | 'updatedAt'>, now = Date.now()): boolean {
  return match.statusGroup === 'in_play' && !isLiveNow(match, now);
}

/** "40m ago" / "3h ago" / "2d ago" — how long the row has sat unwritten. */
export function agoLabel(ms: number): string {
  if (!Number.isFinite(ms)) return 'unknown';
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
