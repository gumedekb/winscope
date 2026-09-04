# winscope-web

Next.js app — the dashboard **and** its API routes live together here (deploys to Vercel).
Big-picture design is in the Obsidian vault; migration state is in `../MIGRATION.md`.

## Layout (no `src/` — that's normal for Next.js App Router)
```
web/
├── app/
│   ├── components/   Dashboard, MatchCard, drawers, AiInsightPanel, SearchBar…
│   ├── api/          fixtures, predict, insight, betslip, history, sync…
│   ├── layout.tsx  page.tsx  globals.css  api-client.ts  types.ts
├── lib/            db.ts, fixtures.ts, ai.ts, insights.ts, betTiers.ts, betslip.ts, odds.ts
└── public/         favicon
```

## Run
```
npm install
cp .env.example .env.local   # Turso URL + token; AI keys optional
npm run dev
```

---

## Where the data comes from

```
data/ ETL ──writes──▶ Turso `fixtures` ──reads──▶ this app
                            ▲                        │
                            └─ predictions · ai_insights · betslip ─┘
```

**This app never calls a football API.** Fixtures, live scores and results all come from the
`fixtures` table that `data/pipeline.py` publishes.

That is deliberate. The ETL caps every provider at 90% of its free tier and persists one shared
budget (`data/output/.quota.json`); API-Football's free tier is **100 requests a day**. A "Sync"
button here that fetched fixtures itself would spend that budget outside the cap and eventually
get the key banned — so the routes that used to do it are retired (410).

To pull new matches: `cd ../data && python pipeline.py --live` (or let the Actions cron do it).

### Tables

| Table | Written by | Notes |
|---|---|---|
| `fixtures` | `data/` ETL | Live + upcoming + finished. Finished rows are **never deleted**. |
| `team_assets` | `data/badges.py` | Club crest URLs, keyed on the canonical club name. |
| `predictions` | this app | Model + market probabilities per fixture. |
| `ai_insights` | this app | Cached AI output. One row per match. |
| `betslip` | this app | One list per day. |

All keyed on **`match_key`** (`YYYY-MM-DD|Home|Away`) — the same key `model_data.csv` uses, so
scoring a prediction against its real result is a plain join.

---

## AI insights

Two providers, tried in order: **Gemini** (`GEMINI_API_KEY`), then **OpenRouter**
(`OPENROUTER_API_KEY`). Either alone is enough. Both are free tiers, which shapes the whole design:

- **Nothing is generated automatically.** Listing fixtures, opening a match, refreshing a
  prediction — none of it touches a provider. Only the **AI Insight** tab's button does, for the
  one match you are looking at.
- **Every result is cached in Turso.** Re-opening the same match is a ~300 ms database read, not a
  new call. Only the panel's **Refresh** button spends another one, and it says so on hover.
- **The three bets are computed in code, not by the AI.** `lib/betTiers.ts` derives them from the
  model's probabilities:
  - *Lower risk* — the safest double chance (`1X` / `X2` / `12`)
  - *Balanced* — the model's outright call
  - *Higher risk* — the biggest positive disagreement with the bookmakers, else the second favourite

  The AI is handed those selections with their probabilities, fair odds and edge, and asked only to
  explain them. An LLM asked to invent betting probabilities will produce confident nonsense, and a
  bet suggestion is exactly where that does damage.

> **Why Turso and not a local better-sqlite3 file?** This app deploys to Vercel, where the
> filesystem is ephemeral and not shared between lambda instances — a local cache would be wiped on
> every cold start and invisible to the next instance, so the same match would regenerate over and
> over, burning the free tier we are trying to protect. Turso also means an insight generated on
> the desktop is already there on the phone.

---

## Features

### 1. Matchday view + search
- Live matches are pulled into their own section at the top, with running score and minute; the
  page polls once a minute **only while something is in play**.
- **Search** filters cards live by team, league or country. Forgiving on purpose — accents and
  punctuation are folded, so "koln" finds *1. FC Köln* and "utd" finds *Sheffield Utd*, because
  Betway's names will not match ours exactly.
- **League chips** narrow the board to the competitions in the pool.
- **Status bar** shows counts and data age. Since this app cannot fetch fixtures itself, "why is
  this match missing?" is nearly always "the ETL has not run", so the bar says so.

### 2. Betslip
- **`+` on any card** adds it to today's slip; the button flips to a green ✓. Click again to remove.
- **One list per day**, keyed by the date **in South Africa** — a 21:00 SAST kickoff is still
  "today" to the person holding the slip.
- **Snapshot on add:** teams, pick and probabilities are copied onto the row at that moment, so the
  slip survives kick-off (when the fixture leaves the upcoming view) and stays gradeable.
- **Drawer** shows the slip in order with each pick and its confidence — the sheet you copy onto
  Betway. Reorder with up/down arrows, remove with the bin.
- **Graded automatically** once results arrive: per-row HIT/MISS and a "7/10" header.
- Covered matches only — the `+` is disabled until the model has a prediction.

### 3. Track record
- Hit rate, correct/evaluated, average confidence on the outcome that actually happened.
- **Market baseline** — what simply backing the bookmakers' favourite would have scored on the same
  matches. Beating that is the only bar that means anything.
- **Per-league breakdown**, model % beside market % for each.
- **Date range** filter.
- **Model vs market log-loss** over matches where a pre-match market snapshot was captured.

---

## Routes

| Route | What it does |
|---|---|
| `GET /api/fixtures` | Live + upcoming with predictions, form strips, freshness. `?league=` |
| `GET /api/teams` | Backwards-compatible alias of `/api/fixtures`. |
| `POST /api/predict` | One fixture's detail: model + market blend, recent form, `coverage`. **No AI.** |
| `GET /api/insight` | Cached insight, or `204`. Never calls a provider. |
| `POST /api/insight` | Generate. Returns cache unless `{"refresh":true}`. |
| `GET/POST /api/betslip` | Read the day's slip; `add` / `remove` / `move`. |
| `GET /api/history` | Track record. `?from=&to=` |
| `POST /api/sync` | Reports what is in Turso and whether it is stale. Fetches nothing. |
| `POST /api/retrain` | **Retired (410)** — the model is trained in Colab, not from here. |
| `GET /api/results`, `POST /api/sync-played` | **Retired (410)** — used to hit football APIs directly. |

---

## Degradation

- **No model server** → fixtures, live scores and form still render; cards say "model server
  offline"; the `+` and the AI button are disabled (there is no prediction to bet or explain).
- **No AI keys** → the panel says so instead of failing silently.
- **Gemini down** → OpenRouter takes over automatically (verified).
- **No Turso config** → a clear red banner naming the env vars.

---

## Known gaps

1. ~~The model is not trained on club data~~ — **done.** `model/` now serves **v3.1-club**
   (81 features, 5-seed ensemble, held-out accuracy 0.511). The dashboard predicts a full slate
   in one `POST /predict/batch` call rather than ~90 separate requests.
2. **Draws are never the top pick.** Expected for a 1X2 model — draws are ~25% of results and
   almost never the single most likely outcome. The draw *probability* still drives the
   lower-risk double-chance bet tier.
3. **`lib/odds.ts` uses one sport key** (`ODDS_SPORT_KEY`, default `soccer_epl`), so market blending
   and the edge column only populate for that league. A league→key map would fix it.
4. **Club crests** are generated initials badges — the ETL stores club *names* (what joins to the
   model), not provider crest ids.
