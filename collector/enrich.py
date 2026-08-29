"""Fill in track names/artists for URIs we've seen. Cached: each track fetched once."""
import datetime as dt, urllib.error
import auth
from core import api, connect



def enrich():
    con = connect()
    todo = [r[0] for r in con.execute(
        "SELECT DISTINCT p.track_uri FROM plays p"
        " LEFT JOIN tracks t USING (track_uri)"
        " WHERE t.track_uri IS NULL AND p.track_uri LIKE 'spotify:track:%'")]

    if not todo:
        print("Nothing to enrich - all tracks already have metadata.")
        return

    print(f"Enriching {len(todo)} track(s)...")
    token = auth.access_token()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    done = 0

    for uri in todo:
        try:
            t = api("/tracks/" + uri.rsplit(":", 1)[-1], token)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # track no longer in the catalog
            raise
        artist = (t.get("artists") or [{}])[0]
        album  = t.get("album") or {}
        con.execute(
            "INSERT OR REPLACE INTO tracks (track_uri, name, artist_name,"
            " artist_uri, album_name, album_uri, duration_ms, popularity,"
            " enriched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (t["uri"], t.get("name"), artist.get("name"), artist.get("uri"),
             album.get("name"), album.get("uri"), t.get("duration_ms"),
             t.get("popularity"), now))
        done += 1
        if done % 25 == 0 or done == len(todo):
            con.commit()
            print(f"  {done}/{len(todo)}")
    con.commit()

    missing = len(todo) - done
    if missing:
        print(f"NOTE: {missing} track(s) returned no metadata (removed or "
              f"region-locked); their plays still count.", file=sys.stderr)
    con.close()


if __name__ == "__main__":
    enrich()
