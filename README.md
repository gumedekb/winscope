# WinScope v3

AI-assisted football predictor for **Betway Soccer Tote** pools. Three apps + Turso.
Full design notes live in the Obsidian vault (`WinScope App`).

```
winscope/
├── web/     Next.js (frontend + backend)  → Vercel     — dashboard + API routes
├── model/   FastAPI + XGBoost             → Render     — /predict, /predict/batch
└── data/    Python ETL                    → GitHub Actions — pull, clean, train.csv
```

Database: **Turso** — one DB, two tables: `fixtures` (written by data/, finished matches kept
forever) and `predictions` (written by web/). Both keyed on `match_key`.

## Data flow
```
CSVs + APIs ─▶ data/ (clean, normalise, dedupe) ─▶ model_data.csv ─▶ model/ (trains, serves)
                 │                                                        │
                 └────▶ Turso `fixtures` ◀── web/ reads ──▶ `predictions` ─┘
```
**data/ is the only thing that calls a football API.** It caps every provider at 90% of its
free tier and shares one persisted budget across runs; the web app reads Turso instead, so a
second uncapped caller can't blow the 100/day API-Football limit. Finished matches are never
deleted from Turso, which is what lets the web app score past predictions.

## Quick start
1. **data/**: `cd data && pip install -r requirements.txt && python pipeline.py`
   → writes `output/model_data.csv` + a coverage report, **offline and free**.
   Add `--with-api` for the Betway Premiership (SA) history and live/upcoming fixtures.
   Every API is capped at 90% of its free tier — `python pipeline.py --quota` shows what is left.
   See [data/README.md](data/README.md).
2. **model/**: `cd model && pip install -r requirements.txt && uvicorn main:app --reload --port 8000`
   → serves **v3.1-club** (81 features, 5-seed XGBoost ensemble, held-out accuracy 0.511) from
   `model/artifacts/`. Trained in Colab, not here. See [model/README.md](model/README.md).
3. **web/**: `cd web && npm install`, copy `.env.example` → `.env.local`, add the Turso URL +
   token, `npm run dev`. Dashboard shows live scores, upcoming fixtures per league, and the
   model's track record. See [web/README.md](web/README.md).

## Status
Scaffolded from v2 (fifascope), retargeted international → club. See **MIGRATION.md** for what still needs hand-tuning before first real predictions.
