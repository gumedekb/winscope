"""Parser: CSVs already in the canonical WinScope schema.

Anything that writes `date, season, league, country, home_team, away_team,
home_score, away_score, outcome, odds_*, source` can be dropped straight into
unsorted/ and the pipeline will pick it up — no new parser per source. That is
how the scrapers hand their output over (see scrapers/, scrape.py).

Detection is by column shape rather than filename, so the file can be called
whatever is most readable to a human.
"""
import pandas as pd

from ingest import schema

REQUIRED = {"date", "league", "home_team", "away_team", "home_score", "away_score"}


def looks_canonical(path: str) -> bool:
    try:
        head = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
    except Exception:
        return False
    return REQUIRED.issubset({str(c).strip() for c in head.columns})


def parse(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    raw.columns = [str(c).strip() for c in raw.columns]
    if not REQUIRED.issubset(set(raw.columns)):
        return schema.empty()

    out = schema.conform(raw)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("home_score", "away_score"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ("odds_home", "odds_draw", "odds_away"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    # Re-derive the outcome rather than trusting the file's own column.
    out["outcome"] = [schema.result_to_outcome(h, a)
                      for h, a in zip(out["home_score"], out["away_score"])]
    if out["season"].isna().any():
        from ingest.seasons import season_series
        out.loc[out["season"].isna(), "season"] = season_series(
            out.loc[out["season"].isna(), "date"])
    out["source"] = out["source"].fillna("canonical-csv")
    return schema.conform(out.dropna(subset=["date", "home_team", "away_team", "outcome"]))
