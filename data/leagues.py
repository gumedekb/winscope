"""Canonical league registry for the ETL — maps our leagues to every source's codes.

Mirrors web/lib/config.ts. See Obsidian: 03 - Data Sources / 04 - Data Collection Strategy.

One row per league we care about. `None` means "this source does not carry it".
Source id columns:
  fduk_div    football-data.co.uk division code (bulk CSV history, the main source)
  af_id       API-Football v3 league id            (v3.football.api-sports.io)
  fdo_code    football-data.org v4 competition code (free tier = 13 competitions)
  tsdb_id     TheSportsDB league id                 (free key, heavily result-capped)
  of_code     openfootball football.json league code (free, no key, no limit)
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class League:
    name: str
    country: str
    fduk_div: Optional[str]
    af_id: Optional[int]
    fdo_code: Optional[str]
    tsdb_id: Optional[str]
    of_code: Optional[str]


REGISTRY = [
    #       name                    country          fduk   af    fdo     tsdb    openfootball
    League("Premier League",        "England",       "E0",   39, "PL",  "4328", "en.1"),
    League("Championship",          "England",       "E1",   40, "ELC", "4329", "en.2"),
    League("League One",            "England",       "E2",   41, None,  "4396", "en.3"),
    League("Bundesliga",            "Germany",       "D1",   78, "BL1", "4331", "de.1"),
    League("Serie A",               "Italy",         "I1",  135, "SA",  "4332", "it.1"),
    League("La Liga",               "Spain",         "SP1", 140, "PD",  "4335", "es.1"),
    League("Ligue 1",               "France",        "F1",   61, "FL1", "4334", "fr.1"),
    League("Eredivisie",            "Netherlands",   "N1",   88, "DED", "4337", "nl.1"),
    League("Liga Portugal",         "Portugal",      "P1",   94, "PPL", "4344", "pt.1"),
    League("Scottish Premiership",  "Scotland",      "SC0", 179, None,  "4330", None),
    # Betway Premiership = the sponsored name of the South African Premier Soccer League.
    # Absent from football-data.co.uk -> API-only. This is the gap the API stage fills.
    League("Betway Premiership",    "South Africa",  None,  288, None,  "4802", None),
]

# ---- lookups -------------------------------------------------------------
BY_DIV = {lg.fduk_div: lg for lg in REGISTRY if lg.fduk_div}
BY_NAME = {lg.name: lg for lg in REGISTRY}
BY_AF_ID = {lg.af_id: lg for lg in REGISTRY if lg.af_id}
BY_FDO_CODE = {lg.fdo_code: lg for lg in REGISTRY if lg.fdo_code}
BY_TSDB_ID = {lg.tsdb_id: lg for lg in REGISTRY if lg.tsdb_id}

# The one league we actively pull *history* for from the APIs (everything else is
# already covered by the local football-data.co.uk dump).
SA_LEAGUE = BY_NAME["Betway Premiership"]

# Back-compat: collectors/football_data_uk.py + run.py (Mode A) unpack 4-tuples.
LEAGUES = [(lg.name, lg.country, lg.fduk_div, lg.af_id) for lg in REGISTRY]

# Seasons to pull from football-data.co.uk (codes like "2425" = 2024/25).
SEASON_CODES = ["1617", "1718", "1819", "1920", "2021", "2122", "2223",
                "2324", "2425", "2526", "2627"]

# The coverage window the report asserts against. Extended back to 2016/17 when
# those files were added — otherwise the report silently stops checking the
# oldest seasons it actually holds, and a hole there would never be flagged.
TARGET_SEASONS = ["2016/17", "2017/18", "2018/19", "2019/20", "2020/21", "2021/22",
                  "2022/23", "2023/24", "2024/25", "2025/26", "2026/27"]
