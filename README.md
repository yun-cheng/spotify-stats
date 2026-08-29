# spotify-stats

Tracks how many times you've played each song, and keeps a local history you own.

## Why this exists

**Spotify's API does not expose play counts.** No endpoint reports "you played
this 47 times". Counts have to be derived from individual play events, which
come from two places:

| Source | Covers | Notes |
|---|---|---|
| `recently-played` API | forward, from today | last 50 plays only — must be polled |
| Extended streaming history export | your entire account | request once, ~30 day wait |
| `top/tracks` API | 4wk / 6mo / ~1yr | ranked only, **no counts** — unused here |

The poller handles the future; the export handles the past. Both land in one
`plays` table.

## Setup

Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
with redirect URI `http://127.0.0.1:8888/callback`, then:

```bash
mkdir -p var
cp config.example.json var/config.json   # paste your client id in
python3 collector/auth.py                # one-time authorization, opens a browser
python3 collector/poll.py                # first fetch
```

Genres additionally need a free Last.fm API key from
[last.fm/api/account/create](https://www.last.fm/api/account/create), added to
`var/config.json` as `lastfm_api_key`.

The collector has no dependencies — Python 3 standard library only. Only the
dashboard needs anything installed.

Then install the scheduler so it collects on its own:

```bash
./scripts/install_agent.sh
```

## Layout

```
collector/            data collection — everything that writes to the database
  core.py             shared paths, DB connection, rate-limit-aware API caller
  auth.py             OAuth PKCE flow and token refresh
  poll.py             fetch recent plays — the scheduled job
  enrich.py           backfill track metadata the poller didn't capture
  import_history.py   load a Spotify data export
  backfill_artists.py one-off: add credits for tracks stored pre-multi-artist
  fetch_genres.py     attach genre tags to artists via Last.fm
  schema.sql          database definition
web/
  app.py              Streamlit dashboard — a consumer; asks the collector to
                      fetch rather than writing to the database itself
scripts/
  install_agent.sh    generate and load the launchd polling agent
requirements.txt      dashboard dependencies (the collector needs none)
config.example.json   template — copy to var/config.json
var/                  all runtime state — gitignored in full
  spotify.db          the database
  config.json         your client id
  tokens.json         OAuth tokens
  logs/               poller output
```

Nothing at the top level is generated: every mutable file lives under `var/`,
which keeps `.gitignore` to a single entry and makes "what is mine vs. what is
the project's" obvious at a glance.

Collection and presentation are kept separate: `collector/` only ever writes
to the database, and anything that reads it is a consumer. A dashboard is the
obvious next consumer, and it should not need to know how data arrives.

## Viewing your stats

The dashboard is the only part with dependencies, so it lives in a venv:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run web/app.py
```

The sidebar shows the newest play on record and a **Fetch new plays** button
that runs the collector on demand — useful because the scheduled poll only
runs every 30 minutes.

Filter by date range, switch between counting every credited artist and
primary credits only, and see top tracks, artists, genres, plays per day, a
day-by-hour listening heatmap, a searchable play-by-play history with CSV
export, and collector health.

## Importing an export

Request it at [spotify.com/account/privacy](https://www.spotify.com/account/privacy/),
check **Extended streaming history**, then confirm via the email they send —
*nothing is prepared until you click that link.* When the zip arrives:

```bash
python3 collector/import_history.py ~/Downloads/my_spotify_data.zip
```

Safe to re-run; duplicate plays are ignored.

## Design notes

Things learned the hard way, kept here so they aren't rediscovered:

- **Dedupe key is `(track_uri, played_at)`.** Spotify timestamps are
  millisecond-exact, so overlapping fetches are no-ops. This is what makes the
  poller safe to re-run, overlap, and backfill.
- **The batch `/v1/tracks?ids=` endpoint returns 403** for development-mode
  apps. Single `/v1/tracks/{id}` works. But `recently-played` already includes
  full album/artist objects, so `poll.py` stores metadata inline for free and
  `enrich.py` is only a backfill.
- **Refresh tokens expire after 180 days.** `auth.py` exits loudly rather
  than failing silently — a silent death here costs unrecoverable history.
  Re-run `python3 collector/auth.py` when it happens.
- **The 50-item buffer is the real constraint.** Nothing may go unpolled for
  more than ~50 tracks of *listening* or plays are lost permanently. Polling is
  every 30 min via launchd, which fires missed runs on wake, so a closed laptop
  is fine.
- **Two export formats, not equally trustworthy.** The extended export has
  track ids and second-precision timestamps and merges cleanly. The 5-day
  "Account data" export has neither — minute-precision *local* timestamps and
  no ids — so its rows are tagged `export_basic` and should be excluded from
  counts once the extended export lands.
- **A track credits several artists, and a play counts toward all of them.**
  Storing only the primary artist undercounted the top artist here by 46%,
  and made feature-only artists invisible. Credits live in `track_artists`
  with `position` (0 = primary), so both readings stay available.
- **Spotify withholds genres from development-mode apps.** The artist object
  returns only name/id/images/uri — no `genres`, `popularity`, or `followers`.
  Genres come from Last.fm's crowd-sourced tags instead, recorded with
  `artists.genre_source` so their provenance stays visible.
- **Last.fm tags are noisy.** They mix genres with places, moods, labels and
  personal bookmarks. `fetch_genres.py` normalises case and punctuation (so
  "Hip-Hop" and "hip hop" merge), drops tags scoring under 15, and filters a
  stoplist. Some junk still survives — "smoothly sexy sounding" scored 17 —
  so treat genres as indicative, not authoritative.
- **API rows carry no `ms_played`**; the endpoint doesn't report it. A
  colliding export row backfills the duration instead of losing it.
- **Plays under ~30s never reach the API at all**, so skips are invisible in
  `api` rows but present in export rows. The two sources will never agree
  exactly, by design.

## Useful queries

```sql
-- most played tracks
SELECT t.name, t.artist_name, COUNT(*) n FROM plays p
JOIN tracks t USING (track_uri) WHERE p.source IN ('api','export')
GROUP BY p.track_uri ORDER BY n DESC LIMIT 20;

-- listening by genre
WITH g AS (SELECT a.artist_uri, j.value AS genre FROM artists a, json_each(a.genres) j)
SELECT g.genre, COUNT(*) plays FROM plays p
JOIN track_artists ta USING (track_uri) JOIN g USING (artist_uri)
GROUP BY g.genre ORDER BY plays DESC LIMIT 20;

-- most played artists, counting features
SELECT a.name, COUNT(*) n FROM plays p
JOIN track_artists ta USING (track_uri) JOIN artists a USING (artist_uri)
WHERE p.source IN ('api','export') GROUP BY a.artist_uri ORDER BY n DESC LIMIT 20;

-- is the collector alive?
SELECT started_at, fetched, inserted, ok, error FROM runs
ORDER BY started_at DESC LIMIT 10;
```

`SPOTIFY_STATS_DB` overrides the database path — use it to rehearse an import
against a scratch copy before touching real data.
