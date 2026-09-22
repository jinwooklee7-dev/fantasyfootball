# Build Spec

Phases are ordered by value-per-unit-effort. Phases 0–3 are entirely free data and
produce a genuinely useful tool on their own. Do not start Phase 4 until Phase 3 works.

---

## Phase 0 — Scaffold

- `uv init`, dependencies: `nflreadpy`, `httpx`, `fastapi`, `uvicorn`, `jinja2`,
  `polars`, `python-dotenv`.
- `db/schema.sql` applied by `scripts/init_db.py` (idempotent).
- `.env.example` with placeholder keys.
- `ingest_log` table working and written to by a trivial test ingest.

**Done when:** `uv run scripts/init_db.py` creates the DB and is safe to run twice.

---

## Phase 1 — nflverse ingest

One script per dataset in `scripts/ingest_*.py`, each callable standalone.

| Script | nflreadpy call | Cron |
|---|---|---|
| `ingest_schedules.py` | `load_schedules()` | every 15 min in season |
| `ingest_player_stats.py` | `load_player_stats()` | nightly + Wed→Thu correction pull |
| `ingest_snaps.py` | `load_snap_counts()` | 4x daily |
| `ingest_depth_charts.py` | `load_depth_charts()` | daily |
| `ingest_injuries.py` | `load_injuries()` | daily (after 7am UTC) |
| `ingest_players.py` | `load_players()`, `load_ff_playerids()` | weekly |
| `ingest_nextgen.py` | `load_nextgen_stats()` | nightly |

Notes:
- `load_schedules()` carries `spread_line` and `total_line` — that is our free game-odds
  source. Store them in `odds_game` with `source='nflverse'`.
- Build the player crosswalk (Phase 1 priority #1) before any stats ingest, so every
  stats row can resolve to an internal `player_id` at write time.
- The Wed→Thu re-pull is a separate cron entry, not a flag. Make it obvious.

**Done when:** a SQL query returns the last 6 weeks of target share and snap % for any
named player, joined correctly across stats, snaps, and roster.

---

## Phase 2 — Weather

- Source: Open-Meteo forecast API (free, no key). Fall back to NWS if it misbehaves.
- Seed `data/stadiums.csv` into a `stadium` table. **Verify the coordinates and roof
  types before the season** — the file ships with best-effort values and several venues
  are in flux (see comments in the file).
- For each upcoming game, fetch the hourly forecast for the stadium's coordinates at the
  kickoff hour. Store temp, wind speed, wind gust, wind direction, precipitation
  probability, precipitation amount.
- If `roof` is `dome` or `closed`, skip the fetch and store a null weather row with a
  `not_applicable` flag. Retractable roofs are unknown until gameday — flag them
  `retractable_unknown` and show the outdoor forecast with a caveat.
- Refresh daily, then hourly inside 24 hours of kickoff.

**Done when:** the game page shows "12 mph crosswind, open air" or "Roof closed — no
weather impact", never a generic five-day forecast widget.

---

## Phase 3 — The game page (the actual product)

FastAPI + Jinja + HTMX. One route: `/game/{game_id}`. Panels, in priority order:

1. **Line block** — spread, total, and both **implied team totals** computed from them.
2. **Weather block** — suppressed for domes, wind-first for outdoor.
3. **Injury block** — official practice participation (W/Th/F), game designation,
   and inactive status once published. Grouped by team, skill positions first.
4. **Usage block** — for each relevant skill player: trailing 4–6 week snap %,
   target share / carry share, red zone touches, route participation. Sparkline per
   metric beats a table of numbers.
5. **Matchup block** — opposing defense's trailing 4–6 week performance vs that
   position. Career H2H goes here, small, below the fold.

Every panel shows its own "last updated" timestamp. Stale data renders; it does not blank.

**Done when:** you can make a real start/sit decision from this page without opening
another tab.

---

## Phase 4 — Bluesky ingest

1. **Validate accounts first.** Run `scripts/validate_bluesky.py data/bluesky_accounts.csv`.
   It resolves handles to DIDs and reports last-post date and posting frequency. The
   user manually confirms each account before it is marked `active` in `social_account`.
   Nothing is ingested from an unvalidated account.
2. **Consume Jetstream**, filtered to the validated DID list. Persist raw posts to
   `social_post` — always store the raw text, never only the parsed version.
3. **Tag with an LLM.** For each post, extract: mentioned player(s) → internal
   `player_id`, team, category (`injury` | `usage` | `transaction` | `noise`), and
   status if present (`limited` | `dnp` | `full` | `ruled_out` | `questionable` |
   `expected_to_play` | `activated`). Write to `social_tag`. Keep the model output
   alongside a confidence score; low confidence still surfaces, flagged.
4. **Surface on the game page** as a per-player feed: only posts tagged to a player on
   this slate, newest first, with the source handle visible so the user can judge it.

Expect national insiders to be thin on Bluesky. The aggregator accounts (which echo X
news within minutes) carry more of the load than the insiders themselves. That tradeoff
is accepted — see the account file.

**Done when:** "3 new injury updates for players in this game" is accurate and clickable.

---

## Phase 5 — Player props (optional, costs money)

- Provider options: The Odds API, OpticOdds, SportsDataIO. Game lines are cheap or free;
  **player props are the paid tier** — price this before committing.
- Store in `odds_prop` keyed on internal `player_id` + market + book + captured_at.
  Keep the full history: line movement is signal, the closing number is not the whole story.
- Markets to cover: receiving yards, receptions, rushing yards, passing yards, passing
  TDs, anytime TD.
- **Do not scrape sportsbooks.** If the budget isn't there, skip this phase — Phases 1–3
  already carry most of the decision value.

**Done when:** the usage block shows each player's prop line next to his trailing
average for the same stat, so the gap is visible at a glance.

---

## Phase 6 — Start/sit view

Cross-game view: given a roster (manual list or a league import), rank the start/sit
decisions by how much the available signals disagree with consensus. Only worth building
once Phases 1–4 have been trusted for a few weeks.

---

## Explicitly out of scope

- Multi-user support, auth, hosting for other people. This is a personal tool; adding
  users changes the odds-data licensing situation entirely.
- Projections modelling. Surface the inputs well; don't build a projection engine until
  the inputs are trustworthy.
- Any sport other than NFL.
