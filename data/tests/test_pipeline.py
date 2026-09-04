"""Offline test suite.

TWO HARD RULES, enforced in setUpModule below, not just by convention:
  1. WINSCOPE_OFFLINE=1 — every BudgetedSession refuses to issue a request.
  2. socket.socket is replaced with a raising stub — so even code that somehow
     bypassed rule 1 cannot open a connection.

That means running the tests can never spend a single unit of your API quota,
which matters when the API-Football free tier is 100 requests for the whole day.
The tests below therefore verify the *rate-limiting maths and behaviour* against
fakes rather than the live services.

Run:  python -m unittest discover -s tests -v        (no extra dependencies)
      python -m pytest tests -q                      (if you prefer pytest)
"""
import json
import os
import socket
import sys
import tempfile
import unittest
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REAL_SOCKET = socket.socket


def setUpModule():
    os.environ["WINSCOPE_OFFLINE"] = "1"

    class _Blocked(socket.socket):
        def __init__(self, *a, **k):
            raise AssertionError(
                "the test suite tried to open a network connection — tests must "
                "never touch a live API or spend quota")

    socket.socket = _Blocked


def tearDownModule():
    socket.socket = _REAL_SOCKET
    os.environ.pop("WINSCOPE_OFFLINE", None)


DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNSORTED = os.path.join(DATA_DIR, "unsorted")


# ---------------------------------------------------------------------------
class TestSeasons(unittest.TestCase):
    def test_label_uses_august_to_may_convention(self):
        from ingest.seasons import season_label
        self.assertEqual(season_label(pd.Timestamp("2021-08-14")), "2021/22")
        self.assertEqual(season_label(pd.Timestamp("2022-05-03")), "2021/22")
        self.assertEqual(season_label(pd.Timestamp("2026-09-02")), "2026/27")

    def test_file_season_survives_the_covid_extended_2019_20(self):
        """2019/20 ran into July 2020; the tail must not become 2020/21."""
        from ingest.seasons import resolve_file_season
        dates = pd.Series(pd.to_datetime(
            ["2019-08-09"] * 300 + ["2020-06-20"] * 60 + ["2020-07-26"] * 20))
        self.assertEqual(set(resolve_file_season(dates)), {"2019/20"})

    def test_fduk_season_code_roundtrip(self):
        from ingest.seasons import label_to_fduk_code
        self.assertEqual(label_to_fduk_code("2018/19"), "1819")
        self.assertEqual(label_to_fduk_code("2026/27"), "2627")


class TestFdukParser(unittest.TestCase):
    def test_bom_file_is_not_skipped(self):
        """SC0.csv carries a UTF-8 BOM: the first header reads 'ï»¿Div'."""
        from ingest.fduk import parse
        path = os.path.join(UNSORTED, "SC0.csv")
        with open(path, "rb") as fh:
            self.assertTrue(fh.read(3) == b"\xef\xbb\xbf", "expected a BOM in SC0.csv")
        df = parse(path)
        self.assertGreater(len(df), 0)
        self.assertEqual(df["league"].iloc[0], "Scottish Premiership")
        self.assertEqual(df["country"].iloc[0], "Scotland")

    def test_league_comes_from_div_not_filename(self):
        """'E0 (5).csv' has nothing to do with league 5 — Div says Premier League."""
        from ingest.fduk import parse
        df = parse(os.path.join(UNSORTED, "E0 (5).csv"))
        self.assertEqual(set(df["league"]), {"Premier League"})
        self.assertEqual(set(df["season"]), {"2024/25"})

    def test_outcome_encoding(self):
        from ingest.fduk import parse
        df = parse(os.path.join(UNSORTED, "E0 (5).csv"))
        self.assertTrue(set(df["outcome"]).issubset({1, 2, 3}))
        home_wins = df[df["outcome"] == 1]
        self.assertTrue((home_wins["home_score"] > home_wins["away_score"]).all())
        draws = df[df["outcome"] == 2]
        self.assertTrue((draws["home_score"] == draws["away_score"]).all())

    def test_schema_is_canonical(self):
        from ingest import schema
        from ingest.fduk import parse
        df = parse(os.path.join(UNSORTED, "E0 (5).csv"))
        self.assertEqual(list(df.columns), schema.COLUMNS)


class TestNormalise(unittest.TestCase):
    def test_aliases_collapse_source_spellings(self):
        from clean.normalize import canon_team
        self.assertEqual(canon_team("Man United"), canon_team("Manchester United"))
        self.assertEqual(canon_team("Sundowns"), "Mamelodi Sundowns")
        self.assertEqual(canon_team("AFC Bournemouth"), canon_team("Bournemouth"))

    def test_dedupe_prefers_the_higher_trust_source(self):
        from clean.normalize import normalize
        rows = pd.DataFrame([
            dict(date="2024-08-16", league="Premier League", home_team="Man United",
                 away_team="Fulham", home_score=1, away_score=0, outcome=1,
                 source="TheSportsDB"),
            dict(date="2024-08-16", league="Premier League",
                 home_team="Manchester United", away_team="Fulham",
                 home_score=1, away_score=0, outcome=1, source="football-data.co.uk"),
        ])
        out = normalize(rows)
        self.assertEqual(len(out), 1, "the two spellings are the same fixture")
        self.assertEqual(out["source"].iloc[0], "football-data.co.uk")


class TestConsolidate(unittest.TestCase):
    def _row(self, home_score, away_score, outcome, source="football-data.co.uk"):
        return dict(date="2024-08-16", season="2024/25", league="Premier League",
                    country="England", home_team="Manchester United",
                    away_team="Fulham", home_score=home_score, away_score=away_score,
                    outcome=outcome, source=source)

    def test_added_unchanged_and_conflicts(self):
        from ingest import consolidate
        previous = pd.DataFrame([self._row(1, 0, 1)])
        same = pd.DataFrame([self._row(1, 0, 1)])
        self.assertEqual(consolidate.diff(previous, same).unchanged, 1)
        self.assertEqual(consolidate.diff(previous, same).added, 0)

        wrong = pd.DataFrame([self._row(2, 0, 1, source="TheSportsDB")])
        d = consolidate.diff(previous, wrong)
        self.assertEqual(d.conflict_count, 1, "different score = a conflict to inspect")

        fresh = pd.DataFrame([dict(self._row(3, 3, 2), date="2024-08-17",
                                   home_team="Arsenal", away_team="Chelsea")])
        self.assertEqual(consolidate.diff(previous, fresh).added, 1)


class TestManifest(unittest.TestCase):
    def test_only_new_or_changed_files_are_reingested(self):
        from ingest.manifest import Manifest, discover
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            path = os.path.join(src, "a.csv")
            with open(path, "w") as fh:
                fh.write("Div,Date\nE0,01/01/2024\n")

            man = Manifest(tmp)
            name, _p, digest, size = discover(src)[0]
            self.assertTrue(man.is_new_or_changed(name, digest))
            man.record(name, digest, size, 1)
            man.save()

            reloaded = Manifest(tmp)
            self.assertFalse(reloaded.is_new_or_changed(name, digest))
            with open(path, "a") as fh:
                fh.write("E0,02/01/2024\n")
            _n, _p, new_digest, _s = discover(src)[0]
            self.assertTrue(reloaded.is_new_or_changed(name, new_digest))


# ---------------------------------------------------------------------------
# The rate limiting — the part that keeps the account alive.
# ---------------------------------------------------------------------------
class TestBudgetCaps(unittest.TestCase):
    def test_ninety_percent_cap(self):
        from apis.ratelimit import Budget
        af = Budget("api-football", per_day=100, per_minute=10)
        self.assertEqual(af.day_cap, 90, "API-Football free tier 100/day -> 90")
        self.assertEqual(af.minute_cap, 9)

    def test_cap_scales_with_a_paid_tier(self):
        from apis.ratelimit import Budget
        self.assertEqual(Budget("api-football", per_day=7500).day_cap, 6750)

    def test_cap_never_rounds_down_to_zero(self):
        from apis.ratelimit import Budget
        self.assertEqual(Budget("tiny", per_day=1).day_cap, 1)

    def test_unlimited_provider_has_no_daily_cap(self):
        from apis.ratelimit import Budget
        self.assertIsNone(Budget("openfootball", per_minute=60).day_cap)


class TestQuotaStore(unittest.TestCase):
    def _store(self, tmp):
        from apis.ratelimit import QuotaStore
        return QuotaStore(os.path.join(tmp, ".quota.json"))

    def test_usage_persists_across_runs(self):
        """Ten runs in a day must share ONE budget, not get a fresh 100 each."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for _ in range(5):
                store.note_request("api-football")
            store.save()
            self.assertEqual(self._store(tmp).used_today("api-football"), 5)

    def test_counter_resets_on_a_new_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".quota.json")
            with open(path, "w") as fh:
                json.dump({"api-football": {"day": "2000-01-01", "day_used": 99}}, fh)
            from apis.ratelimit import QuotaStore
            self.assertEqual(QuotaStore(path).used_today("api-football"), 0)

    def test_provider_reported_usage_wins(self):
        """If the key was also used elsewhere, believe the provider, not us."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.note_request("api-football")
            store.sync_day_used("api-football", 61)
            self.assertEqual(store.used_today("api-football"), 61)


class TestSessionRefusesToOverspend(unittest.TestCase):
    def _session(self, tmp, used):
        from apis.ratelimit import Budget, BudgetedSession, QuotaStore
        store = QuotaStore(os.path.join(tmp, ".quota.json"))
        store.sync_day_used("api-football", used)
        return BudgetedSession(Budget("api-football", per_day=100, per_minute=10),
                               "https://example.invalid", store=store, verbose=False)

    def test_stops_at_the_cap_not_at_the_tier_limit(self):
        from apis.ratelimit import QuotaExhausted
        with tempfile.TemporaryDirectory() as tmp:
            sess = self._session(tmp, used=89)
            self.assertTrue(sess.can_spend(1))
            self.assertEqual(sess.remaining_today(), 1)

            spent = self._session(tmp, used=90)
            self.assertFalse(spent.can_spend(1))
            self.assertEqual(spent.remaining_today(), 0)
            with self.assertRaises((QuotaExhausted, Exception)):
                spent.get_json("anything")

    def test_ten_requests_of_headroom_are_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = self._session(tmp, used=90)
            self.assertEqual(sess.budget.per_day - 90, 10,
                             "10 requests of the free tier stay unspent as a buffer")


class TestNoNetworkFromTests(unittest.TestCase):
    def test_offline_switch_blocks_every_provider(self):
        from apis.ratelimit import Budget, BudgetedSession, OfflineError
        for provider in ("api-football", "football-data.org", "thesportsdb",
                         "openfootball"):
            sess = BudgetedSession(Budget(provider, per_day=100, per_minute=10),
                                   "https://example.invalid", verbose=False)
            with self.assertRaises(OfflineError, msg=f"{provider} was not blocked"):
                sess.get_json("status")

    def test_socket_is_stubbed_out(self):
        with self.assertRaises(AssertionError):
            socket.socket()


class TestApiParsersWithoutNetwork(unittest.TestCase):
    """The response -> canonical-rows mapping, tested on recorded payload shapes."""

    def test_api_football_finished_fixture_becomes_a_match_row(self):
        from apis import api_football
        payload = {"response": [{
            "fixture": {"id": 1, "date": "2024-05-04T15:00:00+00:00",
                        "status": {"short": "FT", "elapsed": 90},
                        "venue": {"name": "Loftus"}},
            "league": {"id": 288, "name": "Premier Soccer League",
                       "country": "South-Africa", "season": 2023},
            "teams": {"home": {"name": "Mamelodi Sundowns"},
                      "away": {"name": "Kaizer Chiefs"}},
            "goals": {"home": 2, "away": 1}}]}
        rows = api_football._rows(payload["response"])
        self.assertEqual(rows["league"].iloc[0], "Betway Premiership")
        self.assertEqual(rows["country"].iloc[0], "South Africa")
        match = api_football.to_matches(rows)
        self.assertEqual(len(match), 1)
        self.assertEqual(match["outcome"].iloc[0], 1)
        self.assertEqual(match["season"].iloc[0], "2023/24")

    def test_api_football_live_fixture_is_not_a_match_row(self):
        from apis import api_football
        rows = api_football._rows([{
            "fixture": {"id": 2, "date": "2026-09-02T17:30:00+00:00",
                        "status": {"short": "1H", "elapsed": 12}},
            "league": {"id": 288}, "teams": {"home": {"name": "Orlando Pirates"},
                                             "away": {"name": "Milford"}},
            "goals": {"home": 0, "away": 0}}])
        self.assertTrue(api_football.to_matches(rows).empty,
                        "an in-play match must not enter the training set")
        self.assertEqual(len(api_football.to_fixtures(rows)), 1)

    def test_plan_error_is_detected_not_raised(self):
        from apis import api_football
        msg = api_football._plan_error(
            {"errors": {"plan": "Free plans do not have access to this season, "
                                "try from 2022 to 2024."}})
        self.assertIn("2022", msg)

    def test_football_data_org_mapping(self):
        from apis import football_data_org
        rows = football_data_org._rows([{
            "id": 9, "utcDate": "2026-09-05T14:00:00Z", "status": "SCHEDULED",
            "competition": {"code": "PL", "name": "Premier League"},
            "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Chelsea FC"},
            "score": {"fullTime": {"home": None, "away": None}}}])
        self.assertEqual(rows["league"].iloc[0], "Premier League")
        self.assertTrue(football_data_org.to_matches(rows).empty)
        self.assertEqual(len(football_data_org.to_fixtures(rows)), 1)

    def test_thesportsdb_mapping(self):
        from apis import thesportsdb
        rows = thesportsdb._rows([{
            "idEvent": "1", "idLeague": "4802", "strTimestamp": "2026-08-01T15:00:00",
            "strHomeTeam": "Orlando Pirates", "strAwayTeam": "Milford",
            "intHomeScore": "2", "intAwayScore": "0", "strStatus": "FT"}])
        self.assertEqual(rows["league"].iloc[0], "Betway Premiership")
        self.assertEqual(thesportsdb.to_matches(rows)["outcome"].iloc[0], 1)

    def test_openfootball_mapping(self):
        from apis import openfootball
        from leagues import BY_NAME
        rows = openfootball._rows(
            {"matches": [{"date": "2024-08-16", "team1": "Manchester United FC",
                          "team2": "Fulham FC", "score": {"ft": [1, 0]}}]},
            BY_NAME["Premier League"])
        self.assertEqual(rows["outcome"].iloc[0], 1)
        self.assertEqual(rows["season"].iloc[0], "2024/25")

    def test_fixture_dedupe_prefers_api_football(self):
        from apis.topup import dedupe_fixtures
        df = pd.DataFrame([
            dict(kickoff_utc="2026-09-05T14:00:00Z", status="NS", league="Premier League",
                 home_team="Arsenal FC", away_team="Chelsea FC", source="TheSportsDB"),
            dict(kickoff_utc="2026-09-05T14:00:00Z", status="NS", league="Premier League",
                 home_team="Arsenal", away_team="Chelsea", source="API-Football"),
        ])
        out = dedupe_fixtures(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out["source"].iloc[0], "API-Football")


class TestStatusHandling(unittest.TestCase):
    def test_football_data_org_timestamp_in_status_is_repaired(self):
        """The API really does return a timestamp where the status belongs."""
        from apis import football_data_org
        rows = football_data_org._rows([{
            "id": 1, "utcDate": "2099-09-05T13:30:00Z",
            "status": "2026-09-05 13:30:00Z",
            "competition": {"code": "BL1"}, "homeTeam": {"name": "Bayer Leverkusen"},
            "awayTeam": {"name": "Union Berlin"},
            "score": {"fullTime": {"home": None, "away": None}}}])
        self.assertEqual(rows["status"].iloc[0], "SCHEDULED")

    def test_a_played_match_with_a_broken_status_is_still_finished(self):
        from apis import football_data_org
        rows = football_data_org._rows([{
            "id": 2, "utcDate": "2024-09-05T13:30:00Z", "status": "2024-09-05 13:30:00Z",
            "competition": {"code": "BL1"}, "homeTeam": {"name": "Bayer Leverkusen"},
            "awayTeam": {"name": "Union Berlin"},
            "score": {"fullTime": {"home": 2, "away": 1}}}])
        self.assertEqual(rows["status"].iloc[0], "FINISHED")
        self.assertEqual(football_data_org.to_matches(rows)["outcome"].iloc[0], 1)

    def test_finished_statuses_agree_across_sources(self):
        from ingest import schema
        for status in ("FT", "FINISHED", "AET"):
            self.assertTrue(schema.is_finished(status))
        for status in ("NS", "TIMED", "HT", "POSTPONED"):
            self.assertFalse(schema.is_finished(status))


class TestVariantCollapsing(unittest.TestCase):
    def test_accent_and_prefix_variants_become_one_club(self):
        from clean.normalize import _key
        for a, b in [("Málaga", "Malaga"), ("1. FC Köln", "1. FC Koln"),
                     ("CA Osasuna", "Osasuna"), ("AJ Auxerre", "Auxerre"),
                     ("Bayer 04 Leverkusen", "Bayer Leverkusen"),
                     ("Celta de Vigo", "Celta Vigo"), ("ST Mirren", "St Mirren")]:
            self.assertEqual(_key(a), _key(b), f"{a!r} and {b!r} must share a key")

    def test_different_clubs_keep_different_keys(self):
        from clean.normalize import _key
        self.assertNotEqual(_key("Paderborn 07"), _key("07 Elversberg"))
        self.assertNotEqual(_key("Sheffield United"), _key("Sheffield Wednesday"))
        self.assertNotEqual(_key("Sporting Clube de Braga"),
                            _key("Sporting Clube de Portugal"))

    def test_collapse_picks_the_dominant_spelling(self):
        from clean.normalize import normalize
        rows = pd.DataFrame(
            [dict(date=f"2024-08-{d:02d}", league="La Liga", home_team="Malaga",
                  away_team="Osasuna", home_score=1, away_score=0, outcome=1,
                  source="football-data.co.uk") for d in range(1, 11)]
            + [dict(date="2024-09-01", league="La Liga", home_team="Málaga",
                    away_team="CA Osasuna", home_score=1, away_score=0, outcome=1,
                    source="football-data.org")])
        out = normalize(rows)
        self.assertEqual(set(out["home_team"]), {"Malaga"})
        self.assertEqual(set(out["away_team"]), {"Osasuna"})


class TestUtcDateHandling(unittest.TestCase):
    """Regression: the pipeline used the machine's LOCAL date to decide which
    days to fetch, while every fixture timestamp is UTC. In South Africa (UTC+2)
    the local date rolls over at 22:00 UTC, so the evening run asked for tomorrow
    and never fetched the day still being played — matches sat frozen at
    half-time. GitHub Actions runs in UTC, so CI never saw it."""

    def test_today_is_utc_not_local(self):
        import datetime as dt
        from ingest.clock import utc_today
        self.assertEqual(utc_today(), dt.datetime.now(dt.timezone.utc).date())

    def test_fixture_window_includes_yesterday(self):
        """A match that kicked off at 19:45 and finished after the last run only
        shows its final score on its own date, and drops out of `live=all` the
        moment it ends — so the backward day is what closes it out."""
        import inspect
        from apis import api_football
        src = inspect.getsource(api_football.upcoming_window)
        self.assertIn('range(-1,', src, 'the window must reach back a day')
        self.assertIn('utc_today()', src, 'the window must be anchored to UTC')

    def test_todays_date_query_is_not_cached_for_hours(self):
        """Today's date query is a live scoreboard, not a fixture list. Caching
        it for three hours is what kept serving half-time scores past full time."""
        import datetime as dt
        from apis import cache
        today = dt.date(2026, 9, 2)
        self.assertEqual(cache.ttl_for_date(today, today), cache.TTL_TODAY)
        self.assertLessEqual(cache.ttl_for_date(today, today), 300)
        self.assertEqual(cache.ttl_for_date(today + dt.timedelta(days=2), today),
                         cache.TTL_FIXTURES)

    def test_window_covering_today_is_short_lived(self):
        import datetime as dt
        from apis import cache
        today = dt.date(2026, 9, 2)
        self.assertEqual(
            cache.ttl_for_window(today - dt.timedelta(days=7), today + dt.timedelta(days=7), today),
            cache.TTL_TODAY)
        self.assertEqual(
            cache.ttl_for_window(today + dt.timedelta(days=3), today + dt.timedelta(days=9), today),
            cache.TTL_FIXTURES)

    def test_quota_day_rolls_on_utc(self):
        """Providers reset the daily quota on the UTC day; the local date would
        hand us a fresh budget hours early and blow through the real cap."""
        import inspect
        from apis.ratelimit import QuotaStore
        self.assertIn('utc_today', inspect.getsource(QuotaStore._today))


class TestTargetWindow(unittest.TestCase):
    def test_target_window_covers_every_season_on_disk(self):
        """The report can only flag a hole inside TARGET_SEASONS, so the window
        has to grow when older seasons are added or the oldest ones go unchecked."""
        import os
        import pandas as pd
        from leagues import TARGET_SEASONS
        path = os.path.join(DATA_DIR, "output", "model_data.csv")
        if not os.path.exists(path):
            self.skipTest("no dataset built yet")
        seasons = set(pd.read_csv(path, usecols=["season"])["season"].dropna())
        missing = sorted(seasons - set(TARGET_SEASONS))
        self.assertEqual(missing, [],
                         f"dataset holds seasons the report never checks: {missing}")


class TestSplitClubMerging(unittest.TestCase):
    """The long official names football-data.org uses would otherwise split one
    club in two — leaving e.g. Benfica with 3 matches of history instead of 275,
    which hands the model a default ELO for a club it should know well."""

    def test_short_and_official_names_are_recognised_as_one_club(self):
        from clean.normalize import _same_club
        for short, official in [
                ("Benfica", "Sport Lisboa e Benfica"),
                ("AZ Alkmaar", "AZ"), ("Lens", "Racing Club de Lens"),
                ("Feyenoord", "Feyenoord Rotterdam"),
                ("Willem II", "Willem II Tilburg"),
                ("Estoril", "GD Estoril Praia"), ("Real Betis", "Real Betis Balompié"),
                ("Brest", "Stade Brestois 29"), ("Rennes", "Stade Rennais FC 1901"),
                ("NEC", "NEC Nijmegen"), ("Cambuur", "Cambuur-Leeuwarden")]:
            self.assertTrue(_same_club(short, official),
                            f"{short!r} and {official!r} are the same club")

    def test_genuinely_different_clubs_are_never_merged(self):
        from clean.normalize import _same_club
        for a, b in [("Real Madrid", "Real Sociedad"),
                     ("Manchester United", "Manchester City"),
                     ("Sheffield United", "Sheffield Wednesday"),
                     ("Sporting CP", "Sporting Clube de Braga"),
                     ("Bayern Munchen", "Bayer Leverkusen")]:
            self.assertFalse(_same_club(a, b), f"{a!r} and {b!r} are different clubs")

    def test_reserve_sides_are_not_merged_into_the_first_team(self):
        from clean.normalize import _same_club
        self.assertFalse(_same_club("Sparta Praha", "Sparta Praha II"))
        self.assertFalse(_same_club("Real Madrid", "Real Madrid Castilla"))
        # ...but a club whose real name contains a numeral keeps working
        self.assertTrue(_same_club("Willem II", "Willem II Tilburg"))

    def test_a_generic_word_cannot_swallow_a_club(self):
        from clean.normalize import _same_club
        self.assertFalse(_same_club("Real", "Real Madrid"))
        self.assertFalse(_same_club("United", "Manchester United"))

    def test_clubs_that_played_each_other_are_never_merged(self):
        from clean.normalize import merge_split_clubs
        df = pd.DataFrame([
            dict(league="La Liga", home_team="Barcelona",
                 away_team="Espanyol de Barcelona", source="football-data.org"),
            dict(league="La Liga", home_team="Barcelona",
                 away_team="Espanyol de Barcelona", source="football-data.org"),
        ])
        out = merge_split_clubs(df, verbose=False)
        self.assertEqual(set(out["home_team"]), {"Barcelona"})
        self.assertEqual(set(out["away_team"]), {"Espanyol de Barcelona"})

    def test_ambiguous_matches_are_left_alone_not_guessed(self):
        """'Internazionale Milano' matches both 'Inter Milan' and 'AC Milan'.
        Guessing wrong fuses two clubs' histories, so it must not guess."""
        from clean.normalize import merge_split_clubs
        rows = ([dict(league="Serie A", home_team="Inter Milan", away_team="Lazio",
                      source="football-data.co.uk")] * 5
                + [dict(league="Serie A", home_team="AC Milan", away_team="Roma",
                        source="football-data.co.uk")] * 5
                + [dict(league="Serie A", home_team="Internazionale Milano",
                        away_team="Napoli", source="football-data.org")])
        out = merge_split_clubs(pd.DataFrame(rows), verbose=False)
        self.assertIn("Internazionale Milano", set(out["home_team"]),
                      "an ambiguous name must be reported, not merged")

    def test_unambiguous_match_is_merged(self):
        from clean.normalize import merge_split_clubs
        rows = ([dict(league="Liga Portugal", home_team="Benfica", away_team="Porto",
                      source="football-data.co.uk")] * 20
                + [dict(league="Liga Portugal", home_team="Sport Lisboa e Benfica",
                        away_team="Braga", source="football-data.org")])
        out = merge_split_clubs(pd.DataFrame(rows), verbose=False)
        self.assertNotIn("Sport Lisboa e Benfica", set(out["home_team"]))
        self.assertEqual((out["home_team"] == "Benfica").sum(), 21)

    def test_fixtures_are_aligned_to_the_training_vocabulary(self):
        """A fixture must resolve to the club name the model was trained on."""
        from clean.normalize import align_to_vocabulary
        reference = pd.DataFrame([
            dict(league="Liga Portugal", home_team="Benfica", away_team="Porto",
                 source="football-data.co.uk")] * 10)
        fixtures = pd.DataFrame([dict(league="Liga Portugal",
                                      home_team="Sport Lisboa e Benfica",
                                      away_team="Porto", source="football-data.org")])
        out = align_to_vocabulary(fixtures, reference)
        self.assertEqual(out["home_team"].iloc[0], "Benfica")


class TestGapFillTargeting(unittest.TestCase):
    def test_only_genuinely_missing_league_seasons_are_fetched(self):
        """openfootball has known-bad rows, so it must only touch real gaps."""
        from apis.openfootball import missing_pairs
        from leagues import BY_NAME
        leagues = [BY_NAME["Premier League"], BY_NAME["Bundesliga"]]
        have = pd.DataFrame([{"league": "Premier League", "season": "2019/20"}] * 380)
        gaps = missing_pairs(have, ["2018/19", "2019/20"], leagues)
        self.assertNotIn(("Premier League", "2019/20"), gaps)
        self.assertIn(("Premier League", "2018/19"), gaps)
        self.assertIn(("Bundesliga", "2019/20"), gaps)

    def test_a_thin_season_still_counts_as_missing(self):
        from apis.openfootball import missing_pairs
        from leagues import BY_NAME
        have = pd.DataFrame([{"league": "Premier League", "season": "2019/20"}] * 3)
        self.assertIn(("Premier League", "2019/20"),
                      missing_pairs(have, ["2019/20"], [BY_NAME["Premier League"]]))

    def test_openfootball_payload_variants(self):
        """Season files are not all the same shape."""
        from apis.openfootball import _matches, _score, _team
        self.assertEqual(len(_matches({"matches": [{}, {}]})), 2)
        self.assertEqual(len(_matches({"rounds": [{"matches": [{}]}, {"matches": [{}]}]})), 2)
        self.assertEqual(_score({"score": {"ft": [2, 1]}}), (2, 1))
        self.assertEqual(_score({"score1": 3, "score2": 0}), (3, 0))
        self.assertEqual(_score({"score": {}}), (None, None))
        self.assertEqual(_team({"name": "Ajax"}), "Ajax")
        self.assertEqual(_team("Ajax"), "Ajax")


class TestTursoSink(unittest.TestCase):
    """The mapping and the SQL contract — no connection is opened (see the
    module guards); `Turso()` itself is never constructed here."""

    def _fixture(self, status, home=None, away=None, minute=None):
        return pd.DataFrame([{
            "kickoff_utc": "2026-09-02T17:30:00+00:00", "status": status,
            "minute": minute, "league": "Betway Premiership",
            "country": "South Africa", "season": "2026/27",
            "home_team": "Mamelodi Sundowns", "away_team": "Milford",
            "home_score": home, "away_score": away, "venue": "Loftus",
            "source": "API-Football", "source_match_id": "123", "fetched_at": None}])

    def test_status_grouping(self):
        from sinks.turso import status_group
        self.assertEqual(status_group("FT"), "finished")
        self.assertEqual(status_group("FINISHED"), "finished")
        self.assertEqual(status_group("HT"), "in_play")
        self.assertEqual(status_group("IN_PLAY"), "in_play")
        self.assertEqual(status_group("NS"), "scheduled")
        self.assertEqual(status_group("TIMED"), "scheduled")
        self.assertEqual(status_group("POSTPONED"), "off")

    def test_progress_rank_is_monotonic(self):
        from sinks.turso import PROGRESS
        self.assertLess(PROGRESS["scheduled"], PROGRESS["in_play"])
        self.assertLess(PROGRESS["in_play"], PROGRESS["finished"])

    def test_upsert_never_deletes_and_only_moves_forward(self):
        """The two properties the whole design rests on, asserted on the SQL."""
        from sinks.turso import UPSERT_SQL, SCHEMA_SQL
        sql = UPSERT_SQL.upper()
        self.assertNotIn("DELETE", sql)
        self.assertIn("ON CONFLICT(MATCH_KEY) DO UPDATE", sql)
        self.assertIn("WHERE EXCLUDED.PROGRESS >= FIXTURES.PROGRESS", sql)
        # finished_at must be COALESCEd from the EXISTING row, i.e. stamped once
        self.assertIn("FINISHED_AT     = COALESCE(FIXTURES.FINISHED_AT", sql)
        self.assertFalse(any("DROP" in stmt.upper() for stmt in SCHEMA_SQL))

    def test_match_key_matches_the_training_set(self):
        """A fixture row must join to model_data.csv on equality, or the later
        'did the model get it right?' feature has nothing to join on."""
        from clean.normalize import match_key, normalize
        from sinks.turso import to_rows
        key_from_fixture = to_rows(self._fixture("FT", 2, 1))[0][1][0]
        history = normalize(pd.DataFrame([dict(
            date="2026-09-02", league="Betway Premiership",
            home_team="Mamelodi Sundowns", away_team="Milford",
            home_score=2, away_score=1, outcome=1, source="API-Football")]))
        self.assertEqual(key_from_fixture, match_key(history).iloc[0])

    def test_outcome_only_set_once_finished(self):
        from sinks.turso import to_rows
        live = to_rows(self._fixture("1H", 2, 1, minute=60))[0][1]
        done = to_rows(self._fixture("FT", 2, 1))[0][1]
        self.assertIsNone(live[13], "an in-play score is not a result")
        self.assertEqual(done[13], 1)

    def test_finished_at_only_stamped_for_finished_rows(self):
        from sinks.turso import to_rows
        self.assertIsNone(to_rows(self._fixture("NS"))[0][1][19])
        self.assertIsNotNone(to_rows(self._fixture("FT", 0, 0))[0][1][19])

    def test_draw_and_away_win_encoding(self):
        from sinks.turso import to_rows
        self.assertEqual(to_rows(self._fixture("FT", 1, 1))[0][1][13], 2)
        self.assertEqual(to_rows(self._fixture("FT", 0, 3))[0][1][13], 3)

    def test_libsql_url_is_converted_to_https(self):
        import os
        from sinks import turso
        os.environ["TURSO_FIXTURES_URL"] = "libsql://example.turso.io"
        try:
            self.assertEqual(turso.url(), "https://example.turso.io")
        finally:
            os.environ.pop("TURSO_FIXTURES_URL")

    def test_wire_encoding_of_values(self):
        from sinks.turso import _arg, _decode
        self.assertEqual(_arg(None), {"type": "null"})
        self.assertEqual(_arg(3), {"type": "integer", "value": "3"})
        self.assertEqual(_arg("FT"), {"type": "text", "value": "FT"})
        self.assertEqual(_arg(pd.NA), {"type": "null"})
        self.assertEqual(_decode({"type": "integer", "value": "7"}), 7)
        self.assertIsNone(_decode({"type": "null"}))

    def test_client_refuses_to_be_built_without_credentials(self):
        """Missing config must fail loud at construction, not silently no-op."""
        import unittest.mock
        from sinks.turso import Turso, TursoError
        cleared = {k: "" for k in
                   ("TURSO_FIXTURES_URL", "TURSO_MATCHES_URL", "TURSO_DATABASE_URL",
                    "TURSO_FIXTURES_TOKEN", "TURSO_MATCHES_TOKEN", "TURSO_AUTH_TOKEN")}
        with unittest.mock.patch.dict(os.environ, cleared):
            with self.assertRaises(TursoError):
                Turso()


class TestRobotsEnforcement(unittest.TestCase):
    """The scrapers must refuse disallowed paths, and this is the check that
    matters most: Python's stdlib RobotFileParser ignores wildcard patterns, so
    `Disallow: /*?year=` reads as ALLOWED there. That is precisely the rule
    covering BetExplorer's dated archive pages."""

    BETEXPLORER_ROBOTS = """
User-agent: *
Disallow: /ad/
Disallow: /redirect/
Disallow: /bookmaker/
Disallow: /*?year=
Disallow: /*?stage=
Disallow: /*?page=
Allow: /reviews/wp-admin/admin-ajax.php
"""

    def _fetcher_with(self, robots_text, origin):
        from scrapers.base import PoliteFetcher, parse_wildcard_rules
        import urllib.robotparser
        f = PoliteFetcher(verbose=False)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots_text.splitlines())
        f._robots[origin] = rp
        f._wildcard[origin] = parse_wildcard_rules(robots_text)
        return f

    def test_wildcard_disallow_is_honoured(self):
        origin = 'https://www.betexplorer.com'
        f = self._fetcher_with(self.BETEXPLORER_ROBOTS, origin)
        for path, allowed in [
                ('/football/south-africa/betway-premiership/results/', True),
                ('/?year=2024&month=9&day=16', False),
                ('/football/south-africa/betway-premiership/?stage=xyz', False),
                ('/bookmaker/bet365', False),
                ('/ad/banner', False),
        ]:
            self.assertEqual(f.allowed(origin + path), allowed, f'{path} verdict wrong')

    def test_stdlib_alone_would_have_got_this_wrong(self):
        """Documents why the extra matcher exists."""
        import urllib.robotparser
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(self.BETEXPLORER_ROBOTS.splitlines())
        self.assertTrue(
            rp.can_fetch('*', 'https://www.betexplorer.com/?year=2024&month=9'),
            'if the stdlib ever learns wildcards this test can go, but the '
            'wildcard matcher must stay correct either way')

    def test_verdict_is_the_stricter_of_the_two_matchers(self):
        """We take the STRICTER of the stdlib parser and our wildcard matcher.

        The two disagree on longest-match precedence: given `Disallow: /data/`
        plus a more specific `Allow: /data/public/`, the spec says allow and our
        matcher says allow, but the stdlib returns first-match and says deny.
        We follow the stdlib there and skip the page.

        That is the right way round to be wrong. Being stricter than the spec
        costs us a page we could have fetched; being laxer means fetching a page
        the site asked us not to.
        """
        origin = 'https://example.com'
        f = self._fetcher_with(
            "User-agent: *\nDisallow: /data/\nAllow: /data/public/\n", origin)
        self.assertFalse(f.allowed(origin + '/data/secret'))
        self.assertFalse(f.allowed(origin + '/data/public/file.csv'),
                         'stricter-of-the-two: the stdlib denies this one')
        # Nothing outside the disallowed subtree is affected.
        self.assertTrue(f.allowed(origin + '/other/file.csv'))

    def test_fetch_raises_rather_than_warning(self):
        from scrapers.base import Disallowed
        origin = 'https://www.betexplorer.com'
        f = self._fetcher_with(self.BETEXPLORER_ROBOTS, origin)
        with self.assertRaises(Disallowed):
            f.get(origin + '/?year=2024&month=9&day=16')


class TestScraperParsers(unittest.TestCase):
    BETEXPLORER_ROW = (
        '<tr><td class="h-text-left">'
        '<a data-test="1" href="/m/" class="in-match">'
        '<span><strong>Mamelodi Sundowns</strong></span> - <span>Milford FC</span></a></td>'
        '<td class="h-text-center"><a href="/m/">2:0</a></td>'
        '<td class="table-main__odds colored"><span><span><span data-odd="1.27"></span></span></span></td>'
        '<td class="table-main__odds" data-odd="5.13"></td>'
        '<td class="table-main__odds" data-odd="9.23"></td>'
        '<td class="h-text-right">02.09.2026</td></tr>'
    )

    GSA_MATCH = (
        '<a href="https://globalsportsarchive.com/en/soccer/match/2019-05-11/'
        'bloemfontein-celtic-fc-vs-amazulu-fc/1395246" class="x">'
        '<div class="gsa-d-sm-md-none gsa-d-block">Bloemfontein Celtic FC</div>'
        '<div class="gsa-match-rounds-v2__match-score gsa-flex-center">'
        '<div class="gsa-text-yellow"> 3 </div>'
        '<div class="gsa-text-grey">&nbsp;:&nbsp;</div>'
        '<div class="gsa-text-grey"> 1 </div></div>'
        '<div class="gsa-d-sm-md-none gsa-d-block">AmaZulu FC</div></a>'
    )

    def test_betexplorer_row_gives_result_and_odds(self):
        from scrapers.betexplorer import parse_results
        df = parse_results(self.BETEXPLORER_ROW)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row['home_team'], 'Mamelodi Sundowns')
        self.assertEqual(row['away_team'], 'Milford FC')
        self.assertEqual((row['home_score'], row['away_score']), (2, 0))
        self.assertEqual(row['outcome'], 1)
        self.assertAlmostEqual(row['odds_home'], 1.27)
        self.assertAlmostEqual(row['odds_away'], 9.23)
        self.assertEqual(row['league'], 'Betway Premiership')

    def test_betexplorer_is_the_odds_carrier_in_the_dedupe(self):
        """Same fixture from two sources: keep the one with odds."""
        from clean.normalize import normalize
        from ingest import schema
        self.assertLess(schema.SOURCE_PRIORITY['betexplorer'],
                        schema.SOURCE_PRIORITY['globalsportsarchive'])
        rows = pd.DataFrame([
            dict(date='2026-09-02', league='Betway Premiership', home_team='Mamelodi Sundowns',
                 away_team='Milford', home_score=2, away_score=0, outcome=1,
                 source='globalsportsarchive'),
            dict(date='2026-09-02', league='Betway Premiership', home_team='Mamelodi Sundowns FC',
                 away_team='Milford FC', home_score=2, away_score=0, outcome=1,
                 odds_home=1.27, odds_draw=5.13, odds_away=9.23, source='betexplorer'),
        ])
        out = normalize(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out['source'].iloc[0], 'betexplorer')
        self.assertAlmostEqual(out['odds_home'].iloc[0], 1.27)

    def test_gsa_match_anchor_parses(self):
        from scrapers.globalsportsarchive import parse_season
        df = parse_season(self.GSA_MATCH, '2018/19')
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row['home_team'], 'Bloemfontein Celtic FC')
        self.assertEqual(row['away_team'], 'AmaZulu FC')
        self.assertEqual(row['outcome'], 1)
        self.assertEqual(row['season'], '2018/19')

    def test_gsa_skips_unplayed_fixtures(self):
        from scrapers.globalsportsarchive import parse_season
        unplayed = self.GSA_MATCH.replace(
            '<div class="gsa-text-yellow"> 3 </div>', '<div class="gsa-text-yellow"></div>')
        self.assertTrue(parse_season(unplayed, '2026/27').empty)

    def test_gsa_derives_season_from_date_on_team_pages(self):
        """A club's /matches page spans seasons, so each row dates itself."""
        from scrapers.globalsportsarchive import parse_season
        df = parse_season(self.GSA_MATCH, None)
        self.assertEqual(df['season'].iloc[0], '2018/19')

    def test_club_abbreviations_do_not_split_a_club(self):
        from clean.normalize import _key
        self.assertEqual(_key('Chippa Utd.'), _key('Chippa United'))
        self.assertEqual(_key('Sheffield Utd'), _key('Sheffield United'))
        self.assertNotEqual(_key('Sheffield United'), _key('Sheffield Wednesday'))


class TestCanonicalCsvIngest(unittest.TestCase):
    """Scraper output lands in unsorted/ in our own schema, so the pipeline needs
    no bespoke parser per scraped source."""

    def test_canonical_csv_is_detected_and_parsed(self):
        import tempfile
        from ingest import canonical
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'sa-scraped.csv')
            pd.DataFrame([dict(
                date='2026-09-02', season='2026/27', league='Betway Premiership',
                country='South Africa', home_team='Mamelodi Sundowns', away_team='Milford',
                home_score=2, away_score=0, outcome=1, odds_home=1.27, odds_draw=5.13,
                odds_away=9.23, source='betexplorer')]).to_csv(path, index=False)
            self.assertTrue(canonical.looks_canonical(path))
            df = canonical.parse(path)
            self.assertEqual(len(df), 1)
            self.assertEqual(df['outcome'].iloc[0], 1)
            self.assertAlmostEqual(df['odds_home'].iloc[0], 1.27)

    def test_outcome_is_recomputed_not_trusted(self):
        """A scraper that mislabels the outcome must not poison the label."""
        import tempfile
        from ingest import canonical
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'bad.csv')
            pd.DataFrame([dict(
                date='2026-09-02', league='Betway Premiership', home_team='A',
                away_team='B', home_score=0, away_score=3, outcome=1,
                source='someone')]).to_csv(path, index=False)
            self.assertEqual(canonical.parse(path)['outcome'].iloc[0], 3)

    def test_a_football_data_csv_is_not_mistaken_for_canonical(self):
        from ingest import canonical
        path = os.path.join(UNSORTED, 'E0.csv')
        self.assertFalse(canonical.looks_canonical(path))


class TestMatchStatsIngest(unittest.TestCase):
    def test_shots_corners_and_cards_survive_into_the_dataset(self):
        from ingest.fduk import parse
        df = parse(os.path.join(UNSORTED, 'E0 (5).csv'))
        for col in ('home_shots', 'away_shots', 'home_shots_on_target',
                    'home_corners', 'away_corners', 'home_fouls',
                    'home_yellow', 'home_red', 'home_half_score'):
            self.assertIn(col, df.columns)
            self.assertTrue(df[col].notna().any(), f'{col} came through empty')

    def test_half_time_score_never_exceeds_full_time(self):
        from ingest.fduk import parse
        df = parse(os.path.join(UNSORTED, 'E0 (5).csv')).dropna(
            subset=['home_half_score', 'away_half_score'])
        self.assertTrue((df['home_half_score'] <= df['home_score']).all())
        self.assertTrue((df['away_half_score'] <= df['away_score']).all())

    def test_stats_are_optional_for_sources_that_lack_them(self):
        """API and scraped rows have no stats; that must not drop the row."""
        from clean.normalize import normalize
        rows = pd.DataFrame([dict(
            date='2026-09-02', league='Betway Premiership', home_team='A', away_team='B',
            home_score=2, away_score=0, outcome=1, source='API-Football')])
        out = normalize(rows)
        self.assertEqual(len(out), 1)
        self.assertTrue(pd.isna(out['home_shots'].iloc[0]))


class TestLeagueRegistry(unittest.TestCase):
    def test_betway_premiership_is_api_only(self):
        from leagues import SA_LEAGUE
        self.assertEqual(SA_LEAGUE.country, "South Africa")
        self.assertIsNone(SA_LEAGUE.fduk_div, "not on football-data.co.uk")
        self.assertEqual(SA_LEAGUE.af_id, 288)
        self.assertEqual(SA_LEAGUE.tsdb_id, "4802")

    def test_every_div_code_is_unique(self):
        from leagues import REGISTRY
        divs = [lg.fduk_div for lg in REGISTRY if lg.fduk_div]
        self.assertEqual(len(divs), len(set(divs)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
