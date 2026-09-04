# v2 → v3 Migration Notes

Scaffolded 2026-09-02 by lifting v2 (fifascope) and retargeting international → club.
Structure is in place and coherent; below is what still needs a human pass.

## ✅ Done
- 3-app structure: `web/` (Next.js combined), `model/` (FastAPI), `data/` (ETL).
- `web/lib/config.ts` — display metadata only; the provider ids live in `data/leagues.py`, and
  the football-API keys are gone from the web app entirely (see below).
- `web/lib/db.ts` — one Turso client pool; every logical name falls back to `TURSO_FIXTURES_*`.
- `web/lib/fixtures.ts` — typed read/write layer over `fixtures` + `predictions`.
- `web/app/api/fixtures` — the dashboard's source: live + upcoming from Turso, joined to
  predictions, with form strips and freshness stats. `/api/teams` is an alias of it.
- `web/app/api/sync` — reports what is in Turso; fetches nothing.
- **One writer for the football APIs.** The web app used to call football-data.org and
  API-Football directly from `/api/sync`, `/api/results`, `/api/sync-played` and
  `lib/recentMatches.ts` — with no rate limiting, bypassing the ETL's 90% cap on a free tier
  of 100 requests/day. All four are retired or repointed at Turso.
- Dashboard UI: live section with running scores and minute, league filter, data-freshness
  bar, form strips, track record scored off `match_key`.
- Frontend ported into Next.js: `"use client"` added, `services/api`→`api-client` (relative `/api`), FIFA logo/splash video removed, WinScope branding.
- `model/` — rewritten for v3.1-club: serves the 5-seed ensemble from `artifacts/`, adds
  `POST /predict/batch` (a 98-fixture slate in ~0.07s, vs ~90 separate HTTP calls before) and
  a `coverage` block on every response saying how much real club history backed it.
- `data/` — football-data.co.uk collector (no key), normaliser + team-alias map, GitHub Actions cron.

## ⚠️ Needs hand-tuning before first real predictions
1. ~~**Retrain the model on CLUB data.**~~ — **done.** Trained in Colab on
   `data/output/model_data.csv`: **v3.1-club**, 81 features, 5-seed XGBoost ensemble, 38,224
   matches over 11 leagues, held-out accuracy 0.511 / log-loss 0.994. Artifacts live in
   `model/artifacts/`; `model/main.py` serves them and `model/features.py` rebuilds the 81
   features from the exported stores. The v2 `train.py` / `pipeline.py` / `evaluate.py` are
   gone — training is the notebook's job now.
2. ~~**`web/app/api/sync-played/route.ts`**~~ — **done.** Retired (410). It pulled whole seasons
   from API-Football, outside the ETL's 90% cap. Historical results now come from the ETL.
3. ~~**`web/lib/recentMatches.ts`**~~ — **done.** Now reads finished club matches from the Turso
   `fixtures` table instead of football-data's World Cup endpoint.
4. **`web/lib/odds.ts`** — The Odds API uses per-league sport keys. Currently one default
   (`ODDS_SPORT_KEY`, defaults `soccer_epl`). Add a league→key map for full odds coverage.
5. **Team-name mapping** — `data/clean/team_aliases.json` is a starter. Grow it as sources
   disagree (this is the #1 data-merge bug). The model's `TEAM_ALIASES` should use the same map.
6. ~~**Turso schema**~~ — **done.** One DB, two tables: `fixtures` (ETL-written, finished matches
   retained) and `predictions` (web-written, auto-created). Both keyed on `match_key`.
7. ~~**`web/middleware.ts`**~~ — **done.** Deleted; it also set `Access-Control-Allow-Origin` to
   the caller's origin on every API route, which is worth not shipping now it is same-origin.
8. ~~**SA Betway Premiership**~~ — **partly done.** The ETL pulls it (API-Football league 288 +
   TheSportsDB 4802): 748 matches, complete for 2022/23–2024/25. 2025/26 is a hole the free
   tiers will not fill — see the data README. Women's leagues are still not covered.

## Not carried over (intentionally)
- International flag assets, WC endpoints, `BallDontLie`, the Railway frontend URL.
