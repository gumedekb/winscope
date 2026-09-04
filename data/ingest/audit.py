"""Team-name audit — the tool for growing clean/team_aliases.json.

The APIs spell clubs differently from football-data.co.uk ("Dundee Utd" vs
"Dundee United", "ST Mirren" vs "St Mirren"). Every unfixed spelling splits one
club into two, which silently poisons the model's form features. This finds the
suspects by looking for near-identical names that appear *inside the same league*
under *different sources*, and prints them as ready-to-paste JSON.

    python pipeline.py --audit-teams
"""
import difflib
import re

import pandas as pd

from ingest.schema import SOURCE_PRIORITY

SIMILARITY = 0.82          # below this, two names are probably different clubs


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def suspects(df: pd.DataFrame, threshold: float = SIMILARITY) -> list[tuple]:
    """-> [(league, name_a, name_b, ratio, sources), ...] worth a human look."""
    if df is None or df.empty:
        return []
    long = pd.concat([
        df[["league", "home_team", "source"]].rename(columns={"home_team": "team"}),
        df[["league", "away_team", "source"]].rename(columns={"away_team": "team"}),
    ], ignore_index=True).dropna(subset=["league", "team"])

    # Two clubs that have played each other are definitively not the same club —
    # this is what stops "Sporting Clube de Braga" ~ "Sporting Clube de Portugal"
    # being reported as a duplicate.
    opponents = set()
    if {"home_team", "away_team"}.issubset(df.columns):
        opponents = {frozenset((h, a)) for h, a in
                     zip(df["home_team"], df["away_team"]) if pd.notna(h) and pd.notna(a)}

    found = []
    for league, grp in long.groupby("league"):
        sources = grp.groupby("team")["source"].agg(lambda s: sorted(set(s)))
        names = sorted(sources.index)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if frozenset((a, b)) in opponents:
                    continue
                if _squash(a) == _squash(b):
                    ratio = 1.0                     # same letters, different punctuation
                else:
                    ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
                    if ratio < threshold:
                        continue
                # Only interesting when the two spellings come from different
                # sources — within one source they are usually genuinely
                # different clubs (e.g. "Sheffield United" / "Sheffield Wednesday").
                pair = sorted(set(sources[a]) | set(sources[b]))
                if len(pair) < 2:
                    continue
                found.append((league, a, b, round(ratio, 3), pair))
    return sorted(found, key=lambda r: -r[3])


def _pick_canonical(df: pd.DataFrame, a: str, b: str) -> tuple[str, str]:
    """Suggest the spelling from the more trusted source as the canonical one."""
    def rank(name):
        used = df[(df.get("home_team") == name) | (df.get("away_team") == name)]
        if used.empty:
            return 99
        return min(SOURCE_PRIORITY.get(s, 9) for s in used["source"].dropna().unique())

    ra, rb = rank(a), rank(b)
    if ra != rb:
        return (a, b) if ra < rb else (b, a)
    return (a, b) if len(a) >= len(b) else (b, a)


def print_audit(df: pd.DataFrame, threshold: float = SIMILARITY) -> list:
    rows = suspects(df, threshold)
    if not rows:
        print("\n  ✓ no cross-source team-name mismatches found")
        return rows
    print(f"\n  {len(rows)} possible duplicate club(s) — same league, different "
          f"sources, similar names:")
    for league, a, b, ratio, sources in rows:
        print(f"      {league}: {a!r} ~ {b!r}  ({ratio:.0%}, {'+'.join(sources)})")
    print("\n  Paste the wrong spellings into clean/team_aliases.json, e.g.")
    for _league, a, b, _r, _s in rows[:6]:
        keep, drop = _pick_canonical(df, a, b)
        print(f'      "{drop}": "{keep}",')
    print("  then re-run:  python pipeline.py --full-rescan --with-api")
    return rows
