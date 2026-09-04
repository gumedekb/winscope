"""Merge new rows into the existing dataset and report what changed.

Spec step 9: compare against the existing model_data.csv and report rows ADDED,
rows UNCHANGED, and CONFLICTS (same fixture, different score/outcome — a
data-quality flag to inspect, written to output/conflicts.csv).
"""
from dataclasses import dataclass, field

import pandas as pd

from clean.normalize import match_key, normalize
from ingest import schema


@dataclass
class Diff:
    added: int = 0
    unchanged: int = 0
    updated: int = 0
    conflicts: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)


def load_existing(path: str) -> pd.DataFrame:
    """Read a previously written model_data.csv, or an empty canonical frame."""
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["date"])
    except (FileNotFoundError, OSError, pd.errors.EmptyDataError):
        return schema.empty()
    return schema.conform(df)


def diff(previous: pd.DataFrame, incoming: pd.DataFrame) -> Diff:
    """What `incoming` does to `previous`, keyed on (date, home_team, away_team)."""
    if incoming is None or incoming.empty:
        return Diff()
    incoming = incoming.copy()
    incoming["_k"] = match_key(incoming)
    incoming = incoming.drop_duplicates(subset="_k", keep="first")

    if previous is None or previous.empty:
        return Diff(added=len(incoming))

    previous = previous.copy()
    previous["_k"] = match_key(previous)
    previous = previous.drop_duplicates(subset="_k", keep="first")

    merged = incoming.merge(previous, on="_k", how="left", suffixes=("_new", "_old"))
    seen = merged["source_old"].notna()
    added = int((~seen).sum())

    def _num(series):
        return pd.to_numeric(series, errors="coerce")

    same_score = (
        (_num(merged["home_score_new"]) == _num(merged["home_score_old"])) &
        (_num(merged["away_score_new"]) == _num(merged["away_score_old"])) &
        (_num(merged["outcome_new"]) == _num(merged["outcome_old"]))
    )
    conflicting = seen & ~same_score
    unchanged = int((seen & same_score).sum())

    conflicts = merged.loc[conflicting, [
        "_k", "league_new", "season_new", "home_team_new", "away_team_new",
        "home_score_old", "away_score_old", "outcome_old", "source_old",
        "home_score_new", "away_score_new", "outcome_new", "source_new",
    ]].rename(columns=lambda c: c.replace("_new", "").replace("league", "league")
              if c.endswith("_new") else c)
    return Diff(added=added, unchanged=unchanged, updated=int(conflicting.sum()),
                conflicts=conflicts.reset_index(drop=True))


def merge(previous: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Union previous + incoming, then normalise (which dedupes by source trust)."""
    frames = [f for f in (previous, incoming) if f is not None and not f.empty]
    if not frames:
        return schema.empty()
    return normalize(pd.concat(frames, ignore_index=True))
