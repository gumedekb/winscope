# winscope-model — prediction server

FastAPI in front of the **v3.1-club** XGBoost ensemble. This app only *serves* the
model; training happens in a Colab notebook and its artifacts are dropped into
`artifacts/`.

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
curl localhost:8000/health
```

## What is in `artifacts/`

| File | What it is |
|---|---|
| `xgboost_seed0..4.ubj` | The ensemble. Probabilities are averaged — bit-identical to the exported `VotingClassifier`, without depending on a pickle's sklearn version. |
| `xgboost_tuned.pkl` | That `VotingClassifier`. Kept for reference; the server does not load it. |
| `features.json` | The 81 feature names, in the exact order the model expects. |
| `feature_defaults.json` | Training-set medians for the non-sparse features, used when a value is unknown. |
| `model_metadata.json` | Version, training window, test metrics, hyper-parameters, and `sparse_features`. |
| `elo_ratings.json` · `pi_ratings.json` | Club strength. |
| `team_form.json` | Rolling form (5 and 10), venue splits, last match date, and a W/D/L `form_summary` the dashboard renders. |
| `h2h_records.json` | Head-to-head rates. **Keys are alphabetical** (`"A|B"`), so `t1` is not necessarily the home side. |
| `season_tables.json` | Current league tables, for position and points-per-game. |
| `league_map.json` | League → the integer code the model was trained with. |

`winscope_model_artifacts.zip` is the original export. Everything in it is already
unpacked alongside, so it can be deleted.

## Model

- 81 features · 5-seed ensemble · classes `away_win / draw / home_win`
- Trained on 38,224 matches, 2016-08-05 → 2026-09-02, 11 leagues
- Held-out (2025/26 + 2026/27): **accuracy 0.511, log-loss 0.994, Brier 0.198**
- `use_odds: false` — the model does not see bookmaker odds; the web app blends
  them in separately.

**Draws are rarely the argmax.** That is expected for a 1X2 model: draws are ~25%
of results and almost never the single most likely outcome, so the top pick is
nearly always home or away. The draw *probability* is still meaningful, which is
why the double-chance bet tier in the web app uses it.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /health` | Liveness, seeds loaded, feature/team counts. |
| `GET /model` | Full metadata: version, training window, test metrics, leagues. |
| `GET /teams?league=` | Clubs the model knows (347). |
| `POST /predict` | One fixture. |
| `POST /predict/batch` | A whole slate in one call — 88 fixtures in ~0.07s. |

Request: `{"home_team": "...", "away_team": "...", "league": "...", "kickoff": "ISO date"}`.
`league` and `kickoff` are optional but improve the prediction (they drive the
league code, table position and days-rest features).

Every response carries a `coverage` block — whether each club was known, how many
prior matches back the form, and how many of the 81 features fell back to a
training-set average. A prediction with `defaults_used: 0` is fully informed; a
high count means the model is largely guessing from league averages.

## `features.py` — keep it in lock-step with the notebook

Serving rebuilds the same 81 features the notebook built. `features.py` is a
line-for-line port of `feature_row` in `train/colab_train.py` (Cell 11), and the
notebook's serving-path check proves that function reproduces the training
features exactly. Every fallback matches training too: an unknown club gets the
initial Elo, zero pi-ratings and the maximum rest days; a pair with no history
gets 0.5 / 0.25 / 0 head-to-head; a fixture in a season the table does not
cover gets an empty table; sparse features (shots, shots on target, corners,
odds) stay NaN instead of being filled.

If you change a feature in the notebook, change it here the same way, then
retrain and replace `artifacts/`. Two formulas that are easy to get wrong:

```python
pi_exp_gd  = f(home_pi["home"]) - f(away_pi["away"])   # f(r) = sign(r) * (10**(|r|/c) - 1), c = 3
sot_diff_10 = (home_sot_for - home_sot_against) - (away_sot_for - away_sot_against)   # 10-game form
```

## Deployment

`render.yaml` / `Procfile` run `uvicorn main:app`. `artifacts/` must ship with the
service — it is ~11 MB unzipped (or ~4 MB if you keep only the five `.ubj` files
and the JSON stores, dropping `xgboost_tuned.pkl` and the zip).

## Retraining

Not done here. Rebuild the dataset (`cd ../data && python pipeline.py --with-api`),
paste `train/colab_train.py` cell by cell into Colab, upload
`data/output/model_data.csv` when Cell 3 asks, then unzip the exported
`winscope_model_artifacts.zip` into `artifacts/` and restart. The old
`train.py` / `pipeline.py` / `evaluate.py` in this folder were v2
international-football code and have been removed.
