"""
WinScope - Google Colab training script for the club model.

Copy each '# ── Cell N' block into its own Colab cell, in order.
  Input : data/output/model_data.csv   (upload when Cell 3 asks, or read from Drive)
  Output: winscope_model_artifacts.zip -> unzip into model/artifacts/

Features (all computed from the state BEFORE each match, across all leagues):
  Elo, pi-ratings (attack/defence, home & away), rolling form on goals AND on shots /
  shots-on-target / corners, venue-specific form, head-to-head, rest days, season
  standings (points, position, gap), league. The first WARMUP_SEASONS only feed the
  ratings and are never trained or scored on.

Artifacts: xgboost_seed*.ubj (the ensemble members), xgboost_tuned.pkl (the same
ensemble as a VotingClassifier), and the JSON stores (elo_ratings, pi_ratings,
team_form, h2h_records, season_tables, features, feature_defaults, league_map,
label_map, model_metadata). Cell 11 `feature_row` is the reference for how
model/features.py must rebuild the feature row from those stores.
"""

# ── Cell 1: Install & imports ──────────────────────────────────────────────
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "xgboost==2.1.1", "scikit-learn==1.5.2", "joblib==1.4.2"])

import os, json, zipfile, warnings
import numpy as np
import pandas as pd
import xgboost as xgb
import joblib
from sklearn.ensemble import VotingClassifier
from sklearn.metrics import (accuracy_score, log_loss, brier_score_loss,
                             classification_report, confusion_matrix)
from sklearn.utils.class_weight import compute_sample_weight

warnings.filterwarnings("ignore")
pd.set_option("display.width", 160)
print("xgboost", xgb.__version__, "| pandas", pd.__version__)


# ── Cell 2: Config ─────────────────────────────────────────────────────────
CSV_PATH = "model_data.csv"
OUT_DIR = "artifacts"
SEED = 42

WARMUP_SEASONS = ["2016/17", "2017/18"]   # warm up Elo/form/ratings only; never trained or scored on
VALID_SEASON = "2024/25"                  # used only for early stopping
TEST_SEASONS = ["2025/26", "2026/27"]     # held out, never seen while training

USE_ODDS = False   # True = add bookmaker-implied probabilities as features (see Cell 9d).
                   # Biggest gain available, but /predict must then be given odds per fixture.
N_SEEDS = 5        # final model = average of N XGBoost fits with different seeds (1 = single model)

INITIAL_ELO = 1000.0
ELO_K = 20.0
ELO_HOME_ADV = 60.0                       # Elo points credited to the home side in the expectation
PI_LAMBDA, PI_GAMMA, PI_C = 0.035, 0.7, 3.0   # pi-ratings (Constantinou & Fenton) learning rates
FORM_WINDOWS = (5, 10)
VENUE_WINDOW = 10                         # last N home games (home team) / away games (away team)
MAX_REST_DAYS = 30

STAT_COLUMNS = ["home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
                "home_corners", "away_corners"]

LABEL_MAP = {"away_win": 0, "draw": 1, "home_win": 2}   # must match model/main.py
CLASSES = ["away_win", "draw", "home_win"]
OUTCOME_TO_TARGET = {1: 2, 2: 1, 3: 0}                  # csv outcome (1=H,2=D,3=A) -> class index


# ── Cell 3: Load the CSV ───────────────────────────────────────────────────
try:
    from google.colab import files
    if not os.path.exists(CSV_PATH):
        uploaded = files.upload()           # choose data/output/model_data.csv
        CSV_PATH = next(iter(uploaded))
except ImportError:
    pass                                    # outside Colab: CSV_PATH is used as-is

# Alternative: read from Google Drive instead of uploading every session.
# Uncomment the 3 lines below and comment out the try/except block above.
# from google.colab import drive
# drive.mount("/content/drive")
# CSV_PATH = "/content/drive/MyDrive/winscope/model_data.csv"

raw = pd.read_csv(CSV_PATH)
print(raw.shape)
raw.head()


# ── Cell 4: Clean & inspect ────────────────────────────────────────────────
df = raw.copy()
df["date"] = pd.to_datetime(df["date"], errors="coerce")
for c in ("home_score", "away_score", "outcome", "odds_home", "odds_draw", "odds_away"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
for c in STAT_COLUMNS:                      # older CSVs without match stats still work (all-NaN column)
    df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan

df = df.dropna(subset=["date", "home_team", "away_team", "home_score", "away_score", "outcome"])
df = df[df["home_team"] != df["away_team"]]
df = (df.sort_values(["date", "league", "home_team"])
        .drop_duplicates(["date", "home_team", "away_team"], keep="last")
        .reset_index(drop=True))
df["home_score"] = df["home_score"].astype(int)
df["away_score"] = df["away_score"].astype(int)
df["target"] = df["outcome"].map(OUTCOME_TO_TARGET).astype(int)

print(f"{len(df):,} matches   {df.date.min().date()} -> {df.date.max().date()}")
print(f"teams: {pd.concat([df.home_team, df.away_team]).nunique()}   "
      f"odds coverage: {df.odds_home.notna().mean():.1%}   "
      f"shot stats coverage: {df.home_shots_on_target.notna().mean():.1%}")
print("\nclass balance:")
print(df["target"].map(dict(enumerate(CLASSES))).value_counts(normalize=True).round(3))
print("\nmatches per season:")
print(df.groupby("season").size())


# ── Cell 5: Feature engineering ────────────────────────────────────────────
def season_of(date):
    """Season label for a kickoff date (Jul-Jun), matching the CSV convention."""
    y = date.year if date.month >= 7 else date.year - 1
    return f"{y}/{(y + 1) % 100:02d}"


def elo_expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def elo_margin_mult(gd):
    gd = abs(gd)
    return 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8.0)


def pi_expected_gd(r):
    return np.sign(r) * (10 ** (abs(r) / PI_C) - 1)


def league_pos(table, team):
    """1 + number of teams strictly ahead on (points, goal difference)."""
    pts, gd = table.get(team, (0, 0, 0))[:2]
    return 1 + sum(1 for t, (p, g, _) in table.items() if t != team and (p, g) > (pts, gd))


def run_sequential(d):
    """One chronological pass. Every value is the state BEFORE the match (no leakage).
    Returns the features plus the final Elo / pi / H2H / rest / standings stores."""
    elo, pi, pairs, last_seen, tables, latest_season = {}, {}, {}, {}, {}, {}
    names = ("home_elo", "away_elo", "home_pi_home", "home_pi_away", "away_pi_home", "away_pi_away",
             "pi_exp_gd", "h2h_home_winrate", "h2h_draw_rate", "h2h_matches_count",
             "home_days_rest", "away_days_rest", "home_season_ppg", "away_season_ppg",
             "home_season_played", "away_season_played", "home_season_gd", "away_season_gd",
             "home_league_pos", "away_league_pos")
    cols = {k: [] for k in names}

    for h, a, hs, as_, dt, lg, season in zip(d.home_team, d.away_team, d.home_score, d.away_score,
                                             d.date, d.league, d.season):
        eh, ea = elo.get(h, INITIAL_ELO), elo.get(a, INITIAL_ELO)
        ph, pa = pi.setdefault(h, [0.0, 0.0]), pi.setdefault(a, [0.0, 0.0])
        cols["home_elo"].append(eh); cols["away_elo"].append(ea)
        cols["home_pi_home"].append(ph[0]); cols["home_pi_away"].append(ph[1])
        cols["away_pi_home"].append(pa[0]); cols["away_pi_away"].append(pa[1])
        exp_gd = pi_expected_gd(ph[0]) - pi_expected_gd(pa[1])
        cols["pi_exp_gd"].append(exp_gd)

        key = (h, a) if h < a else (a, h)
        st = pairs.setdefault(key, {"n": 0, "w1": 0, "w2": 0, "d": 0})
        if st["n"]:
            wins_h = st["w1"] if key[0] == h else st["w2"]
            cols["h2h_home_winrate"].append(wins_h / st["n"])
            cols["h2h_draw_rate"].append(st["d"] / st["n"])
        else:
            cols["h2h_home_winrate"].append(0.5)
            cols["h2h_draw_rate"].append(0.25)
        cols["h2h_matches_count"].append(st["n"])

        cols["home_days_rest"].append(min((dt - last_seen[h]).days, MAX_REST_DAYS) if h in last_seen else MAX_REST_DAYS)
        cols["away_days_rest"].append(min((dt - last_seen[a]).days, MAX_REST_DAYS) if a in last_seen else MAX_REST_DAYS)
        last_seen[h] = last_seen[a] = dt

        table = tables.setdefault((lg, season), {})
        latest_season[lg] = season
        for side, team in (("home", h), ("away", a)):
            pts, gd, played = table.get(team, (0, 0, 0))
            cols[f"{side}_season_ppg"].append(pts / played if played else np.nan)
            cols[f"{side}_season_played"].append(played)
            cols[f"{side}_season_gd"].append(gd)
            cols[f"{side}_league_pos"].append(league_pos(table, team))

        # ---- updates ----
        s_h = 1.0 if hs > as_ else (0.5 if hs == as_ else 0.0)
        delta = ELO_K * elo_margin_mult(hs - as_) * (s_h - elo_expected(eh + ELO_HOME_ADV, ea))
        elo[h] = eh + delta
        elo[a] = ea - delta

        err = abs((hs - as_) - exp_gd)
        psi = PI_C * np.log10(1 + err) * (1 if (hs - as_) > exp_gd else -1)
        ph[0] += PI_LAMBDA * psi;            ph[1] += PI_GAMMA * PI_LAMBDA * psi
        pa[1] += PI_LAMBDA * (-psi);         pa[0] += PI_GAMMA * PI_LAMBDA * (-psi)

        st["n"] += 1
        if hs > as_:
            st["w1" if key[0] == h else "w2"] += 1
        elif hs < as_:
            st["w1" if key[0] == a else "w2"] += 1
        else:
            st["d"] += 1

        hp, ap = (3, 0) if hs > as_ else ((0, 3) if hs < as_ else (1, 1))
        for team, p, g in ((h, hp, hs - as_), (a, ap, as_ - hs)):
            pts, gd, played = table.get(team, (0, 0, 0))
            table[team] = (pts + p, gd + g, played + 1)

    feats = pd.DataFrame(cols, index=d.index)
    feats["elo_diff"] = feats.home_elo - feats.away_elo
    feats["pi_diff"] = (feats.home_pi_home + feats.home_pi_away) / 2 - (feats.away_pi_home + feats.away_pi_away) / 2
    feats["season_pos_diff"] = feats.home_league_pos - feats.away_league_pos
    feats["season_ppg_diff"] = feats.home_season_ppg - feats.away_season_ppg
    stores = {"elo": elo, "pi": pi, "pairs": pairs, "last_seen": last_seen,
              "tables": {lg: (s, tables[(lg, s)]) for lg, s in latest_season.items()}}
    return feats, stores


def team_long_frame(d):
    """One row per (match, team) so rolling form is computed per team."""
    base = {"idx": d.index, "date": d.date}
    home = pd.DataFrame({**base, "team": d.home_team, "gf": d.home_score, "ga": d.away_score, "is_home": 1,
                         "sot_for": d.home_shots_on_target, "sot_against": d.away_shots_on_target,
                         "shots_for": d.home_shots, "shots_against": d.away_shots,
                         "corners_for": d.home_corners, "corners_against": d.away_corners})
    away = pd.DataFrame({**base, "team": d.away_team, "gf": d.away_score, "ga": d.home_score, "is_home": 0,
                         "sot_for": d.away_shots_on_target, "sot_against": d.home_shots_on_target,
                         "shots_for": d.away_shots, "shots_against": d.home_shots,
                         "corners_for": d.away_corners, "corners_against": d.home_corners})
    long = pd.concat([home, away]).sort_values(["team", "date", "idx"]).reset_index(drop=True)
    long["win"] = (long.gf > long.ga).astype(int)
    long["draw"] = (long.gf == long.ga).astype(int)
    long["pts"] = long.win * 3 + long.draw
    return long


FORM_STATS = (("win", "win_rate"), ("draw", "draw_rate"), ("gf", "goals_for"), ("ga", "goals_against"), ("pts", "ppg"))
STAT_STATS = tuple((s, s) for s in ("sot_for", "sot_against", "shots_for", "shots_against", "corners_for", "corners_against"))
VENUE_STATS = (("pts", "ppg"), ("gf", "goals_for"), ("ga", "goals_against"))
SPARSE_TOKENS = ("odds_", "sot_", "shots_", "corners_")   # features left NaN when unknown (no median fill)


def rolling_form(long):
    """Rolling means over the team's PREVIOUS matches (shift(1) excludes the current one)."""
    out = pd.DataFrame(index=long.index)
    g = long.groupby("team")
    for w in FORM_WINDOWS:
        for src, name in FORM_STATS + STAT_STATS:
            out[f"form_{name}_{w}"] = g[src].transform(lambda s: s.shift(1).rolling(w, min_periods=1).mean())
    gv = long.groupby(["team", "is_home"])
    for src, name in VENUE_STATS:
        out[f"venue_{name}"] = gv[src].transform(lambda s: s.shift(1).rolling(VENUE_WINDOW, min_periods=1).mean())
    out["matches_played"] = g.cumcount()
    return out


def add_diffs(X):
    X["ppg_diff_5"] = X.home_form_ppg_5 - X.away_form_ppg_5
    X["ppg_diff_10"] = X.home_form_ppg_10 - X.away_form_ppg_10
    X["venue_ppg_diff"] = X.home_venue_ppg - X.away_venue_ppg
    X["sot_diff_10"] = (X.home_form_sot_for_10 - X.home_form_sot_against_10) - (X.away_form_sot_for_10 - X.away_form_sot_against_10)
    return X


def build_features(d, league_map):
    d = d.sort_values("date", kind="stable")      # keep d's index so X aligns with the labels row-for-row
    X, stores = run_sequential(d)

    long = team_long_frame(d)
    form = rolling_form(long)
    long = pd.concat([long, form], axis=1)
    home_side = long[long.is_home == 1].set_index("idx")
    away_side = long[long.is_home == 0].set_index("idx")
    for c in form.columns:
        X[f"home_{c}"] = home_side[c].reindex(d.index).to_numpy()
        X[f"away_{c}"] = away_side[c].reindex(d.index).to_numpy()

    X = add_diffs(X)
    X["league_code"] = d.league.map(league_map).astype(float)
    if USE_ODDS:
        inv = 1.0 / d[["odds_home", "odds_draw", "odds_away"]]
        X[["odds_p_home", "odds_p_draw", "odds_p_away"]] = inv.div(inv.sum(axis=1), axis=0).to_numpy()

    stores["long"] = long
    return X, stores


# ── Cell 6: Build features & split ─────────────────────────────────────────
LEAGUE_MAP = {lg: i for i, lg in enumerate(sorted(df.league.unique()))}
X_all, STORES = build_features(df, LEAGUE_MAP)
FEATURES = list(X_all.columns)
SPARSE_FEATURES = [c for c in FEATURES if any(t in c for t in SPARSE_TOKENS)]

is_test = df.season.isin(TEST_SEASONS).to_numpy()
is_valid = df.season.eq(VALID_SEASON).to_numpy()
is_warmup = df.season.isin(WARMUP_SEASONS).to_numpy()
is_train = ~(is_test | is_valid | is_warmup)
is_model = ~is_warmup                              # everything the final model is refit on

# NaN = a team's first match. Fill with TRAIN medians; the serving side uses the same values.
# Sparse features (odds, shot stats) stay NaN so XGBoost learns an explicit "unknown" branch.
FEATURE_DEFAULTS = (X_all.loc[is_train, [c for c in FEATURES if c not in SPARSE_FEATURES]]
                    .median().round(4).to_dict())
X_all = X_all.fillna(FEATURE_DEFAULTS)

y_all = df["target"].to_numpy()
X_tr, y_tr = X_all[is_train], y_all[is_train]
X_va, y_va = X_all[is_valid], y_all[is_valid]
X_te, y_te = X_all[is_test], y_all[is_test]
print(f"features: {len(FEATURES)}   warm-up: {is_warmup.sum():,}   train: {len(X_tr):,}   "
      f"valid: {len(X_va):,}   test: {len(X_te):,}")
print("\nshare missing in the sparse features (train rows):")
print(X_tr[SPARSE_FEATURES].isna().mean().round(3).to_string())


# ── Cell 7: Train XGBoost ──────────────────────────────────────────────────
from sklearn.ensemble import VotingClassifier
from sklearn.utils.class_weight import compute_sample_weight


def make_weights(y, dates):
    # Keep ONE of the three `w = ...` lines (measured numbers are in the notes at the end of Cell 9).
    w = np.ones(len(y))                                   # no weighting: best log-loss, never picks a draw
    # Alternative A - sqrt class balancing: calls a few % of matches as draws.
    # Uncomment the next line and comment out the `np.ones` line above:
    # w = np.sqrt(compute_sample_weight("balanced", y))
    # Alternative B - full class balancing: many draw picks, worse everywhere else.
    # w = compute_sample_weight("balanced", y)
    # Time decay - recent seasons count more. Stacks on top of any option above:
    # age_years = (dates.max() - dates).dt.days.to_numpy() / 365.25
    # w = w * np.exp(-age_years / 3.0)
    return w


PARAMS = dict(
    objective="multi:softprob",
    n_estimators=3000, learning_rate=0.02, max_depth=4,
    subsample=0.8, colsample_bytree=0.6, min_child_weight=10,
    gamma=0.1, reg_alpha=0.1, reg_lambda=2.0,
    eval_metric="mlogloss", early_stopping_rounds=150,
    tree_method="hist", random_state=SEED, n_jobs=-1,
)


def fit_ensemble(X, y, w, n_estimators):
    """N_SEEDS XGBoost fits with different seeds, probabilities averaged (VotingClassifier is
    plain sklearn, so joblib.load works unchanged)."""
    base = {**PARAMS, "n_estimators": n_estimators}
    base.pop("early_stopping_rounds")
    if N_SEEDS == 1:
        return xgb.XGBClassifier(**base).fit(X, y, sample_weight=w)
    members = [(f"xgb{i}", xgb.XGBClassifier(**{**base, "random_state": SEED + i})) for i in range(N_SEEDS)]
    return VotingClassifier(members, voting="soft").fit(X, y, sample_weight=w)


w_tr = make_weights(y_tr, df.loc[is_train, "date"])
model = xgb.XGBClassifier(**PARAMS)                        # single fit, early-stopped on the validation season
model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=250)
BEST_ITER = int(model.best_iteration) + 1
print(f"\nbest iteration: {BEST_ITER}   valid mlogloss: {model.best_score:.4f}")

model_ens = fit_ensemble(X_tr, y_tr, w_tr, BEST_ITER)      # what actually ships (refit on all data in Cell 10)
print(f"{N_SEEDS}-seed ensemble trained")


# ── Cell 8: Evaluate on the held-out seasons ───────────────────────────────
def evaluate(name, proba, y):
    pred = proba.argmax(1)
    onehot = np.eye(3)[y]
    m = {
        "accuracy": round(float(accuracy_score(y, pred)), 4),
        "log_loss": round(float(log_loss(y, proba, labels=[0, 1, 2])), 4),
        "brier": round(float(np.mean([brier_score_loss(onehot[:, k], proba[:, k]) for k in range(3)])), 4),
        "n": int(len(y)),
    }
    print(f"{name:<30} acc={m['accuracy']:.4f}  logloss={m['log_loss']:.4f}  brier={m['brier']:.4f}  n={m['n']:,}")
    return m


print(f"=== TEST SET {TEST_SEASONS} ===")
evaluate("xgboost single", model.predict_proba(X_te), y_te)
proba_te = model_ens.predict_proba(X_te)
pred_te = proba_te.argmax(1)
TEST_METRICS = evaluate(f"xgboost {N_SEEDS}-seed ensemble", proba_te, y_te)

prior = np.tile(np.bincount(y_tr, minlength=3) / len(y_tr), (len(y_te), 1))
evaluate("baseline: class prior", prior, y_te)

has_odds = df.loc[is_test, "odds_home"].notna().to_numpy()
if has_odds.any():
    inv = 1.0 / df.loc[is_test, ["odds_away", "odds_draw", "odds_home"]].to_numpy()[has_odds]
    evaluate("bookmaker (rows with odds)", inv / inv.sum(1, keepdims=True), y_te[has_odds])
    evaluate("ensemble (same rows)", proba_te[has_odds], y_te[has_odds])

print("\n" + classification_report(y_te, pred_te, target_names=CLASSES, digits=3))
print("confusion matrix (rows = actual, cols = predicted)")
print(pd.DataFrame(confusion_matrix(y_te, pred_te), index=CLASSES, columns=CLASSES))

by_league = pd.DataFrame({"league": df.loc[is_test, "league"].to_numpy(), "correct": pred_te == y_te})
print("\naccuracy by league")
print(by_league.groupby("league")["correct"].agg(acc="mean", n="size").round(3).sort_values("acc", ascending=False))

conf = proba_te.max(1)
cal = pd.DataFrame({"bin": pd.cut(conf, [0, .4, .45, .5, .55, .6, .7, 1.0]), "conf": conf, "hit": pred_te == y_te})
print("\ncalibration of the predicted class (avg_conf should be close to hit_rate)")
print(cal.groupby("bin", observed=True).agg(n=("hit", "size"), avg_conf=("conf", "mean"), hit_rate=("hit", "mean")).round(3))

dcal = pd.DataFrame({"bin": pd.cut(proba_te[:, 1], [0, .2, .25, .3, .35, .4, 1.0]), "p": proba_te[:, 1], "draw": y_te == 1})
print("\ndraw probability calibration")
print(dcal.groupby("bin", observed=True).agg(n=("p", "size"), avg_p_draw=("p", "mean"), draw_rate=("draw", "mean")).round(3))

imp = pd.Series(model.get_booster().get_score(importance_type="gain")).sort_values(ascending=False)
print("\ntop 20 features by gain")
print(imp.head(20).round(1))


# ── Cell 9: Optional experiments (all commented out) ───────────────────────
# Each block is independent. Uncomment ONE, run it, compare its line against Cell 8.

# --- 9a. Random hyper-parameter search (~10 min on Colab CPU) ----------------
# Paste the printed BEST dict over the matching keys in PARAMS (Cell 7), then re-run Cells 7-8.
# from sklearn.model_selection import ParameterSampler
# space = dict(max_depth=[3, 4, 5, 6], learning_rate=[0.01, 0.02, 0.03, 0.05],
#              min_child_weight=[3, 5, 10, 20], subsample=[0.7, 0.8, 0.9],
#              colsample_bytree=[0.4, 0.6, 0.8], reg_lambda=[1, 2, 5], gamma=[0, 0.1, 0.3])
# best = (9e9, None)
# for p in ParameterSampler(space, n_iter=25, random_state=SEED):
#     m = xgb.XGBClassifier(**{**PARAMS, **p}).fit(X_tr, y_tr, sample_weight=w_tr,
#                                                  eval_set=[(X_va, y_va)], verbose=False)
#     print(round(m.best_score, 4), p)
#     if m.best_score < best[0]:
#         best = (m.best_score, p)
# print("BEST:", best)

# --- 9b. Isotonic calibration on the validation season -----------------------
# If it wins, set REFIT_ON_ALL = False in Cell 10 and uncomment the `final_model = cal_model` line there.
# from sklearn.calibration import CalibratedClassifierCV
# cal_model = CalibratedClassifierCV(model_ens, method="isotonic", cv="prefit").fit(X_va, y_va)
# evaluate("ensemble + isotonic", cal_model.predict_proba(X_te), y_te)

# --- 9c. LightGBM instead of XGBoost (pre-installed on Colab) ----------------
# If it wins, set REFIT_ON_ALL = False in Cell 10 and uncomment `final_model = lgb_model` there.
# import lightgbm as lgb
# lgb_model = lgb.LGBMClassifier(objective="multiclass", n_estimators=3000, learning_rate=0.02,
#                                num_leaves=15, min_child_samples=30, subsample=0.8, subsample_freq=1,
#                                colsample_bytree=0.6, reg_lambda=2.0, random_state=SEED, verbose=-1)
# lgb_model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)],
#               callbacks=[lgb.early_stopping(150), lgb.log_evaluation(250)])
# evaluate("lightgbm", lgb_model.predict_proba(X_te), y_te)

# --- 9d. Bookmaker odds as features --------------------------------------------
# Set USE_ODDS = True in Cell 2 and re-run Cells 6-8. /predict must then receive
# odds_home / odds_draw / odds_away for every fixture (web/lib/odds.ts); rows without
# odds are handled as missing values.

# --- 9e. Fewer seeds / single model --------------------------------------------
# Set N_SEEDS = 1 in Cell 2 for a faster loop while experimenting; go back to 5 to ship.

# Measured on the 2025/26 hold-out, 5-seed ensemble (accuracy / log-loss / draw picks -> correct):
#   no weights (default) ......... 0.511 / 0.994 /    2 -> 2
#   sqrt class balancing ......... 0.509 / 1.001 /  155 -> 45   (29% precision, base rate 26%)
#   full class balancing ......... 0.469 / 1.018 / 1065 -> 292
#   time decay ................... 0.514 / 0.994 /   11 -> 3    (within noise of the default)
#   USE_ODDS = True .............. 0.527 / 0.984 /    5 -> 1    (bookmakers themselves: 0.519 / 0.986)


# ── Cell 10: Final refit on ALL seasons + serving stores ───────────────────
REFIT_ON_ALL = True    # False = ship the Cell 7 ensemble as-is (required for 9b / 9c)

if REFIT_ON_ALL:
    final_model = fit_ensemble(X_all[is_model], y_all[is_model],
                               make_weights(y_all[is_model], df.loc[is_model, "date"]), int(BEST_ITER * 1.1))
else:
    final_model = model_ens
    # final_model = cal_model      # if 9b won
    # final_model = lgb_model      # if 9c won
print(type(final_model).__name__, "trained on", f"{int(is_model.sum()):,}", "matches")


def make_stores(S):
    """JSON-ready serving stores = the state after the last match seen."""
    elo = {t: round(r, 2) for t, r in S["elo"].items()}
    pi = {t: {"home": round(v[0], 4), "away": round(v[1], 4)} for t, v in S["pi"].items()}
    h2h = {f"{t1}|{t2}": {"t1_win_rate": round(st["w1"] / st["n"], 4), "t2_win_rate": round(st["w2"] / st["n"], 4),
                          "draw_rate": round(st["d"] / st["n"], 4), "matches": int(st["n"])}
           for (t1, t2), st in S["pairs"].items() if st["n"]}
    tables = {lg: {"season": s, "table": {t: {"pts": int(p), "gd": int(g), "played": int(n)} for t, (p, g, n) in tbl.items()}}
              for lg, (s, tbl) in S["tables"].items()}

    def r4(v):
        return None if pd.isna(v) else round(float(v), 4)

    form = {}
    for team, grp in S["long"].groupby("team"):
        e = {}
        for w in FORM_WINDOWS:
            tail = grp.tail(w)
            for src, name in FORM_STATS + STAT_STATS:
                e[f"form_{name}_{w}"] = r4(tail[src].mean())
        for is_home, label in ((1, "home"), (0, "away")):
            tail = grp[grp.is_home == is_home].tail(VENUE_WINDOW)
            for src, name in VENUE_STATS:
                e[f"venue_{label}_{name}"] = r4(tail[src].mean()) if len(tail) else None
        e["matches_played"] = int(len(grp))
        e["last_match_date"] = str(S["last_seen"][team].date())
        e["form_summary"] = ["W" if w else ("D" if d else "L") for w, d in zip(grp.tail(5).win, grp.tail(5).draw)]
        form[team] = e
    return {"elo": elo, "pi": pi, "h2h": h2h, "form": form, "tables": tables}


SERVING = make_stores(STORES)
print(f"stores: {len(SERVING['elo'])} teams, {len(SERVING['h2h'])} h2h pairs, {len(SERVING['tables'])} league tables")
print("\ntop 10 Elo")
print(pd.Series(SERVING["elo"]).sort_values(ascending=False).head(10))


# ── Cell 11: Serving-path check (this is what model/features.py must do) ───
def feature_row(home, away, league, kickoff, S, league_map, defaults, odds=None):
    """Rebuild one feature row from the JSON stores, exactly as features.py does."""
    kickoff = pd.Timestamp(kickoff)
    hf, af = S["form"].get(home, {}), S["form"].get(away, {})
    hp, ap = S["pi"].get(home, {"home": 0.0, "away": 0.0}), S["pi"].get(away, {"home": 0.0, "away": 0.0})

    def rest(f):
        return min((kickoff - pd.Timestamp(f["last_match_date"])).days, MAX_REST_DAYS) if f else MAX_REST_DAYS

    t1, t2 = sorted([home, away])
    pair = S["h2h"].get(f"{t1}|{t2}")
    entry = S["tables"].get(league)
    table = {}
    if entry and entry["season"] == season_of(kickoff):          # new season -> empty table
        table = {t: (v["pts"], v["gd"], v["played"]) for t, v in entry["table"].items()}

    row = {
        "home_elo": S["elo"].get(home, INITIAL_ELO), "away_elo": S["elo"].get(away, INITIAL_ELO),
        "home_pi_home": hp["home"], "home_pi_away": hp["away"], "away_pi_home": ap["home"], "away_pi_away": ap["away"],
        "pi_exp_gd": pi_expected_gd(hp["home"]) - pi_expected_gd(ap["away"]),
        "h2h_home_winrate": (pair["t1_win_rate"] if home == t1 else pair["t2_win_rate"]) if pair else 0.5,
        "h2h_draw_rate": pair["draw_rate"] if pair else 0.25,
        "h2h_matches_count": pair["matches"] if pair else 0,
        "home_days_rest": rest(hf), "away_days_rest": rest(af),
        "league_code": float(league_map.get(league, np.nan)),
    }
    for side, team in (("home", home), ("away", away)):
        pts, gd, played = table.get(team, (0, 0, 0))
        row[f"{side}_season_ppg"] = pts / played if played else np.nan
        row[f"{side}_season_played"] = played
        row[f"{side}_season_gd"] = gd
        row[f"{side}_league_pos"] = league_pos(table, team)
    row["elo_diff"] = row["home_elo"] - row["away_elo"]
    row["pi_diff"] = (hp["home"] + hp["away"]) / 2 - (ap["home"] + ap["away"]) / 2
    row["season_pos_diff"] = row["home_league_pos"] - row["away_league_pos"]
    row["season_ppg_diff"] = row["home_season_ppg"] - row["away_season_ppg"]
    for w in FORM_WINDOWS:
        for _, name in FORM_STATS + STAT_STATS:
            row[f"home_form_{name}_{w}"] = hf.get(f"form_{name}_{w}")
            row[f"away_form_{name}_{w}"] = af.get(f"form_{name}_{w}")
    for _, name in VENUE_STATS:
        row[f"home_venue_{name}"] = hf.get(f"venue_home_{name}")
        row[f"away_venue_{name}"] = af.get(f"venue_away_{name}")
    row["home_matches_played"] = hf.get("matches_played", 0)
    row["away_matches_played"] = af.get("matches_played", 0)
    if odds:                                                    # (home, draw, away) decimal odds, when USE_ODDS
        inv = np.array([1 / o for o in odds])
        row["odds_p_home"], row["odds_p_draw"], row["odds_p_away"] = inv / inv.sum()

    X = pd.DataFrame([row]).astype(float)
    for c in FEATURES:
        if c not in X.columns:
            X[c] = np.nan
    return add_diffs(X)[FEATURES].fillna(defaults)


# 1) Prove stores -> feature row == training features: rebuild stores without the last
#    N matches, then reconstruct those matches from the stores and compare.
N_CHECK = 60
head, tail = df.iloc[:-N_CHECK], df.iloc[-N_CHECK:]
_, S_head = build_features(head, LEAGUE_MAP)
S_check = make_stores(S_head)
seen = pd.concat([tail.home_team, tail.away_team]).value_counts()
single = tail[tail.home_team.map(seen).eq(1) & tail.away_team.map(seen).eq(1)]   # teams playing once in the tail
rows = pd.concat([feature_row(r.home_team, r.away_team, r.league, r.date, S_check, LEAGUE_MAP, FEATURE_DEFAULTS,
                              odds=(r.odds_home, r.odds_draw, r.odds_away) if USE_ODDS and pd.notna(r.odds_home) else None)
                  for r in single.itertuples()], ignore_index=True)
ref = X_all.loc[single.index, FEATURES].reset_index(drop=True)
diff = (rows - ref).abs().fillna(0)                            # NaN == NaN counts as equal
elo_cols = ["home_elo", "away_elo", "elo_diff"]
standing_cols = [c for c in FEATURES if "season_" in c or "league_pos" in c]
worst = diff.drop(columns=elo_cols + standing_cols).max()
# Standings depend on OTHER teams' results too, so they can only match exactly for the
# first fixture of each league inside the held-back tail.
first_in_league = (~tail.league.duplicated(keep="first")).loc[single.index].to_numpy()
worst_standing = diff.loc[first_in_league, standing_cols].max()
print(f"serving-path check on {len(single)} fixtures: max |diff| = {worst.max():.2e} in '{worst.idxmax()}' "
      f"(stores rounded to 4dp; Elo to 2dp -> max {diff[elo_cols].max().max():.3f}); "
      f"standings on {int(first_in_league.sum())} fixtures: max |diff| = {worst_standing.max():.2e}")
assert worst.max() < 1e-3 and worst_standing.max() < 1e-3, "train/serve skew - feature_row() does not match build_features()"


# 2) Demo predictions through the exact serving path.
def predict_match(home, away, league, kickoff, odds=None):
    X = feature_row(home, away, league, kickoff, SERVING, LEAGUE_MAP, FEATURE_DEFAULTS, odds)
    p = final_model.predict_proba(X)[0]
    return {"home": home, "away": away, "H": round(p[2], 3), "D": round(p[1], 3), "A": round(p[0], 3), "pick": CLASSES[p.argmax()]}

tomorrow = df.date.max() + pd.Timedelta(days=1)
print(pd.DataFrame([predict_match(r.home_team, r.away_team, r.league, tomorrow) for r in df.tail(6).itertuples()]))


# ── Cell 12: Export artifacts (unzip into model/artifacts/) ────────────────
os.makedirs(OUT_DIR, exist_ok=True)

def dump(name, obj):
    with open(os.path.join(OUT_DIR, name), "w") as f:
        json.dump(obj, f, indent=2)

joblib.dump(final_model, os.path.join(OUT_DIR, "xgboost_tuned.pkl"))
boosters = ([final_model] if isinstance(final_model, xgb.XGBClassifier)
            else getattr(final_model, "estimators_", []))
for i, m in enumerate(boosters):                               # what model/main.py actually loads
    if isinstance(m, xgb.XGBClassifier):
        m.get_booster().save_model(os.path.join(OUT_DIR, f"xgboost_seed{i}.ubj"))

dump("elo_ratings.json", SERVING["elo"])
dump("pi_ratings.json", SERVING["pi"])
dump("team_form.json", SERVING["form"])
dump("h2h_records.json", SERVING["h2h"])
dump("season_tables.json", SERVING["tables"])
dump("features.json", FEATURES)
dump("label_map.json", LABEL_MAP)
dump("league_map.json", LEAGUE_MAP)
dump("feature_defaults.json", FEATURE_DEFAULTS)
dump("model_metadata.json", {
    "model_version": "3.1-club",
    "trained_at": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    "data_range": f"{df.date.min().date()} -> {df.date.max().date()}",
    "n_matches": int(len(df)),
    "warmup_seasons": WARMUP_SEASONS,
    "test_seasons": TEST_SEASONS,
    "test_metrics": TEST_METRICS,
    "best_iteration": BEST_ITER,
    "n_seeds": N_SEEDS,
    "refit_on_all": REFIT_ON_ALL,
    "use_odds": USE_ODDS,
    "elo": {"initial": INITIAL_ELO, "k": ELO_K, "home_adv": ELO_HOME_ADV},
    "pi": {"lambda": PI_LAMBDA, "gamma": PI_GAMMA, "c": PI_C},
    "max_rest_days": MAX_REST_DAYS,
    "sparse_features": SPARSE_FEATURES,
    "classes": CLASSES,
    "features": FEATURES,
})

zip_path = "winscope_model_artifacts.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(os.listdir(OUT_DIR)):
        z.write(os.path.join(OUT_DIR, name), name)
print("wrote", zip_path, f"({os.path.getsize(zip_path) / 1e6:.1f} MB):", sorted(os.listdir(OUT_DIR)))

try:
    from google.colab import files
    files.download(zip_path)
except ImportError:
    pass
