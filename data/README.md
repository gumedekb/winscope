# winscope-data — ETL / data pipeline

Turns raw football data into ONE clean training file for the model, and keeps it fresh from
the live APIs. Design notes live in the Obsidian vault (`03 - Data Sources`,
`04 - Data Collection Strategy`).

**The app is `pipeline.py`.** It scans `unsorted/`, consolidates everything into
`output/model_data.csv`, tops up the leagues the dump cannot cover from the APIs, writes
`output/fixtures.csv` (live + upcoming), and prints a coverage report.

```bash
pip install -r requirements.txt
python pipeline.py                 # files only — free, no network at all
python pipeline.py --with-api      # + Betway Premiership history + live/upcoming fixtures
python pipeline.py --live          # fixtures only -> output/fixtures.csv + Turso
```

---

## 📂 What's in this folder

| Path | What it is |
|---|---|
| **`pipeline.py`** | **The app.** CLI entrypoint for everything below. |
| `ingest/` | File side: `fduk.py` (football-data.co.uk parser), `footystats.py`, `manifest.py` (incremental ingest), `seasons.py`, `consolidate.py` (merge + diff), `report.py` (coverage), `audit.py` (team-name audit), `schema.py`, `env.py`. |
| `sinks/turso.py` | Publishes fixtures to Turso (libSQL) over its HTTP API — no new dependency. |
| `scrapers/` + `scrape.py` | One-off scrapers for the SA league — the only source of **odds** for it. robots.txt is enforced, not consulted. |
| `apis/` | API side: `ratelimit.py` (**the 90% caps**), `cache.py`, `api_football.py`, `football_data_org.py`, `thesportsdb.py`, `openfootball.py`, `topup.py` (orchestration). |
| `clean/` | `normalize.py` (canonicalise, dedupe, collapse name variants) + `team_aliases.json`. |
| `leagues.py` | The league registry: one row per league, with every source's id for it. |
| `tests/` | 40 offline tests. **Physically cannot touch an API** — see *Testing* below. |
| `unsorted/` | Your raw dump — football-data.co.uk CSVs (95 files) + 5 FootyStats files. |
| `output/` | `model_data.csv`, `fixtures.csv`, `conflicts.csv`, `.manifest.json`, `.quota.json`, `cache/`. |
| `.env` / `.env.example` | API keys (`.env` is gitignored). |
| `collectors/`, `run.py` | **Mode A**, the original downloader (`training.csv`). Kept as reference; `pipeline.py` supersedes it. |

---

## ▶️ Commands

| Command | What it does | API cost |
|---|---|---|
| `python pipeline.py` | Scan `unsorted/`, merge, report. **No network.** | 0 |
| `python pipeline.py --with-api` | The above + SA PSL history + live/upcoming fixtures | ~7 API-Football, ~2 football-data.org, ~31 TheSportsDB |
| `python pipeline.py --live` | Refresh fixtures **and push them to Turso** | ~3 API-Football, ~2 football-data.org |
| `python pipeline.py --api-only` | API stage without rescanning files | as `--with-api` |
| `python pipeline.py --openfootball` | Fill league-seasons the dataset is **actually missing**, free | 0 (keyless) |
| `python pipeline.py --cross-check` | Diff openfootball against our data, **merging nothing** | 0 (keyless) |
| `python pipeline.py --quota` | Today's budget usage for every provider | 0 |
| `python pipeline.py --audit-teams` | Find cross-source team-name mismatches | 0 |
| `python pipeline.py --turso-status` | What is currently in the Turso table | 0 |
| `python badges.py` | Fetch club crests into Turso (one-off) | 0 (keyless) |
| `python pipeline.py --from-turso` | Fold Turso's finished matches back into `model_data.csv` | 0 |
| `python pipeline.py --no-turso` | Run normally but skip the database push | 0 |
| `python pipeline.py --report-only` | Coverage report for the existing CSV | 0 |
| `python pipeline.py --full-rescan` | Ignore the manifest, reparse every CSV | 0 |
| `python pipeline.py --footystats` | Also ingest the FootyStats `*-matches-*` files | 0 |

Add `--force-refresh` to bypass the response cache (this **spends real quota**) and
`--all-leagues` to keep API fixtures for leagues outside `leagues.py`.

---

## 🔑 API keys & the 90% caps

Keys live in `.env` (gitignored; `.env.example` documents each one). Real environment
variables win over the file, so GitHub Actions secrets override it with no code change.

| Provider | Free tier | **We stop at** | Needs a key |
|---|---|---|---|
| [API-Football v3](https://v3.football.api-sports.io) | 100/day, 10/min | **90/day, 9/min** | yes |
| [football-data.org](https://www.football-data.org/) | 10/min | **9/min** | yes |
| [TheSportsDB](https://www.thesportsdb.com/) | ~30/min (key `3`) | **27/min** | no (free key) |
| [openfootball](https://openfootball.github.io/) | none published | 54/min (politeness) | no |

Three layers keep API-Football — the one that will actually ban you — under its limit:

1. **Persistent counters** in `output/.quota.json`. Ten runs in a day share **one** budget;
   they do not each get a fresh 100.
2. **The provider's own numbers win.** API-Football reports the day's true usage; that
   overwrites our local count, so usage from another machine or the web dashboard counts too.
3. **A sliding per-minute window**, also at 90%, that sleeps instead of bursting.

At the cap, `QuotaExhausted` is raised, the collector logs and degrades, and the rest of the
run continues. On top of that, every response is cached on disk (`output/cache/`) — a re-run
inside the TTL costs **zero** requests. Check any time with `python pipeline.py --quota`.

> Upgraded a plan? Set `API_SPORTS_DAILY_LIMIT` in `.env` and the 90% cap scales with it.

---

## 🖼️ Club badges

```bash
python badges.py            # clubs in the last two seasons (~257)
python badges.py --missing  # only the ones with no badge yet
python badges.py --report   # what is stored, weakest matches first
```

Writes a Turso `team_assets` table keyed on **our canonical club name** — the same key
fixtures and the model use, so the web joins on it with no id mapping. Source is TheSportsDB
(no key needed). **241 of 257 clubs, and 98% of the fixtures on the board, have a real crest**;
the rest fall back to a drawn initials badge.

Three things this needed that are worth knowing:

- **Filter to `strSport == "Soccer"`.** Searching "Bayern Munchen" otherwise returns Bayern
  München *Basketball* first, and the card gets a basketball crest.
- **The alias map doubles as the query list.** TheSportsDB has its own name for everything —
  "Bayern Munchen" finds nothing, "Bayern Munich" works; likewise `Lille OSC`, `PEC Zwolle`,
  `Racing Santander`. Rather than a second mapping, `_query_variants()` tries every spelling
  `clean/team_aliases.json` already records for that club.
- **Never cache an empty result.** The first run "lost" Chelsea and Celtic to throttling, and
  the empty responses were cached for a week — turning a transient miss into a permanent one.
  Empty lookups are now un-cached, the bulk job paces at 12 req/min, and results are saved
  every 20 clubs so a single Turso timeout cannot discard the whole run (it did, once).

Club names are read from **both** `model_data.csv` and the Turso `fixtures` table. Editing
`team_aliases.json` changes which spelling wins, and `model_data.csv` is only re-normalised on
a rebuild — reading one source alone leaves clubs on the board with no badge.

---

## 🕷️ Scraping the South African league

`python scrape.py` writes `unsorted/sa-betway-premiership-scraped.csv`, then
`python pipeline.py` ingests it like any other file. Run it whenever you want to top the
league up; pages are cached on disk so a re-run costs the sites nothing.

| Source | Gives | Why limited to this |
|---|---|---|
| **betexplorer.com** | current season, results **+ 1X2 odds** | Its robots.txt disallows `?year=`, `?month=` and `?stage=` — and the season archives live behind `?stage=`. Only the plain `/results/` path is touched. |
| **globalsportsarchive.com** | recent seasons, results, no odds | `Allow: /`, nothing off-limits. Season pages render only the latest round (the rest arrives via JavaScript), so the clubs' `/matches` pages are walked instead. |

**robots.txt is enforced in code, not just read.** A disallowed URL raises `Disallowed`
rather than being fetched, so the scraper cannot quietly start hitting the archives later.
Note that Python's stdlib `RobotFileParser` **ignores wildcard patterns** — it reads
`Disallow: /*?year=` as *allowed*, which is exactly the rule covering the dated archive
pages — so `scrapers/base.py` adds its own matcher and takes the stricter of the two
verdicts. There are tests for this.

What this bought: the SA league went from **751 → 868 matches**, 2025/26 from 5 → 90, and
2026/27 now has **88% odds coverage** where it previously had none.

Not obtainable from these two sites: SA seasons **2016/17–2021/22**. GSA holds them but
serves one round at a time via JavaScript; BetExplorer holds them behind URLs its robots.txt
disallows.

### Any scraper can feed the pipeline
`ingest/canonical.py` recognises a CSV that is already in the canonical schema, by column
shape rather than filename. So a new scraper only has to write those columns into
`unsorted/` — no new parser per source, and the row is deduped against every other source by
trust. `outcome` is always recomputed from the scores rather than trusted, so a scraper that
mislabels a result cannot poison the training label.

---

## 🌍 What each source is for

The dump already covers the ten European leagues, so the APIs are aimed at exactly the two
things it cannot give you.

- **API-Football** — Betway Premiership history, and the live feed (`fixtures?live=all` is
  one request for every league on earth, which we filter to ours).
- **football-data.org** — the **7-day lookahead**. API-Football's free plan will not serve a
  future date; this one will, for 13 competitions, in one request per 10-day window.
- **TheSportsDB** — per-league upcoming fixtures **including the Betway Premiership's current
  season**, which the API-Football free plan refuses outright.
- **openfootball** — free gap-filler and cross-check. Not merged wholesale (see below).
- **betexplorer / globalsportsarchive** — scraped, SA league only. See above.

### What about The Odds API?
Checked, and it does not solve the SA gap: **67 soccer competitions, none South African**
(`soccer_africa_cup_of_nations` is the only African entry). It covers 10 of our 11 leagues,
but serves *upcoming* odds only — historical odds are a separate paid endpoint — so it cannot
backfill anything either way. `lib/odds.ts` in the web app already uses it for live market
blending, which is the job it is actually suited to.

### Free-plan boundaries, measured against the live accounts

These are enforced server-side and come back as `errors.plan`, not as HTTP errors:

- API-Football `fixtures?season=` works only for **2022–2024**; `fixtures?date=` only for
  **today−1 … today+1**; `next`/`last` are refused entirely; `live=all` **works**.
- TheSportsDB's free key truncates list responses to a handful of rows. Set
  `THESPORTSDB_KEY` to a Patreon key and the same code returns full seasons.
- openfootball has real data bugs: some COVID-restart fixtures in the 2019-20 files carry the
  season's *start* year (Barnsley v Nottingham Forest dated 2019-07-19, actually played
  2020-07-19), and its in-progress-season scores disagree with football-data.co.uk on dozens
  of matches. That is why it only ever runs in `--openfootball` (fill real gaps) or
  `--cross-check` (report differences, merge nothing).

---

## 🧱 How the pipeline works

1. **Discover new files.** `output/.manifest.json` maps filename → SHA-1, so re-runs only
   parse CSVs that are new or changed. `--full-rescan` ignores it.
2. **Parse, BOM-safe.** Some files carry a UTF-8 BOM (`ï»¿Div`); read with `utf-8-sig`, with a
   latin-1 fallback for older files.
3. **League + country from the `Div` value** via `leagues.py` — never from the filename.
4. **Season from the `Date` column.** The `(1)…(8)` suffixes are browser download numbering.
   Each football-data.co.uk file is exactly one season, so the season is resolved *per file* —
   which is what keeps the COVID-extended 2019/20 season (played into July 2020) from
   splitting its tail into 2020/21.
5. **Normalise** to the canonical schema; `FTR` H/D/A → outcome 1/2/3.
6. **Canonicalise team names** — `clean/team_aliases.json` first, then a data-driven pass that
   folds accents and club-form noise (`Málaga`=`Malaga`, `Bayer 04 Leverkusen`=`Bayer
   Leverkusen`, `ST Mirren`=`St Mirren`) and collapses every spelling of a club onto the one
   the data uses most. Without this, one club silently becomes two and its form features
   are wrong.
7. **Dedupe** on `(date, home_team, away_team)`, highest-trust source winning:
   `football-data.co.uk` → `API-Football` → `football-data.org` → `openfootball` →
   `TheSportsDB` → `FootyStats`.
8. **Write `output/model_data.csv`.**
9. **Diff against the previous file** — rows **added**, **unchanged**, and **conflicts** (same
   fixture, different score). Conflicts go to `output/conflicts.csv`.
10. **API top-up**, then **`output/fixtures.csv`** (live + upcoming only; finished matches
    belong in `model_data.csv`), then the **coverage report**.

### Output 1 — `output/model_data.csv`
```
date, season, league, country, home_team, away_team,
home_score, away_score, outcome, odds_home, odds_draw, odds_away,
home_shots, away_shots, home_shots_on_target, away_shots_on_target,
home_corners, away_corners, home_fouls, away_fouls,
home_yellow, away_yellow, home_red, away_red,
home_half_score, away_half_score, source
```
`outcome`: 1 = home win · 2 = draw · 3 = away win · `season`: e.g. `2021/22`

Match stats come from football-data.co.uk and cover **94%** of rows. The gaps are the
league-seasons no source carries them for — the whole SA league (API/scraped), Eredivisie and
Liga Portugal 2016/17 (football-data.co.uk publishes none for those two files), and the
openfootball gap-fills. The coverage report lists them every run.

> Note for whoever builds the features: every stat column above is measured *during* the
> match, so they are history, not pre-match knowledge.

### Output 2 — `output/fixtures.csv`
```
kickoff_utc, status, minute, league, country, season, home_team, away_team,
home_score, away_score, venue, source, source_match_id, fetched_at
```
Team names are aligned to `model_data.csv`'s spellings, so the web app can join tonight's
fixture to that club's history.

### The report also flags missing ODDS, not just missing rows
Bookmaker odds are a model feature, so a season with rows but no odds is a quiet hole — the
coverage table looks full while the model trains blind on it. Any league-season under 50% odds
coverage is listed with the football-data.co.uk file that would fix it. Currently: Premier
League 2018/19 and Bundesliga 2020/21 (both openfootball gap-fills, no odds), plus the whole
Betway Premiership, which no free source carries odds for.

> **`TARGET_SEASONS` in `leagues.py` must cover every season you hold.** The report can only
> flag a hole *inside* that window, so adding older files without extending it means the
> oldest seasons are never checked. A test asserts the two stay in step.

### Output 3 — the Turso `fixtures` table
Live and upcoming matches are published to Turso on every run that collects
fixtures. Configure with `TURSO_FIXTURES_URL` / `TURSO_FIXTURES_TOKEN` in `.env`
(the `libsql://` URL Turso shows you is accepted as-is; it falls back to the
`TURSO_MATCHES_*` names `web/lib/db.ts` already reads).

**Finished matches are never deleted.** The table is an append-and-advance log, because
the point is to come back later and ask whether the model called a match correctly:

- writes are UPSERTs on `match_key`, never insert-then-delete;
- each row carries a `progress` rank (`scheduled` 0 → `off` 1 → `in_play` 2 → `finished` 3)
  and the update only fires when the incoming row is **at least as advanced**, so a stale
  "scheduled" row from a slower source cannot overwrite a final score;
- `finished_at` is stamped once, on the first write reporting a result, then frozen;
- `first_seen_at` never moves, so you can see when a fixture first appeared.

`match_key` is exactly the key `model_data.csv` uses (`YYYY-MM-DD|Home|Away`), so scoring
predictions later is a plain equality join:

```sql
SELECT p.match_key, p.predicted_outcome, f.outcome AS actual,
       p.predicted_outcome = f.outcome AS correct
FROM predictions p
JOIN fixtures f USING (match_key)
WHERE f.status_group = 'finished';
```

Note the CSV and the table deliberately differ: `fixtures.csv` is the "what's on now" view and
drops finished matches (they are already in `model_data.csv`); Turso keeps everything.

### Output 4 — the coverage report
Per league: country, match count, season range; then the target window **2018/19 → 2026/27**
with every gap and thin season flagged. Gaps come with the fix, e.g. the exact
football-data.co.uk URL to drop into `unsorted/`. It also warns when the same season was
downloaded twice, which is normally why another one is missing.

---

## 🧪 Testing

```bash
python -m unittest discover -s tests -t .      # 40 tests, no extra dependencies
python -m pytest tests -q                      # or pytest, if you prefer
```

**The tests can never spend your API quota.** Two independent guards, enforced in the suite's
`setUpModule`, not by convention:

1. `WINSCOPE_OFFLINE=1` — every `BudgetedSession` raises `OfflineError` instead of issuing a
   request.
2. `socket.socket` is replaced with a stub that raises — so even code that somehow got past
   the first guard cannot open a connection.

The API layer is therefore tested against recorded payload shapes, and the rate limiting
against fakes: that the cap is 90 and not 100, that usage survives across runs, that a new day
resets it, that the provider's reported usage wins, and that the session refuses to spend
request 91.

To check the live APIs deliberately, run `python pipeline.py --with-api` — that is the only
thing in the repo that talks to them.

---

## 🍽️ Which file feeds what

This is the part that is easy to get wrong, so it is worth being blunt:

| File | Contains | Use it for |
|---|---|---|
| `output/model_data.csv` | 30.5k **played** matches with a known result | **Training.** This is the only training input. |
| `output/fixtures.csv` | Matches that have **not** finished — scheduled and in-play | **Prediction input.** The list of matches to predict. Never train on it. |
| Turso `fixtures` table | Both, and it keeps finished ones forever | Serving the web app + scoring predictions after the fact |

**Do not pass `fixtures.csv` to the trainer.** A scheduled match has no outcome to learn from,
and an in-play one has a *partial* score — training on a 60th-minute 1-0 teaches the model
that 1-0 is a final result. The label column would be empty or wrong either way.

The flow is: train on `model_data.csv` → predict on `fixtures.csv` → the result lands in Turso
when the match finishes → join on `match_key` to see whether the call was right → fold those
results back into the training set with `--from-turso` before the next retrain.

---

## 🔁 Keeping it fresh — and what it costs on GitHub Actions

`model_data.csv` is **static history**: commit it once. It only changes when you deliberately
refresh it before a retrain, so `.github/workflows/etl.yml` is split accordingly:

- **Cron, twice a day** (06:00 / 18:00 UTC) — `--live`: fetch new and in-play matches and
  upsert them into Turso. **Commits nothing.**
- **Manual only** (`workflow_dispatch` → `refresh-dataset`) — rescans `unsorted/`, tops up from
  the APIs, folds Turso's accumulated results back in with `--from-turso`, and commits
  `model_data.csv`. Run it when you are about to retrain.

That split is what keeps the repo small: a 3 MB CSV committed twice a day is what would
eventually hurt, and the daily matches are already in the database.

Repository secrets to add: `API_SPORTS_KEY`, `FOOTBALL_DATA_API_KEY`, `TURSO_FIXTURES_URL`,
`TURSO_FIXTURES_TOKEN` (and `THESPORTSDB_KEY` if you buy one).

> Because the cron no longer commits, **Turso is where new results accumulate**. That is what
> `--from-turso` is for — it reads back every finished match and merges it into
> `model_data.csv` using the same dedupe and conflict reporting as any other source.

### Will the free tier last?

Measured runtimes, cold cache, real API calls: tests **2s**, `--live` **75s**,
`--with-api` **117s**. With runner overhead that is ~2 min per live run and ~3 min per
weekly run:

| | Runs/month | Minutes |
|---|---|---|
| Cron → Turso (2/day) | 60 | ~120 |
| Manual dataset refresh | 1–2 | ~5 |
| **Total** | | **~125 min/month** |

GitHub's free allowance is **2,000 minutes/month on private repos and unlimited on public
ones**. So this uses **under 7%** of the tightest case — it will run for years, not months.
You could go hourly (~1,440 min/month) and still fit, though the API budget matters more than
the minutes at that rate.

Three things that actually bite, none of them minutes:

- **Repo size.** `model_data.csv` is 3 MB, which is why the cron commits nothing at all —
  see above. API cache and the quota counter use `actions/cache`, not commits.
- **Idle repos.** GitHub disables scheduled workflows after **60 days with no repository
  activity**. The weekly commit normally keeps it alive; if you go quiet for two months,
  re-enable it in the Actions tab.
- **Cron is best-effort.** Scheduled runs can be delayed or skipped at peak times. This
  pipeline is built to not care: football-data.org is queried over a **−7 to +7 day** window,
  so a missed run's final scores are picked up automatically by the next one, and the Turso
  upsert only ever moves a match forward.

Then train the model in Colab on `model_data.csv` (see `../model`) and drop the artifacts into
`../model/model/`.

---

## ⏱️ Everything is UTC

Every fixture timestamp from every source is UTC, and API-Football's date filter is UTC, so
the pipeline's idea of "today" is UTC too (`ingest/clock.py`).

This is not pedantry. It used the machine's **local** date, which in South Africa (UTC+2)
rolls over at 22:00 UTC — so the evening run asked for *tomorrow* and never fetched the day
still being played. Eleven matches sat frozen at their half-time score for hours, and the web
app rendered them as live. GitHub Actions runs in UTC, so CI would never have caught it.

Two related rules fall out of the same thinking:

- **Today's date query is cached for 5 minutes, not 3 hours.** It is a live scoreboard, not a
  fixture list; a long TTL kept serving half-time scores past full time.
- **The fixture window reaches back a day** (`yesterday … tomorrow`, the whole free-plan
  window). A match that kicks off at 19:45 and ends after the last run drops out of
  `live=all` the moment it finishes, and only shows its final score on its own date. Without
  the backward day, an evening fixture can stay stuck at half-time indefinitely.

Provisional scores never reach the training set regardless — `to_matches()` accepts only
`FT`/`AET`/`PEN`, so an in-play row is excluded by design.

---

## 📊 Is there enough data to train on?

**Yes for the ten European leagues; the South African league is usable but thin in one
specific way.** 38,105 matches, 2016/17 → 2026/27, 328 clubs, 96% carrying bookmaker odds,
class balance roughly home 43% / draw 25% / away 31%.

| League | Matches | Seasons |
|---|---|---|
| Championship | 5,564 | 2016/17–2026/27 |
| League One | 5,368 | 2016/17–2025/26 |
| Premier League | 3,820 | 2016/17–2026/27 |
| La Liga | 3,830 | 2016/17–2026/27 |
| Serie A | 3,821 | 2016/17–2026/27 |
| Ligue 1 | 3,496 | 2016/17–2026/27 |
| Liga Portugal | 3,093 | 2016/17–2026/27 |
| Bundesliga | 3,069 | 2016/17–2026/27 |
| Eredivisie | 3,022 | 2016/17–2026/27 |
| Scottish Premiership | 2,250 | 2016/17–2026/27 |
| **Betway Premiership** | **751** | **2019/20–2026/27** |

Two things matter more than the row count:

**1. Build ELO / form / H2H across ALL leagues, not per league.** Relegated and promoted clubs
move between leagues constantly — West Ham, Wolves, Coventry and Hull each have 300+ matches
in the dataset but only ~4 in the division they are in this season. Compute the stores per
league and every one of them resets to a default rating the moment it moves.

**2. The Betway Premiership has a season-shaped hole, and that is the real limitation.**
2022/23, 2023/24 and 2024/25 are complete (~240 matches each) — enough to learn from. But
2025/26 has only 5 matches, because API-Football's free plan stops at 2024 and TheSportsDB's
free key truncates season lists. So SA ratings jump from June 2025 straight to August 2026:
they are a full season out of date, across a transfer window and a promotion cycle.

Ways out, in order of cost:
- do nothing — the Turso feed rebuilds current SA form as 2026/27 plays out (roughly 10
  matchdays in, the ratings are live again);
- a TheSportsDB Patreon key — lifts the truncation and backfills 2018/19–2025/26 with no code
  change (just set `THESPORTSDB_KEY`);
- API-Football Pro — unlocks seasons past 2024 and the current one.

A handful of genuinely new clubs (Milford, Elversberg, NEC, Málaga, Le Mans, Académico de
Viseu…) have almost no history because they were just promoted. That is not a data problem —
no source can fix it — and it is what `INITIAL_ELO` / `FORM_DEFAULTS` exist for.

---

## ⚠️ Known gaps in the current dump

The report prints these every run:

- **Betway Premiership 2018/19** — no free source carries it. API-Football's free plan starts
  at 2022; TheSportsDB has the seasons but truncates them to ~5 matches each on the free key.
  A Patreon key or an API-Football upgrade closes this.
- **Betway Premiership 2019/20–2021/22 and 2025/26 are thin** (~5 matches each) for the same
  reason. 2022/23–2024/25 are complete (~246 each) from API-Football.
- **FootyStats files** are a different schema and are skipped unless you pass `--footystats`;
  football-data.co.uk already covers those fixtures with better odds.
