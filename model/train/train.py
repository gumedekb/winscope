"""Headless runner for colab_train.py — what .github/workflows/retrain.yml calls.

    python model/train/train.py --mode full      # monthly: retrain the trees, rebuild every store
    python model/train/train.py --mode stores    # weekly:  rebuild only the JSON stores

Why exec the notebook instead of importing it: colab_train.py is written as
Colab cells and has to stay that way (it is the reference model/features.py is
checked against, and the place experiments happen). Importing it would run
every cell; picking cells apart by hand would fork it. So this splits the file
on its `# ── Cell N` markers and runs the ones each mode needs, in one
namespace, exactly as Colab would — the notebook stays the single source of
truth and the workflow gets a plain script.

What the two modes touch in model/artifacts/:

    stores   elo_ratings, pi_ratings, team_form, h2h_records, season_tables
             — the state after the last match in model_data.csv. These are
             what goes stale between retrains (a club's form is a week old
             after a week); the trees do not. features / league_map /
             feature_defaults / label_map are NOT rewritten, because the
             trees on disk were fitted against those and must keep them.
    full     everything, via Cell 12 — but into a scratch directory first.
             The new ensemble is accepted only if its held-out accuracy is
             within MIN_ACCURACY_DELTA of the one currently deployed;
             otherwise nothing is copied and the run reports it. The held-out
             seasons roll forward over time, so this is a sanity gate against
             a broken dataset, not a precise A/B.

Exit codes: 0 = done (a rejected full retrain also exits 0, with accepted=false
in $GITHUB_OUTPUT, so the workflow can skip the commit without failing the
job); anything else = a real error.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
NOTEBOOK = os.path.join(HERE, "colab_train.py")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ARTIFACTS = os.path.join(REPO, "model", "artifacts")
DEFAULT_CSV = os.path.join(REPO, "data", "output", "model_data.csv")

STORE_FILES = {
    "elo": "elo_ratings.json",
    "pi": "pi_ratings.json",
    "form": "team_form.json",
    "h2h": "h2h_records.json",
    "tables": "season_tables.json",
}

# A full retrain replaces the deployed model only if it is not worse than this
# on held-out accuracy. 1pp is about one standard error on a ~4k-match test set.
MIN_ACCURACY_DELTA = float(os.environ.get("WINSCOPE_MIN_ACCURACY_DELTA", "-0.01"))

CELL_RE = re.compile(r"^# ── Cell (\d+):.*$", re.M)


def split_cells(path: str) -> dict[int, str]:
    src = open(path, encoding="utf-8").read()
    marks = list(CELL_RE.finditer(src))
    if not marks:
        raise RuntimeError(f"no '# ── Cell N' markers in {path}")
    cells = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(src)
        cells[int(m.group(1))] = src[m.start():end]
    return cells


def run_cells(cells: dict[int, str], numbers: list[int], ns: dict) -> None:
    for n in numbers:
        print(f"\n{'=' * 78}\n>> Cell {n}\n{'=' * 78}", flush=True)
        code = compile(cells[n], f"{NOTEBOOK}#cell{n}", "exec")
        exec(code, ns)  # noqa: S102 — this IS the notebook, run the way Colab runs it


def dump(directory: str, name: str, obj) -> None:
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def github_output(**kv) -> None:
    """Hand key=value pairs to the workflow step, if we are in one."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def run_stores(cells: dict[int, str], ns: dict) -> None:
    """Cells 1-6 build the sequential state; make_stores turns it into JSON."""
    run_cells(cells, [1, 2, 3, 4, 5, 6], ns)
    serving = ns["make_stores"](ns["STORES"])
    for key, name in STORE_FILES.items():
        dump(ARTIFACTS, name, serving[key])

    df = ns["df"]
    meta_path = os.path.join(ARTIFACTS, "model_metadata.json")
    meta = load_json(meta_path)
    meta["stores_refreshed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    meta["stores_data_range"] = f"{df.date.min().date()} -> {df.date.max().date()}"
    meta["stores_n_matches"] = int(len(df))
    dump(ARTIFACTS, "model_metadata.json", meta)

    summary = (f"stores rebuilt from {len(df):,} matches to {df.date.max().date()}: "
               f"{len(serving['elo'])} clubs, {len(serving['h2h'])} h2h pairs, "
               f"{len(serving['tables'])} league tables")
    print(f"\n{summary}")
    github_output(accepted="true", summary=summary)


def run_full(cells: dict[int, str], ns: dict, out_dir: str) -> bool:
    """Every cell, into out_dir; copy into artifacts/ only if the gate passes."""
    run_cells(cells, sorted(cells), ns)

    new_meta = load_json(os.path.join(out_dir, "model_metadata.json"))
    new_acc = float(new_meta["test_metrics"]["accuracy"])
    old_path = os.path.join(ARTIFACTS, "model_metadata.json")
    old_acc = float(load_json(old_path)["test_metrics"]["accuracy"]) if os.path.exists(old_path) else None

    line = (f"held-out accuracy {new_acc:.4f} on {new_meta['test_seasons']} "
            f"(log-loss {new_meta['test_metrics']['log_loss']:.4f}, best iteration {new_meta['best_iteration']})")
    if old_acc is not None and new_acc < old_acc + MIN_ACCURACY_DELTA:
        summary = f"REJECTED — {line} vs deployed {old_acc:.4f}; kept the deployed model"
        print(f"\n{summary}")
        github_output(accepted="false", summary=summary)
        return False

    # xgboost_tuned.pkl and the .zip are Colab conveniences; the server reads
    # the .ubj boosters, and the pickle would add ~2 MB a month to the repo
    # for nothing. features.json etc. all come from this same run.
    for name in sorted(os.listdir(out_dir)):
        if name.endswith((".pkl", ".zip")):
            continue
        shutil.copy2(os.path.join(out_dir, name), os.path.join(ARTIFACTS, name))

    prev = f" (deployed was {old_acc:.4f})" if old_acc is not None else ""
    summary = f"retrained on {new_meta['n_matches']:,} matches: {line}{prev}"
    print(f"\n{summary}")
    github_output(accepted="true", summary=summary)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=["full", "stores"], required=True)
    parser.add_argument("--csv", default=os.environ.get("WINSCOPE_CSV", DEFAULT_CSV))
    parser.add_argument("--version", default=None,
                        help="model_version to stamp on a full retrain "
                             "(default: the notebook's, which follows USE_ODDS)")
    args = parser.parse_args(argv)

    if not os.path.exists(args.csv):
        print(f"no training set at {args.csv}", file=sys.stderr)
        return 2
    os.makedirs(ARTIFACTS, exist_ok=True)

    # The notebook reads its configuration from these; see colab_train Cell 2.
    os.environ["WINSCOPE_CSV"] = os.path.abspath(args.csv)
    os.environ["WINSCOPE_AUTO_SEASONS"] = "1"
    os.environ.setdefault("WINSCOPE_SKIP_PIP", "1")
    if args.version:
        os.environ["WINSCOPE_MODEL_VERSION"] = args.version

    cells = split_cells(NOTEBOOK)
    ns = {"__name__": "__winscope_train__", "__file__": NOTEBOOK}

    if args.mode == "stores":
        os.environ["WINSCOPE_OUT"] = ARTIFACTS       # unused by Cells 1-6, set for clarity
        run_stores(cells, ns)
        return 0

    with tempfile.TemporaryDirectory(prefix="winscope-train-") as tmp:
        os.environ["WINSCOPE_OUT"] = tmp
        cwd = os.getcwd()
        os.chdir(tmp)                                 # Cell 12 writes its .zip to cwd
        try:
            run_full(cells, ns, tmp)
        finally:
            os.chdir(cwd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
