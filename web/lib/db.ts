import { createClient, type Client } from '@libsql/client/web';

/**
 * Turso (libSQL) clients.
 *
 * There is ONE physical database (`fixture-…`), holding two tables:
 *   fixtures    — written by the data/ ETL. Live + upcoming + finished matches.
 *                 Finished rows are never deleted, which is what makes the
 *                 track record possible.
 *   predictions — written by this app. One row per fixture, joined to `fixtures`
 *                 on `match_key`.
 *
 * The four logical names below are kept from v2 so existing imports keep working;
 * they all resolve to that one database unless you split them later by setting
 * the per-DB env vars. Missing config fails loud on first use, not silently.
 */
const FALLBACK_URL =
  process.env.TURSO_FIXTURES_URL ||
  process.env.TURSO_DATABASE_URL ||
  process.env.TURSO_MATCHES_URL ||
  '';

const FALLBACK_TOKEN =
  process.env.TURSO_FIXTURES_TOKEN ||
  process.env.TURSO_AUTH_TOKEN ||
  process.env.TURSO_MATCHES_TOKEN ||
  '';

/** Turso shows you a libsql:// URL; the web client wants https://. */
export const toHttpUrl = (url: string) =>
  url.replace(/^libsql:\/\//, 'https://').replace(/^wss:\/\//, 'https://');

const clients = new Map<string, Client>();

const make = (url: string | undefined, token: string | undefined, name: string): Client => {
  const finalUrl = toHttpUrl((url || FALLBACK_URL).trim());
  const finalToken = (token || FALLBACK_TOKEN).trim();

  if (!finalUrl || finalUrl.includes('undefined')) {
    console.error(
      `[DB] ${name}: no URL — set TURSO_FIXTURES_URL (or TURSO_${name.toUpperCase()}_URL) in .env.local`
    );
    return {
      execute: () => { throw new Error(`Database "${name}" is not configured.`); },
      batch:   () => { throw new Error(`Database "${name}" is not configured.`); },
    } as unknown as Client;
  }

  // One physical connection per URL+token pair, shared by the logical names.
  const cacheKey = `${finalUrl}::${finalToken}`;
  const existing = clients.get(cacheKey);
  if (existing) return existing;

  const client = createClient({ url: finalUrl, authToken: finalToken });
  clients.set(cacheKey, client);
  return client;
};

export const fixturesDb   = make(process.env.TURSO_FIXTURES_URL,    process.env.TURSO_FIXTURES_TOKEN,    'fixtures');
export const matchesDb    = make(process.env.TURSO_MATCHES_URL,     process.env.TURSO_MATCHES_TOKEN,     'matches');
export const predictionsDb= make(process.env.TURSO_PREDICTIONS_URL, process.env.TURSO_PREDICTIONS_TOKEN, 'predictions');
export const predictedDb  = make(process.env.TURSO_PREDICTED_URL,   process.env.TURSO_PREDICTED_TOKEN,   'predicted');
export const historyDb    = make(process.env.TURSO_HISTORY_URL,     process.env.TURSO_HISTORY_TOKEN,     'history');

export const isConfigured = () => Boolean(FALLBACK_URL && FALLBACK_TOKEN);

/** `internationalDb` kept as an alias so older imports don't break. */
export { historyDb as internationalDb };
