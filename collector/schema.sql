PRAGMA journal_mode = WAL;

-- One row per play event. The (track_uri, played_at) pair is the dedupe key:
-- Spotify's played_at is exact to the millisecond, so re-fetching an overlapping
-- window is a no-op with INSERT OR IGNORE. This is what makes the poller safe to
-- re-run, overlap, or backfill without double-counting.
CREATE TABLE IF NOT EXISTS plays (
  track_uri   TEXT NOT NULL,
  played_at   TEXT NOT NULL,          -- ISO8601 UTC
  ms_played   INTEGER,                -- NULL from API (not reported); filled from export
  source      TEXT NOT NULL,          -- 'api' | 'export'
  context_uri TEXT,                   -- playlist/album/artist the play came from
  PRIMARY KEY (track_uri, played_at)
);

CREATE INDEX IF NOT EXISTS idx_plays_played_at ON plays(played_at);
CREATE INDEX IF NOT EXISTS idx_plays_track     ON plays(track_uri);

-- Track metadata, fetched once per track and cached forever.
CREATE TABLE IF NOT EXISTS tracks (
  track_uri   TEXT PRIMARY KEY,
  name        TEXT,
  artist_name TEXT,
  artist_uri  TEXT,
  album_name  TEXT,
  album_uri   TEXT,
  duration_ms INTEGER,
  popularity  INTEGER,
  enriched_at TEXT
);

-- Health log. Answers "did my collector quietly die three weeks ago?"
CREATE TABLE IF NOT EXISTS runs (
  started_at       TEXT PRIMARY KEY,
  fetched          INTEGER,
  inserted         INTEGER,
  newest_played_at TEXT,
  ok               INTEGER NOT NULL,
  error            TEXT
);

-- Artists are modelled separately because a track can credit several, and a
-- play should count toward every one of them. Storing only the first artist
-- silently undercounts anyone who mostly appears as a feature.
CREATE TABLE IF NOT EXISTS artists (
  artist_uri   TEXT PRIMARY KEY,
  name         TEXT,
  genres       TEXT,   -- JSON array; Spotify withholds these, filled externally
  genre_source TEXT,   -- which provider supplied the genres
  enriched_at  TEXT
);

CREATE TABLE IF NOT EXISTS track_artists (
  track_uri  TEXT NOT NULL,
  artist_uri TEXT NOT NULL,
  position   INTEGER NOT NULL,   -- 0 = primary credit, >0 = featured
  PRIMARY KEY (track_uri, artist_uri)
);

CREATE INDEX IF NOT EXISTS idx_track_artists_artist ON track_artists(artist_uri);
