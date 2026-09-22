# ffdash

One page per NFL game: betting line, implied team totals, wind, injuries, usage
trends and matchup — enough to make a start/sit call without opening another tab.

Personal tool. Phases 0–3 of `SPEC.md` are built and run on free data only: no
API keys, no paid tier, nothing scraped.

## Quick start

```bash
uv sync
uv run scripts/refresh_all.py
uv run uvicorn web.app:app --port 8077
```

Then open <http://127.0.0.1:8077>. Pick a week, pick a game.

The first `refresh_all` pulls a full season of play-by-play, the whole player
crosswalk and the team logos, so give it a few minutes. Afterwards everything is
cached and incremental.

### Nothing updates itself until you schedule it

`refresh_all.py` is a one-shot. Until you install `deploy/crontab.example`, the
database is a snapshot from whenever you last ran an ingest — so a game that
finished last night will still show no score, and the line will be yesterday's.

The footer on every page lists each ingest and its age, and turns the overdue
ones red with a note. If a number looks wrong, check there first: stale is far
more likely than broken.

```bash
uv run scripts/refresh_all.py     # catch everything up right now
```

## What's on the page

`/game/{game_id}` — panels in priority order, each stamped with its own
"last updated":

1. **Line** — spread, total, and both **implied team totals**, computed not
   fetched. The best free start/sit signal there is. Line movement is kept.
2. **Weather** — wind speed and direction first, coloured at the ~15 mph
   threshold where passing and kicking measurably suffer. Suppressed entirely
   for domes and closed roofs.
3. **Injuries and availability** — practice status and game designation, grouped
   by team, skill positions first, with a dot per snapshot so Wed→Thu→Fri reads
   as a trend. **IR, inactive and OUT are flagged in red beside the player's
   name everywhere he appears**, including in the usage tables. A separate block
   lists players who cannot play but are absent from the injury report — almost
   always IR, which the feed drops rather than flags.
4. **Usage** — trailing 4–6 weeks, split into three tables per team because the
   positions are not judged on the same things:
   - **Quarterbacks** — attempts, completion %, passing yards, TDs, INTs, plus
     what they add with their legs. No target share: they do not run routes.
   - **Backfield** — carries, rushing yards, receiving work, and touches inside
     the 20 and the 10.
   - **Receivers** — target share and air-yards share first, then volume.

   Snap %, the lead metric and fantasy points each carry a sparkline. Usage
   predicts fantasy output; production does not.

   **Scoring format** switches between full PPR, half PPR and standard from the
   top bar. It is instant and remembered — every cell carries all three.
5. **Matchup** — what the opposing defence has allowed by position over the same
   trailing window, ranked against the league. Career head-to-head is below the
   fold on purpose.

Other routes: `/` (the week's slate), `/player/{id}` (full trailing log),
`/health` (ingest freshness as JSON).

## Commands

```bash
uv run scripts/init_db.py                          # create/seed; safe to re-run
uv run scripts/fetch_logos.py                      # cache team logos locally, once
uv run scripts/refresh_all.py                      # every ingest, in order
uv run scripts/render_static.py                    # build the shareable site
uv run scripts/player_usage.py "Bijan Robinson"    # trailing usage in the terminal
uv run pytest                                      # logic, DB and link checks
```

Individual ingests live in `scripts/ingest_*.py` and each runs standalone with
an optional `--season`. Every one is idempotent and writes a row to
`ingest_log`.

## Scheduling

`deploy/crontab.example` has the full schedule. Two entries matter more than the
rest:

- **Lines every 15 minutes** in season. A row is only written when the number
  actually moves, so the odds history stays readable.
- **Stat corrections, Wednesday→Thursday overnight**, as its own entry with
  `--corrections`. The NFL issues corrections after games are scored. Do not fold
  this into the nightly run and do not remove it.

## Sharing it

The dashboard is read-only, so it publishes as plain HTML — no server to run,
secure or pay for.

```bash
uv run scripts/render_static.py          # -> dist/, ~914 pages, ~13 MB
```

Point Cloudflare Pages or GitHub Pages at `dist/`. `.github/workflows/publish.yml`
does the whole thing on a schedule: refresh the data, run the tests, render, deploy.

Two things to get right:

- **Base path.** A GitHub Pages *project* site is served from
  `https://<you>.github.io/<repo>/`, so pass `--base /<repo>/`. Get it wrong and
  every link on every page breaks, so the renderer refuses a base that is not a
  root-relative path.
- **Actions minutes** are free on a public repo. On a private one the allowance is
  2,000 minutes a month and this workflow runs 2–4 minutes a go, so thin the
  schedule out.

### Before you share the link

- The data is **CC-BY** from nflverse and Open-Meteo. Attribution is in the footer
  of every page and required — do not remove it. Open-Meteo's free tier is
  **non-commercial only**.
- The NFL team logos are **club trademarks**. Fine on a tool only you use; for
  anything genuinely public set `FFDASH_PUBLIC=1`, which swaps them for coloured
  initials badges and drops the files from the build.
- `robots.txt` asks crawlers to stay out by default. That is a request, not a wall —
  a Pages site is readable by anyone with the URL. Pass `--allow-index` to opt in.
- `SPEC.md` rules out multi-user hosting on odds-licensing grounds. That concern is
  about the Phase 5 paid props feed, which is not built; Phases 0–3 are CC-BY
  throughout.

## Layout

```
db/schema.sql          SQLite schema, applied idempotently
data/stadiums.csv      coordinates + roof types, keyed on the nflverse stadium_id
data/bluesky_accounts.csv   Phase 4 candidates — unvalidated, nothing auto-follows
web/static/logos/      team logos, cached locally so a dead CDN cannot blank the page
deploy/, .github/      cron entries and the publish workflow (use one, not both)
src/ffdash/            db, crosswalk, queries, odds math, weather, nflverse retry
scripts/               one ingest per dataset, plus CLI tools
web/                   FastAPI + Jinja + HTMX. No SPA, no build step.
tests/                 the logic that fails silently, and DB integrity
```

## Things worth knowing before you change anything

`CLAUDE.md` holds the constraints in full. The three that bite hardest:

- **Never join on player name.** There are two Josh Allens. Everything joins on
  our internal `player_id`; external ids map to it in `player_xref`. Name lookup
  happens in exactly one function, at the edge where a human typed one.
- **`spread_line` is positive when the home team is favoured.** Verified against
  historical results, not documentation, and pinned by a test — getting it
  backwards inverts every implied total and looks perfectly normal on screen.
- **Red zone "—" means no data, not zero.** Current-season play-by-play arrives
  with broken team-weeks (ATL, 2026 Weeks 1–2: 161 plays, never inside the 15).
  Those are stored NULL rather than 0 so the page cannot mislead you.

## Not built

- **Phase 4, Bluesky.** The account list and validator are here, but nothing is
  ingested. Every account needs manual confirmation first — a typosquatted
  Schefter account exists, and there is real money attached.
- **Phase 5, player props.** Paid tier. Sportsbooks are not scraped.
- **Phase 6, the cross-game start/sit view.** The spec says to trust Phases 1–4
  for a few weeks first.

Also absent by upstream limitation, not choice: route participation, which is not
available free, so `snap_count.route_pct` stays NULL.

Custom league scoring beyond PPR / half / standard is not supported — six-point
passing touchdowns, TE premium and yardage bonuses need the raw components and a
real scoring engine.
