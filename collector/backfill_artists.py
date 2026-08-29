"""Populate track_artists for tracks stored before multi-artist support.

Early versions kept only the primary artist, so any play of a collaboration
counted toward one name. This refetches each affected track and records every
credit. Safe to re-run; only touches tracks with no credits yet.
"""
import datetime as dt, urllib.error
import auth
from core import api, connect


def main():
    con = connect()
    todo = [r[0] for r in con.execute(
        "SELECT t.track_uri FROM tracks t"
        " LEFT JOIN track_artists ta ON ta.track_uri = t.track_uri"
        " WHERE ta.track_uri IS NULL AND t.track_uri LIKE 'spotify:track:%'")]
    if not todo:
        print("Nothing to backfill - every track already has credits.")
        return

    print(f"Backfilling credits for {len(todo)} track(s)...")
    token = auth.access_token()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    done = 0
    for uri in todo:
        try:
            t = api("/tracks/" + uri.rsplit(":", 1)[-1], token)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        album = t.get("album") or {}
        for pos, a in enumerate(t.get("artists") or []):
            if not a.get("uri"):
                continue
            con.execute("INSERT OR IGNORE INTO track_artists"
                        " (track_uri, artist_uri, position) VALUES (?,?,?)",
                        (uri, a["uri"], pos))
            con.execute("INSERT OR IGNORE INTO artists (artist_uri, name)"
                        " VALUES (?,?)", (a["uri"], a.get("name")))
        con.execute("UPDATE tracks SET album_release_date = COALESCE(album_release_date, ?),"
                    " album_type = COALESCE(album_type, ?) WHERE track_uri = ?",
                    (album.get("release_date"), album.get("album_type"), uri))
        done += 1
        if done % 25 == 0 or done == len(todo):
            con.commit()
            print(f"  {done}/{len(todo)}")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
