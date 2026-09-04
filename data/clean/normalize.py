"""Transform step: raw rows from ANY source -> ONE canonical, deduped dataset.

Canonical schema lives in ingest/schema.py. The team-alias map is the #1 defence
against the same club appearing under different names across sources — e.g.
football-data.co.uk "Man United" vs FootyStats "Manchester United" vs
API-Football "Manchester United". Grow clean/team_aliases.json as mismatches appear.
See Obsidian: 04 - Data Collection Strategy.
"""
import difflib
import json
import os
import re
import unicodedata

import pandas as pd

from ingest import schema

_ALIASES = None
_ALIAS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team_aliases.json")

# Club-form tokens that carry no identity — stripped before the alias lookup so
# "Ajax", "AFC Ajax" and "Ajax FC" all collapse to the same key.
_NOISE = re.compile(
    r"^(?:AFC|FC|CF|SC|AC|SS|SV|VfL|VfB|TSG|RC|CD|UD|SD|RCD)\s+|"
    r"\s+(?:AFC|FC|CF|SC|AC|SK|SV|BV|BC)$",
    flags=re.IGNORECASE,
)

# Common club-word abbreviations, expanded in the lookup key only. Sources mix
# these freely ("Chippa Utd." vs "Chippa United"), and each unfixed pair splits a
# club in two.
_ABBREVIATIONS = {
    "utd": "united",
    "cty": "city",
    "twn": "town",
    "ath": "athletic",
    "acad": "academy",
    "wdrs": "wanderers",
    "rvrs": "rovers",
    "st": "saint",
    "utd.": "united",
}

# Extra tokens only removed when building the *lookup key*, never from the output
# name: club-form initials, founding-year numbers ("Bayer 04 Leverkusen",
# "Paderborn 07") and connector words ("Celta de Vigo").
_KEY_TOKENS = re.compile(
    r"^(?:1\.|2\.)$|"
    r"^(?:AFC|FC|CF|SC|AC|ACF|AS|SS|SSC|SV|SBV|SK|BV|BC|BSC|TSV|FSV|VfL|VfB|TSG|"
    r"RC|RCD|CD|UD|SD|CA|AJ|UC|US|OGC|RB|SL|CS|ES|CFC|OSC|SCO|PEC|SpVgg)$|"
    r"^\d{2,4}$|"
    r"^(?:de|do|da|di|del|of|the|calcio|club|clube|futbol|fussball)$",
    flags=re.IGNORECASE,
)


def _load_aliases() -> dict:
    """Build the lookup: casefolded+stripped spelling -> canonical name.

    Two rules beyond the literal file contents:
      * lookup is case-insensitive, so API-Football's "ST Mirren" and
        football-data.co.uk's "St Mirren" land on one club;
      * every canonical *value* is also registered as a key for itself, so a
        source that already uses the canonical spelling in different casing
        ("Supersport United") is pulled onto it without a second file entry.
    Explicit keys always win over the auto-registered values.
    """
    global _ALIASES
    if _ALIASES is None:
        with open(_ALIAS_PATH, encoding="utf-8") as fh:
            raw = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
        table = {_key(v): v for v in raw.values()}       # canonical names, self-mapped
        table.update({_key(k): v for k, v in raw.items()})   # explicit keys win
        _ALIASES = table
    return _ALIASES


def _key(name: str) -> str:
    """The identity key two spellings of one club must share.

    Accents are folded ("Köln" == "Koln"), club-form initials and founding-year
    numbers are dropped ("Bayer 04 Leverkusen" == "Bayer Leverkusen"), and case
    is ignored ("ST Mirren" == "St Mirren"). Only ever used for MATCHING — the
    name that gets written out keeps its real spelling.
    """
    folded = unicodedata.normalize("NFKD", _strip(name))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    tokens = []
    for token in folded.split():
        token = token.strip(".")                     # "Utd." -> "Utd"
        if not token or _KEY_TOKENS.match(token):
            continue
        tokens.append(_ABBREVIATIONS.get(token.casefold(), token))
    return " ".join(tokens).casefold() or folded.casefold()


def reload_aliases() -> None:
    """Drop the cache — used by the tests and after editing team_aliases.json."""
    global _ALIASES
    _ALIASES = None


def _strip(name: str) -> str:
    n = unicodedata.normalize("NFC", str(name))
    n = re.sub(r"\s+", " ", n).strip()
    prev = None
    while prev != n:                      # "AFC Bournemouth FC" -> "Bournemouth"
        prev = n
        n = _NOISE.sub(" ", n).strip()
    return n


def canon_team(name):
    """Source-specific team name -> ONE canonical name."""
    if not isinstance(name, str) or not name.strip():
        return name
    stripped = _strip(name)
    return _load_aliases().get(_key(stripped), stripped)


# Tokens that identify nothing on their own — a name made only of these must
# never be merged into a longer one ("Real" must not swallow "Real Madrid").
GENERIC_TOKENS = {
    "united", "city", "town", "real", "club", "sporting", "sport", "athletic",
    "atletico", "racing", "stade", "olympique", "deportivo", "football", "fussball",
    "association", "sociedad", "union", "rovers", "wanderers", "albion", "county",
    "borussia", "bayer", "eintracht", "werder", "hertha", "fortuna", "dynamo",
    "sparta", "slavia", "national", "academy", "praia", "lisboa",
}

# A reserve/B side is a DIFFERENT team from its first team, and its name is a
# superset of it, so the subset rule would happily merge them. Block that.
RESERVE_MARKERS = {"ii", "iii", "iv", "b", "2", "3", "reserves", "u19", "u21", "u23",
                   "castilla", "atletic"}


def _tokens(name: str) -> set:
    return set(_key(name).split())


def _stem_match(a: str, b: str) -> bool:
    """Same word, different language/suffix: brest/brestois, milan/milano."""
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4:
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    common = os.path.commonprefix([a, b])
    return (len(common) >= 4
            and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75)


def _same_club(a: str, b: str) -> bool:
    """Is `a` the short form, or a spelling variant, of `b`?

    Two rules, both deliberately conservative and both additionally gated by the
    "have they played each other" check in the caller:

      subset — every token of the shorter name appears in the longer one
               ("Benfica" inside "Sport Lisboa e Benfica", "AZ" inside
               "AZ Alkmaar", "Lens" inside "Racing Club de Lens");
      stem   — same number of tokens, each pairing off by prefix or near-match
               ("Inter Milan" / "Internazionale Milano",
                "Stade Brestois 29" / "Brest" once club tokens are stripped).
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or ta == tb:
        return False
    # A reserve marker only disqualifies when it is the DIFFERENCE between the
    # two names ("Sparta Praha" vs "Sparta Praha II"). A club legitimately called
    # "Willem II" keeps its numeral in both spellings and is fine.
    if (ta ^ tb) & RESERVE_MARKERS:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) < len(long_):
        # Subset, allowing stemmed tokens: "Brest" inside "Stade Brestois 29",
        # "Rennes" inside "Stade Rennais FC 1901".
        if short <= GENERIC_TOKENS:                    # "Real" must not swallow
            return False
        return all(any(_stem_match(t, o) for o in long_) for t in short)
    if len(ta) != len(tb):
        return False
    # Stem rule: pair every token off, each used once.
    pool = list(tb)
    for token in ta:
        hit = next((x for x in pool if _stem_match(token, x)), None)
        if hit is None:
            return False
        pool.remove(hit)
    return not (ta <= GENERIC_TOKENS)


def merge_split_clubs(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Reunite one club that two sources spell differently enough to survive
    `collapse_variants`.

    This is the difference between the model knowing Benfica has 275 matches of
    history and treating it as a debutant on default ELO, so it runs on every
    build rather than waiting for someone to notice and edit the alias file.
    Two safety rules, because a WRONG merge is worse than leaving a club split —
    it fuses two clubs' histories and corrupts both:
      * two clubs that have ever played each other are never merged;
      * a merge only happens when it is UNAMBIGUOUS. "Espanyol de Barcelona"
        matches both "Espanyol" and "Barcelona", and "Internazionale Milano"
        matches both "Inter Milan" and "AC Milan" — neither is guessed at. They
        are reported instead, for a one-line entry in team_aliases.json, where an
        explicit alias always wins.
    """
    if df.empty:
        return df
    opponents = {frozenset((h, a)) for h, a in zip(df["home_team"], df["away_team"])
                 if isinstance(h, str) and isinstance(a, str)}
    canonical_values = set(_load_aliases().values())
    remap = {}

    ambiguous = []
    for league, grp in df.groupby("league"):
        stacked = pd.concat([
            grp[["home_team", "source"]].rename(columns={"home_team": "team"}),
            grp[["away_team", "source"]].rename(columns={"away_team": "team"}),
        ], ignore_index=True).dropna(subset=["team"])
        counts = stacked["team"].value_counts()
        trust = (stacked.assign(p=stacked["source"].map(schema.SOURCE_PRIORITY).fillna(9))
                        .groupby("team")["p"].min())
        names = list(counts.index)

        # Build the candidate graph first, then keep only pairs where BOTH sides
        # have exactly one candidate. Anything else is ambiguous and left alone.
        pairs, degree = [], {}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if frozenset((a, b)) in opponents or not _same_club(a, b):
                    continue
                pairs.append((a, b))
                degree[a] = degree.get(a, 0) + 1
                degree[b] = degree.get(b, 0) + 1

        for a, b in pairs:
            if degree[a] > 1 or degree[b] > 1:
                ambiguous.append((league, a, b))
                continue
            ranked = sorted(
                (a, b),
                key=lambda n: (n not in canonical_values, -counts[n], trust[n], len(n)))
            keep, drop = ranked[0], ranked[1]
            remap[drop] = keep
            if verbose:
                print(f"    · {league}: '{drop}' ({counts[drop]}) -> "
                      f"'{keep}' ({counts[keep]})")

    if ambiguous and verbose:
        print("\n    ! ambiguous, NOT merged — add an explicit alias to "
              "clean/team_aliases.json:")
        for league, a, b in ambiguous:
            print(f"        {league}: '{a}' ~ '{b}'")

    if not remap:
        return df
    # Follow chains (a -> b -> c) to their end.
    for _ in range(3):
        remap = {k: remap.get(v, v) for k, v in remap.items()}
    df = df.copy()
    df["home_team"] = df["home_team"].replace(remap)
    df["away_team"] = df["away_team"].replace(remap)
    return df


def collapse_variants(df: pd.DataFrame) -> pd.DataFrame:
    """Force every spelling of one club onto a single name, data-driven.

    The alias file cannot enumerate every spelling every source will ever invent
    ("Málaga" / "Malaga", "CA Osasuna" / "Osasuna", "AJ Auxerre" / "Auxerre").
    So after the alias pass, names that share an identity key are collapsed onto
    ONE representative, chosen in this order:
        1. a spelling the alias file already declares canonical,
        2. the spelling that appears most often in the data,
        3. the spelling from the most trusted source,
        4. the shortest.
    Without this, one club silently becomes two and its form features are wrong.
    """
    if df.empty:
        return df
    stacked = pd.concat([
        df[["home_team", "source"]].rename(columns={"home_team": "team"}),
        df[["away_team", "source"]].rename(columns={"away_team": "team"}),
    ], ignore_index=True).dropna(subset=["team"])
    if stacked.empty:
        return df

    canonical_values = set(_load_aliases().values())
    stacked["ident"] = stacked["team"].map(_key)
    stacked["prio"] = stacked["source"].map(schema.SOURCE_PRIORITY).fillna(9)

    counts = stacked.groupby(["ident", "team"]).agg(n=("team", "size"),
                                                    p=("prio", "min")).reset_index()
    counts["not_canon"] = ~counts["team"].isin(canonical_values)     # False sorts first
    counts["length"] = counts["team"].str.len()
    counts = counts.sort_values(["ident", "not_canon", "n", "p", "length"],
                                ascending=[True, True, False, True, True])
    winner = counts.drop_duplicates("ident", keep="first").set_index("ident")["team"]

    remap = {team: winner[ident] for ident, team in
             zip(counts["ident"], counts["team"]) if winner[ident] != team}
    if not remap:
        return df
    df = df.copy()
    df["home_team"] = df["home_team"].replace(remap)
    df["away_team"] = df["away_team"].replace(remap)
    return df


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Canonicalise names, drop unusable rows, dedupe by source trust, sort by date."""
    if df is None or df.empty:
        return schema.empty()
    df = schema.conform(df)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["home_team"] = df["home_team"].map(canon_team)
    df["away_team"] = df["away_team"].map(canon_team)
    df = collapse_variants(df)
    df = merge_split_clubs(df, verbose=False)
    df = collapse_variants(df)      # re-run: merges can expose new exact variants
    df = df.dropna(subset=["date", "home_team", "away_team", "outcome"])
    df = df[df["home_team"] != df["away_team"]]

    # Highest-trust source wins on a duplicate fixture; on a tie (same fixture
    # re-parsed from the same source) the row carrying more odds/stats wins, so a
    # rescan can only ever add information, never blank it out.
    extra = ["odds_home", "odds_draw", "odds_away", *schema.STAT_COLUMNS]
    df = df.assign(_p=df["source"].map(schema.SOURCE_PRIORITY).fillna(9),
                   _full=df[extra].notna().sum(axis=1))
    df = (df.sort_values(["_p", "_full"], ascending=[True, False], kind="stable")
            .drop_duplicates(subset=["date", "home_team", "away_team"], keep="first")
            .drop(columns=["_p", "_full"])
            .sort_values(["date", "league", "home_team"])
            .reset_index(drop=True))

    df["outcome"] = df["outcome"].astype(int)
    for col in ("home_score", "away_score"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ("odds_home", "odds_draw", "odds_away"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in schema.STAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return schema.conform(df)


def align_to_vocabulary(df: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Pull `df`'s team names onto the spellings already used in `reference`.

    fixtures.csv and model_data.csv must agree on club names or the web app
    cannot join tonight's fixture to that club's history. The training set is
    the authority, so fixtures are aligned to it rather than the other way round.
    """
    if df is None or df.empty or reference is None or reference.empty:
        return df
    df = df.copy()
    for col in ("home_team", "away_team"):
        df[col] = df[col].map(lambda n: canon_team(n) if isinstance(n, str) else n)

    # Pass 1: exact identity key.
    vocab = {}
    for col in ("home_team", "away_team"):
        for name in reference[col].dropna().unique():
            vocab.setdefault(_key(name), name)
    for col in ("home_team", "away_team"):
        df[col] = df[col].map(
            lambda n: vocab.get(_key(n), n) if isinstance(n, str) else n)

    # Pass 2: the long official names ("Sport Lisboa e Benfica") that pass 1
    # cannot see, resolved against the same league's known clubs — and only when
    # exactly one club matches, never on a guess.
    known = {}
    for league, grp in reference.groupby("league"):
        known[league] = set(grp["home_team"].dropna()) | set(grp["away_team"].dropna())
    resolved = {}
    for league, grp in df.groupby("league"):
        pool = known.get(league)
        if not pool:
            continue
        unknown = ((set(grp["home_team"].dropna()) | set(grp["away_team"].dropna()))
                   - pool)
        for name in unknown:
            hits = [k for k in pool if _same_club(name, k)]
            if len(hits) == 1:
                resolved[name] = hits[0]
    if resolved:
        df["home_team"] = df["home_team"].replace(resolved)
        df["away_team"] = df["away_team"].replace(resolved)
    return df


def match_key(df: pd.DataFrame) -> pd.Series:
    """The identity of a fixture: date + both canonical team names."""
    return (pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d") + "|" +
            df["home_team"].astype(str) + "|" + df["away_team"].astype(str))
