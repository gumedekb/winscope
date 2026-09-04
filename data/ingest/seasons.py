"""Season derivation — from the Date column, never from the filename.

The `(1)…(8)` suffixes on the files in unsorted/ are browser download numbering.
Every league we track runs ~Aug -> May, so a match is in season Y/Y+1 when its
month is >= July, else in season Y-1/Y.
"""
import datetime as _dt
import pandas as pd

SEASON_CUTOVER_MONTH = 7   # July onwards = the new season


def season_start_year(d) -> int | None:
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return None
    if isinstance(d, str):
        d = pd.to_datetime(d, errors="coerce")
    if pd.isna(d):
        return None
    return d.year if d.month >= SEASON_CUTOVER_MONTH else d.year - 1


def season_label(d) -> str | None:
    """datetime -> '2021/22'"""
    y = season_start_year(d)
    return None if y is None else f"{y}/{(y + 1) % 100:02d}"


def season_series(dates: pd.Series) -> pd.Series:
    """Vectorised season_label over a datetime Series."""
    dates = pd.to_datetime(dates, errors="coerce")
    start = dates.dt.year.where(dates.dt.month >= SEASON_CUTOVER_MONTH, dates.dt.year - 1)
    return start.map(lambda y: None if pd.isna(y) else f"{int(y)}/{(int(y) + 1) % 100:02d}")


def label_to_start_year(label: str) -> int:
    """'2021/22' -> 2021"""
    return int(str(label).split("/")[0])


def current_season_start_year(today: _dt.date | None = None) -> int:
    from ingest.clock import utc_today
    today = today or utc_today()
    return today.year if today.month >= SEASON_CUTOVER_MONTH else today.year - 1


def resolve_file_season(dates: pd.Series) -> pd.Series:
    """Collapse one file's rows onto a single season label.

    A football-data.co.uk file holds exactly one season, so the modal row-wise
    label is the season for every row in it. This is what keeps the COVID-extended
    2019/20 season (played into July 2020) from splitting its tail into 2020/21.
    Falls back to the row-wise label if the file somehow spans no valid dates.
    """
    labels = season_series(dates)
    valid = labels.dropna()
    if valid.empty:
        return labels
    counts = valid.value_counts()
    top = counts.max()
    tied = sorted(counts[counts == top].index, key=label_to_start_year)
    return pd.Series([tied[0]] * len(labels), index=labels.index)


def label_to_fduk_code(label: str) -> str:
    """'2018/19' -> '1819' (football-data.co.uk's season path segment)."""
    y = label_to_start_year(label)
    return f"{y % 100:02d}{(y + 1) % 100:02d}"
