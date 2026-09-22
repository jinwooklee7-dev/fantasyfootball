-- ffdash schema. SQLite. All timestamps stored UTC (ISO8601 text).
-- Idempotent: safe to apply repeatedly.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- identity

CREATE TABLE IF NOT EXISTS team (
  team_abbr      TEXT PRIMARY KEY,          -- nflverse abbreviation, e.g. 'KC'
  full_name      TEXT NOT NULL,
  conference     TEXT,
  division       TEXT,
  nick           TEXT,                      -- 'Chiefs'
  team_color     TEXT,                      -- primary, '#E31837'
  team_color2    TEXT,                      -- secondary
  logo_url       TEXT                       -- upstream source; logos are cached
                                            -- locally by scripts/fetch_logos.py
);
-- NOTE: columns added after the first release are also applied by
-- db.ensure_columns() in init_db, since CREATE TABLE IF NOT EXISTS will not
-- alter a table that already exists.

CREATE TABLE IF NOT EXISTS player (
  player_id      INTEGER PRIMARY KEY,       -- OUR stable internal id
  display_name   TEXT NOT NULL,
  position       TEXT,
  current_team   TEXT REFERENCES team(team_abbr),
  updated_at     TEXT NOT NULL
);

-- Every external identifier maps here. Never join on name.
CREATE TABLE IF NOT EXISTS player_xref (
  player_id      INTEGER NOT NULL REFERENCES player(player_id),
  source         TEXT NOT NULL,             -- 'gsis','pfr','sleeper','espn','odds_api',...
  source_id      TEXT NOT NULL,
  PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_xref_player ON player_xref(player_id);

-- ---------------------------------------------------------------- games

CREATE TABLE IF NOT EXISTS stadium (
  stadium_id     TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  team_abbrs     TEXT NOT NULL,             -- comma separated; MetLife and SoFi host two
  latitude       REAL NOT NULL,
  longitude      REAL NOT NULL,
  roof           TEXT NOT NULL,             -- open | dome | retractable | fixed_open_sides
  timezone       TEXT NOT NULL,
  verified_at    TEXT                       -- null until a human has checked it
);

CREATE TABLE IF NOT EXISTS game (
  game_id        TEXT PRIMARY KEY,          -- nflverse game_id
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  season_type    TEXT NOT NULL,
  kickoff_utc    TEXT,
  home_team      TEXT NOT NULL REFERENCES team(team_abbr),
  away_team      TEXT NOT NULL REFERENCES team(team_abbr),
  stadium_id     TEXT REFERENCES stadium(stadium_id),
  roof_state     TEXT,                      -- actual state on gameday, if known
  home_score     INTEGER,
  away_score     INTEGER,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_game_week ON game(season, week);

-- ---------------------------------------------------------------- stats

CREATE TABLE IF NOT EXISTS player_week_stat (
  player_id      INTEGER NOT NULL REFERENCES player(player_id),
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  team_abbr      TEXT,
  opponent       TEXT,
  targets        INTEGER, receptions INTEGER, receiving_yards REAL, receiving_tds INTEGER,
  carries        INTEGER, rushing_yards REAL, rushing_tds INTEGER,
  attempts       INTEGER, completions INTEGER, passing_yards REAL, passing_tds INTEGER,
  interceptions  INTEGER,
  target_share   REAL, air_yards_share REAL,
  rz_touches     INTEGER, inside10_touches INTEGER,
  fantasy_ppr    REAL,
  corrected_at   TEXT,                      -- set by the Wed->Thu correction pull
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (player_id, season, week)
);

CREATE TABLE IF NOT EXISTS snap_count (
  player_id      INTEGER NOT NULL REFERENCES player(player_id),
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  offense_snaps  INTEGER, offense_pct REAL,
  routes_run     INTEGER, route_pct REAL,
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (player_id, season, week)
);

-- Event log, NOT a weekly snapshot (nflverse changed this in 2025).
CREATE TABLE IF NOT EXISTS depth_chart_event (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at    TEXT NOT NULL,             -- ISO8601 from nflverse
  team_abbr      TEXT NOT NULL REFERENCES team(team_abbr),
  player_id      INTEGER NOT NULL REFERENCES player(player_id),
  position       TEXT,
  depth_rank     INTEGER,
  UNIQUE (observed_at, team_abbr, player_id, position)
);

-- ---------------------------------------------------------------- injuries

CREATE TABLE IF NOT EXISTS injury_report (
  player_id      INTEGER NOT NULL REFERENCES player(player_id),
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  report_date    TEXT NOT NULL,             -- Wed/Thu/Fri practice report date
  practice_status TEXT,                     -- DNP | Limited | Full
  game_status    TEXT,                      -- Out | Doubtful | Questionable | null
  body_part      TEXT,
  is_inactive    INTEGER,                   -- set from the 90-minute inactives list
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (player_id, season, week, report_date)
);

-- ---------------------------------------------------------------- odds

CREATE TABLE IF NOT EXISTS odds_game (
  game_id        TEXT NOT NULL REFERENCES game(game_id),
  source         TEXT NOT NULL,             -- 'nflverse' (free) or a paid provider
  book           TEXT,
  captured_at    TEXT NOT NULL,
  spread_line    REAL,                      -- POSITIVE = home favored (nflverse convention,
                                            -- verified against historical results)
  total_line     REAL,
  home_implied   REAL,                      -- computed, not fetched
  away_implied   REAL,
  PRIMARY KEY (game_id, source, book, captured_at)
);

CREATE TABLE IF NOT EXISTS odds_prop (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id        TEXT NOT NULL REFERENCES game(game_id),
  player_id      INTEGER NOT NULL REFERENCES player(player_id),
  market         TEXT NOT NULL,             -- receiving_yards | anytime_td | ...
  book           TEXT NOT NULL,
  line           REAL,
  over_price     INTEGER, under_price INTEGER,
  captured_at    TEXT NOT NULL,
  UNIQUE (game_id, player_id, market, book, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_prop_player ON odds_prop(player_id, market);

-- ---------------------------------------------------------------- weather

CREATE TABLE IF NOT EXISTS weather_forecast (
  game_id        TEXT NOT NULL REFERENCES game(game_id),
  captured_at    TEXT NOT NULL,
  valid_for_utc  TEXT NOT NULL,             -- the kickoff hour
  applicable     INTEGER NOT NULL DEFAULT 1,-- 0 for dome / closed roof
  temp_f         REAL,
  wind_mph       REAL, wind_gust_mph REAL, wind_dir_deg REAL,
  precip_prob    REAL, precip_in REAL,
  conditions     TEXT,
  PRIMARY KEY (game_id, captured_at)
);

-- ---------------------------------------------------------------- social

CREATE TABLE IF NOT EXISTS social_account (
  did            TEXT PRIMARY KEY,          -- PERMANENT id; the handle can change
  handle         TEXT NOT NULL,
  display_name   TEXT,
  tier           TEXT,                      -- national | beat | aggregator | specialist
  team_abbr      TEXT REFERENCES team(team_abbr),
  domain_verified INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'candidate', -- candidate | active | rejected
  validated_at   TEXT,                      -- set only by human confirmation
  last_post_at   TEXT,
  posts_per_week REAL
);

CREATE TABLE IF NOT EXISTS social_post (
  uri            TEXT PRIMARY KEY,          -- at:// uri
  did            TEXT NOT NULL REFERENCES social_account(did),
  posted_at      TEXT NOT NULL,
  text           TEXT NOT NULL,             -- always keep the raw text
  ingested_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_post_time ON social_post(posted_at DESC);

CREATE TABLE IF NOT EXISTS social_tag (
  uri            TEXT NOT NULL REFERENCES social_post(uri),
  player_id      INTEGER REFERENCES player(player_id),
  team_abbr      TEXT REFERENCES team(team_abbr),
  category       TEXT,                      -- injury | usage | transaction | noise
  status         TEXT,                      -- limited | dnp | ruled_out | ...
  confidence     REAL,
  model          TEXT,
  tagged_at      TEXT NOT NULL,
  PRIMARY KEY (uri, player_id)
);

-- ---------------------------------------------------------------- ops

CREATE TABLE IF NOT EXISTS ingest_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  source         TEXT NOT NULL,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  rows_written   INTEGER,
  ok             INTEGER,
  note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_source ON ingest_log(source, started_at DESC);
