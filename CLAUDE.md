# CLAUDE.md

Context for working in this repo. Read this before writing code.

## What this is

A single-page-per-game dashboard for NFL fantasy start/sit and betting decisions.
Pull up one game, see everything that matters: weather, betting lines, usage trends,
injury status, and beat-reporter chatter.

Personal tool. Not a product. Prefer boring, debuggable code over abstraction.

## Stack

- Python 3.11+, managed with `uv`
- SQLite (single file at `db/ffdash.db`) — do not reach for Postgres until there's a reason
- `nflreadpy` for all NFL data (returns **Polars**, not pandas)
- `httpx` for HTTP, `FastAPI` + Jinja2 + HTMX for the UI (no SPA, no build step)
- Scheduling: plain `cron` entries calling `scripts/*.py`. No Celery, no Airflow.

## Hard-won constraints — do not rediscover these

### nflverse

- **Use `nflreadpy`, NOT `nfl_data_py`.** The latter is deprecated with no further
  maintenance planned. `nflreadpy` returns Polars frames; call `.to_pandas()` only
  at the edge if a library demands it.
- **Stat corrections land midweek.** The NFL issues corrections after the games are
  scored. nflverse docs explicitly recommend re-pulling during the Wednesday→Thursday
  overnight window. There is a scheduled job for this. Do not remove it. If our Week N
  numbers disagree with everyone else's, this is why.
- **Depth charts changed schema in 2025.** From 2025 onward they are no longer assigned
  to a week. Each update is appended with an ISO8601 timestamp. Treat the table as an
  event log, not a weekly snapshot — this is a feature, it lets us see the exact day a
  backup moved up.
- **FTN charting data is CC-BY-SA 4.0** and requires attribution to FTN via nflverse.
  The rest of nflverse is CC-BY 4.0. If FTN data appears in the UI, the attribution
  string must appear too.
- Play-by-play goes back to 1999. Current season is included and updates all year —
  this is a live pipeline, not a static archive.

#### Verified while building Phases 0–3 — checked against the data, not the docs

- **`spread_line` is POSITIVE when the HOME team is favoured.** An earlier comment in
  `schema.sql` claimed the opposite. Confirmed against `2020_01_HOU_KC`: KC favoured
  at home by 9.5, `spread_line = 9.5`, KC won by 14; and across 2020+, `spread_line`
  correlates +0.45 with `result` (home margin). So
  `home_implied = total/2 + spread/2`, `away_implied = total/2 - spread/2`.
  Getting this backwards inverts the most important number on the page and looks
  completely normal, so `tests/test_logic.py` pins it.
- **`stadium_id` in `load_schedules()` is nflverse's own key** (`ATL97`, `BAL00`,
  `NYC01`), not a readable slug. The original `data/stadiums.csv` used its own slugs
  and joined to nothing. The file is now keyed on the nflverse id, with the old slug
  kept in the notes column. International venues (`LON00`, `LON02`, `MEX00`, `MAD01`,
  `MUN01`/`GER00`, `PAR00`, `RIO00`, `SAO00`, `MEL00`) were missing entirely.
- **Snap counts key on `pfr_player_id`, not gsis.** They must go through the
  crosswalk. Roughly 4 players a week fail to resolve — new signings not yet in
  `load_players()`. `load_snap_counts()` carries **no route data**, so
  `snap_count.routes_run` / `route_pct` stay NULL; routes are not available free.
- **`load_injuries()` has no report date.** It is one row per player-week carrying the
  latest practice status, not a Wed/Thu/Fri series. We store our own observation date
  as `report_date`, so running the ingest daily builds the progression up over the
  week.
- **The injury feed does not carry IR, and a player placed on IR disappears from it
  entirely** — which renders identically to "healthy, nothing to report". This is the
  worst failure mode the page had: a startable back on injured reserve sat in the
  usage table with normal trailing numbers and no flag at all.
  **`load_rosters_weekly()` is the source for availability**, with a per-week status:
  `ACT`, `RES` (injured reserve), `INA` (inactive), `DEV` (practice squad), `PUP`,
  `SUS`, `CUT`, `RET`, `EXE`. In 2026 Week 3 that is 253 players on RES, 79 of them
  skill positions.
- **`INA` is the gameday inactives list**, and an earlier note here claiming inactives
  were unavailable was wrong. It is populated only for weeks that have been played —
  Weeks 1 and 2 of 2026 carry ~200 each, Week 3 (unplayed) carries none — which is the
  signature of the 90-minute list. `ingest_rosters.py` uses it to fill
  `injury_report.is_inactive`.
- **Practice statuses arrive spelled out** ("Did Not Participate In Practice"), and
  are normalised to DNP / Limited / Full at ingest.
- **The nflverse `roof` field is too coarse to drive weather suppression.** It reports
  SoFi as `closed`, but SoFi's roof is fixed with **open sides** — wind reaches the
  field. Venue structure from `stadiums.csv` decides; the per-game field only decides
  for genuinely retractable roofs, where it is a real gameday announcement.
- **Current-season play-by-play arrives with broken team-weeks.** In 2026 Weeks 1–2,
  ATL shows 161 plays but a minimum `yardline_100` of 15 — no red zone data at all,
  while every other team reaches the 1–4. Red zone touches are therefore written as
  NULL for untrusted team-weeks and 0 only where the feed demonstrably works, so the
  page can distinguish "no red zone usage" from "no data". The coverage test counts
  **scrimmage** plays inside the 20: ATL Week 1 has exactly one play inside the 20 and
  it is an extra point, which a naive check waves through.
- **GitHub release assets return bursts of 504s** lasting up to a minute. Every loader
  goes through `ffdash.nflsource.fetch`, which retries with backoff. An unguarded call
  will fail a cron run for no real reason.

### Scoring

- `fantasy_points_ppr` is what nflverse computes and what we store. Half PPR and
  standard differ from it by **exactly the reception bonus**, so they are derived by
  subtraction (`ppr - 0.5 * receptions`, `ppr - receptions`) rather than by
  reimplementing the scoring table. Recomputing from components would mean restating
  every rule — two-point conversions, return touchdowns, fumble recoveries — and
  quietly disagreeing with every other site by a point or two.
- A player whose reception count is missing returns **None** for the non-PPR formats
  rather than the unconverted PPR figure, which would overstate them.
- Anything genuinely different (six-point passing TDs, TE premium, yardage bonuses)
  does need the components and is not supported.

### Publishing

- The site is read-only, so it is published as **static HTML** (`render_static.py`),
  not a hosted server. ~914 pages, ~13 MB.
- Links go through `ffdash.links`, because the live app and the generated site need
  different URLs for the same page. A wrong `--base` produces a complete site where
  every link is broken, so it is validated before anything is written.
- The scoring switch is **client side**. A query parameter cannot drive it on a static
  site, and rendering three copies of 900 pages to work around that would be absurd,
  so every points cell carries all three formats as data attributes.
- `tests/test_static_build.py` checks every internal link. It has already caught one
  real regression (the IR block linking to player pages the renderer never built).

### Player identity

- **Player IDs do not match across sources.** This will eat a day if it isn't handled
  up front. Use `nflreadpy.load_ff_playerids()` as the crosswalk and store our own
  stable internal `player_id`, mapping every external ID to it at ingest time.
- Never join on player name. Ever. Names collide, change, and are formatted
  differently by every source.

### Bluesky

- **Store DIDs, not handles.** Handles are renameable and squattable. The DID is the
  permanent identifier. Resolve handle → DID once at setup, then key everything on DID.
- **Impersonation is a live risk with real money attached.** A typosquatted Schefter
  account exists (`adanschefter.bsky.social`, note the "n") carrying an identical bio.
  There are also unofficial mirror bots and parody accounts. Every account in
  `data/bluesky_accounts.csv` must be manually validated by the user before its DID is
  written to the `social_account` table. Nothing auto-follows.
- Prefer domain-verified handles (e.g. `johnkaralis.com`) over `*.bsky.social` —
  they require proving control of a real domain and are much harder to fake.
- Use Jetstream (the filtered firehose) rather than polling each account. Free, pushed,
  and simpler than a polling loop.

### Things we deliberately do NOT do

- **No scraping of X/Twitter.** Against their terms, actively blocked, breaks at the
  worst possible moment (Sunday morning).
- **No scraping of sportsbooks.** Same reasoning. Player props come from a paid API or
  not at all.
- No career head-to-head "player vs this defense" stats as a headline metric. Rosters
  and schemes turn over; it is close to noise. Usage share predicts fantasy output far
  better. H2H can exist in the UI, ranked below usage.

## Domain logic that matters

- **Implied team total** = the single best free signal for start/sit. From the game
  line: `implied = total/2 - spread/2` for the favorite's opponent, `total/2 + spread/2`
  for the favorite. A team favored by 7 in a 48-point game is implied for ~27.5.
  Compute and surface this prominently; it costs nothing.
  In nflverse terms, with `spread_line` positive when the home team is favoured:
  `home = total/2 + spread/2`, `away = total/2 - spread/2`. See the verified note above.
- **Weather: wind is the variable that matters.** Sustained wind above ~15 mph
  measurably suppresses passing and kicking. Cold and light rain matter less than
  people assume. Show wind speed and direction first; suppress the whole weather panel
  for domes and closed roofs rather than showing a meaningless forecast.
- **Usage > production** for prediction: target share, snap %, route participation,
  red zone looks, carries inside the 10. Trailing 4–6 weeks, not season-to-date.
- **Opponent strength** should also be trailing 4–6 weeks by position, not full season.

## Time handling

- Store every timestamp as UTC in the DB. Convert to America/New_York at render only.
- The decision deadlines that matter: Wed/Thu/Fri practice reports, Friday final game
  designations, and **inactives at 90 minutes before kickoff**. The Sunday 11:30am ET
  window is the one moment where latency actually matters.

## Conventions

- Every ingest script is idempotent and safe to re-run. Upsert, never blind insert.
- Every ingest writes a row to `ingest_log` (source, rows, started_at, finished_at, ok).
- Secrets in `.env`, never committed. `.env.example` lists required keys.
- If an external API is down, the page must still render with stale data and a visible
  "last updated" timestamp. Never blank the page on a fetch failure.
