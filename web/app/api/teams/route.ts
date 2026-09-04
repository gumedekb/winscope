import { GET as fixturesGet } from '../fixtures/route';

/**
 * Backwards-compatible alias for /api/fixtures.
 *
 * The dashboard used to call /api/teams for its match list; that route used to
 * read a `matches` table this app maintained itself. Fixtures now come from the
 * ETL's `fixtures` table, so this just forwards. Kept so older clients and
 * bookmarks keep working — prefer /api/fixtures in new code.
 */
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  return fixturesGet(request);
}
