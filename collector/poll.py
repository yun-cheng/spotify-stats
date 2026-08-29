"""Fetch recently-played tracks and append new plays. Idempotent; safe to re-run."""
import datetime as dt, sys
import auth
from core import api, connect


def cursor_after(con):
    """Resume from the newest play we already have, minus a small overlap.

    The overlap costs nothing (dupes are ignored) and protects against
    boundary races where a play lands on the same millisecond as our cursor.
    """
    row = con.execute("SELECT MAX(played_at) FROM plays WHERE source='api'").fetchone()
    if not row or not row[0]:
        return None
    ts = dt.datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    return int((ts - dt.timedelta(minutes=1)).timestamp() * 1000)


def poll():
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    con = connect()
    fetched = inserted = 0
    newest = None
    try:
        token = auth.access_token()
        after = cursor_after(con)
        path = "/me/player/recently-played?limit=50"
        if after:
            path += f"&after={after}"

        items = api(path, token).get("items", [])
        fetched = len(items)
        rows, meta, credits, people = [], [], [], []
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for it in items:
            tr = it.get("track") or {}
            uri = tr.get("uri")
            if not uri:
                continue  # local files and unavailable tracks have no URI
            ctx = it.get("context") or {}
            rows.append((uri, it["played_at"], None, "api", ctx.get("uri")))
            newest = max(newest or "", it["played_at"])
            # The recently-played payload already carries full album/artist
            # objects, so metadata is free here. The batch /v1/tracks endpoint
            # is 403 for development-mode apps, making this the cheap path.
            artists = tr.get("artists") or [{}]
            album = tr.get("album") or {}
            meta.append((uri, tr.get("name"), artists[0].get("name"),
                         artists[0].get("uri"), album.get("name"), album.get("uri"),
                         tr.get("duration_ms"), tr.get("popularity"), now,
                         album.get("release_date"), album.get("album_type")))
            # Every credited artist, not just the first: a play counts toward
            # all of them, and features are a large share of listening.
            for pos, a in enumerate(artists):
                if a.get("uri"):
                    credits.append((uri, a["uri"], pos))
                    people.append((a["uri"], a.get("name")))

        con.executemany(
            "INSERT OR REPLACE INTO tracks (track_uri, name, artist_name,"
            " artist_uri, album_name, album_uri, duration_ms, popularity,"
            " enriched_at, album_release_date, album_type)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", meta)
        con.executemany(
            "INSERT OR IGNORE INTO track_artists (track_uri, artist_uri, position)"
            " VALUES (?,?,?)", credits)
        # Never clobber genres an external provider already supplied.
        con.executemany(
            "INSERT OR IGNORE INTO artists (artist_uri, name) VALUES (?,?)", people)

        cur = con.executemany(
            "INSERT OR IGNORE INTO plays"
            " (track_uri, played_at, ms_played, source, context_uri)"
            " VALUES (?,?,?,?,?)", rows)
        inserted = cur.rowcount

        con.execute(
            "INSERT OR REPLACE INTO runs"
            " (started_at, fetched, inserted, newest_played_at, ok, error)"
            " VALUES (?,?,?,?,1,NULL)", (started, fetched, inserted, newest))
        con.commit()
        print(f"{started}  fetched={fetched} new={inserted} newest={newest}")

        if fetched == 50:
            # Buffer was full: we may have lost plays older than the oldest item.
            print("WARNING: hit the 50-item cap - poll more often.", file=sys.stderr)

    except BaseException as e:
        con.execute(
            "INSERT OR REPLACE INTO runs"
            " (started_at, fetched, inserted, newest_played_at, ok, error)"
            " VALUES (?,?,?,?,0,?)", (started, fetched, inserted, newest, repr(e)))
        con.commit()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    poll()
