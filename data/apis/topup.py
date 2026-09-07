"""The API stage — orchestrates the four sources into two products.

Deliberately narrow, because the local football-data.co.uk dump already covers
the European history:
  1. Betway Premiership (South Africa) — the ONLY league we pull history for.
  2. Live + upcoming fixtures for every league we track.

Every source is optional and every failure is contained: a missing key, a blown
quota or a dead endpoint degrades that source to zero rows and the rest of the
run carries on.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from apis import api_football, football_data_org, openfootball, thesportsdb
from apis.ratelimit import QuotaStore
from clean.normalize import canon_team
from ingest.clock import utc_today
from ingest import schema
from leagues import SA_LEAGUE

# Which source wins when two of them report the same fixture.
FIXTURE_PRIORITY = {api_football.SOURCE: 0, football_data_org.SOURCE: 1,
                    thesportsdb.SOURCE: 2}


# Selectable providers, in the order run() works through them.
#
# Splitting these apart is what lets the cron run at two speeds. API-Football is
# the only one with a daily cap (100, and we stop at 90), so a frequent job can
# name just the uncapped providers and cost it nothing, while a slower job takes
# the full set. See .github/workflows/etl.yml.
PROVIDERS = ("api-football", "football-data", "thesportsdb", "openfootball")


def resolve_providers(selected=None) -> set:
    """None/empty -> everything. Unknown names are a hard error, not a silent
    no-op: a typo here would quietly stop collecting from a source."""
    if not selected:
        return set(PROVIDERS)
    if isinstance(selected, str):
        selected = [p.strip() for p in selected.split(",") if p.strip()]
    chosen = {p.strip().lower() for p in selected}
    if "all" in chosen:
        return set(PROVIDERS)
    unknown = chosen - set(PROVIDERS)
    if unknown:
        raise ValueError(f"unknown provider(s): {', '.join(sorted(unknown))}. "
                         f"Choose from: {', '.join(PROVIDERS)}")
    return chosen


@dataclass
class ApiResult:
    history: pd.DataFrame = field(default_factory=schema.empty)
    fixtures: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=schema.FIXTURE_COLUMNS))
    notes: list = field(default_factory=list)
    quota: list = field(default_factory=list)
    # Rows fetched purely to DIFF against what we already have; never merged.
    crosscheck: pd.DataFrame = field(default_factory=schema.empty)


def _log(notes, verbose, prefix, lines):
    for line in lines:
        notes.append(f"{prefix} {line}")
        if verbose:
            print(f"    · {line}")


def dedupe_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    """Canonicalise names, then keep one row per (kickoff day, home, away)."""
    if df is None or df.empty:
        return pd.DataFrame(columns=schema.FIXTURE_COLUMNS)
    out = df.copy()
    out["home_team"] = out["home_team"].map(canon_team)
    out["away_team"] = out["away_team"].map(canon_team)
    out["kickoff_utc"] = pd.to_datetime(out["kickoff_utc"], errors="coerce", utc=True)
    out = out.dropna(subset=["kickoff_utc", "home_team", "away_team"])
    out["_day"] = out["kickoff_utc"].dt.strftime("%Y-%m-%d")
    out["_p"] = out["source"].map(FIXTURE_PRIORITY).fillna(9)
    out = (out.sort_values(["_p", "kickoff_utc"])
              .drop_duplicates(subset=["_day", "home_team", "away_team"], keep="first")
              .drop(columns=["_p", "_day"])
              .sort_values("kickoff_utc")
              .reset_index(drop=True))
    for col in schema.FIXTURE_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[schema.FIXTURE_COLUMNS]


def run(sa_history: bool = True, fixtures: bool = True,
        openfootball_seasons: list | None = None, openfootball_mode: str = "gapfill",
        have: pd.DataFrame | None = None, all_leagues: bool = False,
        force: bool = False, verbose: bool = True,
        lookahead_days: int = 7, providers=None) -> ApiResult:
    res = ApiResult()
    hist_frames, fix_frames = [], []
    store = QuotaStore()
    use = resolve_providers(providers)
    if verbose and use != set(PROVIDERS):
        print(f"  providers: {', '.join(sorted(use))}")

    # -- API-Football: SA PSL history + the live feed -----------------------
    # Skipped outright when neither job was asked for (e.g. --cross-check), so a
    # run that has no use for it does not spend even the one status request.
    if "api-football" in use and api_football.available() and (sa_history or fixtures):
        if verbose:
            print("  API-Football (v3.football.api-sports.io)")
        try:
            sess = api_football.session(verbose=verbose)
            sess.store = store
            info = api_football.sync_quota(sess)
            _log(res.notes, verbose, "[api-football]",
                 [f"plan={info.get('plan')} usage={info.get('used')}/{info.get('limit')} "
                  f"(we stop at {sess.budget.day_cap})"])
            if sa_history:
                rows, notes = api_football.sa_history(sess, force=force)
                _log(res.notes, verbose, "[api-football]", notes)
                if not rows.empty:
                    hist_frames.append(rows)
            if fixtures:
                live, notes = api_football.live(sess, all_leagues=all_leagues, force=force)
                _log(res.notes, verbose, "[api-football]", notes)
                if not live.empty:
                    fix_frames.append(api_football.to_fixtures(live))
                soon, notes = api_football.upcoming_window(
                    sess, days=1, all_leagues=all_leagues, force=force)
                _log(res.notes, verbose, "[api-football]", notes)
                if not soon.empty:
                    fix_frames.append(api_football.to_fixtures(soon))
                    hist_frames.append(api_football.to_matches(soon))
            res.quota.append(sess.status_line())
        except (RuntimeError, ValueError) as exc:
            _log(res.notes, verbose, "[api-football]", [f"skipped: {exc}"])
    elif "api-football" not in use:
        _log(res.notes, verbose, "[api-football]", ["not selected — skipped"])
    elif sa_history or fixtures:
        _log(res.notes, verbose, "[api-football]", ["no API_SPORTS_KEY — skipped"])

    # -- football-data.org: the 7-day lookahead the other free plan refuses --
    if "football-data" in use and fixtures and football_data_org.available():
        if verbose:
            print("  football-data.org")
        try:
            sess = football_data_org.session(verbose=verbose)
            sess.store = store
            today = utc_today()
            rows, notes = football_data_org.matches_between(
                sess, today - timedelta(days=7), today + timedelta(days=lookahead_days),
                all_leagues=all_leagues, force=force)
            _log(res.notes, verbose, "[football-data.org]", notes)
            if not rows.empty:
                fix_frames.append(football_data_org.to_fixtures(rows))
                hist_frames.append(football_data_org.to_matches(rows))
            res.quota.append(sess.status_line())
        except (RuntimeError, ValueError) as exc:
            _log(res.notes, verbose, "[football-data.org]", [f"skipped: {exc}"])
    elif "football-data" not in use:
        _log(res.notes, verbose, "[football-data.org]", ["not selected — skipped"])
    elif fixtures:
        _log(res.notes, verbose, "[football-data.org]",
             ["no FOOTBALL_DATA_API_KEY — skipped"])

    # -- TheSportsDB: SA PSL current season + per-league lookahead ----------
    if "thesportsdb" in use and thesportsdb.available() and (sa_history or fixtures):
        if verbose:
            print("  TheSportsDB")
        try:
            sess = thesportsdb.session(verbose=verbose)
            sess.store = store
            if fixtures:
                # One request, global, uncapped — and the only free source here
                # that reports a match minute. Run it before the per-league
                # calls so a quota problem in those cannot cost us the clock.
                rows, notes = thesportsdb.livescore(
                    sess, all_leagues=all_leagues, force=force)
                _log(res.notes, verbose, "[thesportsdb]", notes)
                if not rows.empty:
                    fix_frames.append(thesportsdb.to_fixtures(rows))
                    hist_frames.append(thesportsdb.to_matches(rows))
                rows, notes = thesportsdb.next_events(sess, force=force)
                _log(res.notes, verbose, "[thesportsdb]", notes)
                if not rows.empty:
                    fix_frames.append(thesportsdb.to_fixtures(rows))
                recent, notes = thesportsdb.past_events(sess, force=force)
                _log(res.notes, verbose, "[thesportsdb]", notes)
                if not recent.empty:
                    hist_frames.append(thesportsdb.to_matches(recent))
            if sa_history:
                rows, notes = thesportsdb.sa_history(sess, force=force)
                _log(res.notes, verbose, "[thesportsdb]", notes)
                if not rows.empty:
                    hist_frames.append(rows)
            res.quota.append(sess.status_line())
        except (RuntimeError, ValueError) as exc:
            _log(res.notes, verbose, "[thesportsdb]", [f"skipped: {exc}"])

    # -- openfootball: gap-fill or cross-check, never a bulk merge ----------
    if openfootball_seasons and "openfootball" in use:
        if verbose:
            print(f"  openfootball ({openfootball_mode})")
        try:
            sess = openfootball.session(verbose=verbose)
            sess.store = store
            leagues = [lg for lg in openfootball.REGISTRY if lg.of_code]
            only = None
            if openfootball_mode == "gapfill":
                only = openfootball.missing_pairs(have, openfootball_seasons, leagues)
                _log(res.notes, verbose, "[openfootball]",
                     [f"{len(only)} missing league-season(s) to fill"
                      if only else "nothing missing — nothing to fetch"])
            rows, notes = openfootball.collect(sess, openfootball_seasons,
                                               force=force, only=only)
            _log(res.notes, verbose, "[openfootball]", notes)
            if not rows.empty:
                if openfootball_mode == "crosscheck":
                    res.crosscheck = rows
                else:
                    hist_frames.append(rows)
            res.quota.append(sess.status_line())
        except (RuntimeError, ValueError) as exc:
            _log(res.notes, verbose, "[openfootball]", [f"skipped: {exc}"])

    store.save()
    hist_frames = [f.dropna(axis=1, how="all") for f in hist_frames if not f.empty]
    fix_frames = [f for f in fix_frames if not f.empty]
    if hist_frames:
        res.history = schema.conform(pd.concat(hist_frames, ignore_index=True))
    res.fixtures = dedupe_fixtures(
        pd.concat(fix_frames, ignore_index=True) if fix_frames else None)
    return res


def summarise_fixtures(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "no live or upcoming fixtures collected"
    live = df[df["status"].isin(
        api_football.IN_PLAY | football_data_org.IN_PLAY | thesportsdb.IN_PLAY)]
    sa = df[df["league"] == SA_LEAGUE.name]
    return (f"{len(df)} fixtures ({len(live)} in play), "
            f"{df['league'].nunique()} leagues, {len(sa)} Betway Premiership")
