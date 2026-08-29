"""Import a Spotify data export into the plays table.

Usage:  python3 import_history.py <path-to-zip-or-folder>

Handles both export formats:

  Extended streaming history  (30-day wait, lifetime coverage)
    Streaming_History_Audio_*.json -- has spotify_track_uri and exact
    second-precision timestamps, so it dedupes cleanly against API rows.

  Account data                (5-day wait, ~1 year coverage)
    StreamingHistory*.json -- NO track URI, and timestamps are truncated to
    the minute in LOCAL time. Rows are stored under source='export_basic'
    with a synthetic uri when the track can't be matched by name, because
    they cannot be reliably deduped against the other two sources.
"""
import glob, hashlib, json, os, sys, tempfile, zipfile
from core import connect


def norm_ts(s):
    """Normalise to the same ISO8601-Z shape poll.py writes."""
    s = s.strip().replace(" ", "T")
    if not s.endswith("Z") and "+" not in s:
        s += "Z"
    return s


def synth_uri(artist, track):
    """Stable pseudo-URI for rows with no real track id."""
    key = f"{(artist or '').lower()}|{(track or '').lower()}"
    return "noturi:" + hashlib.sha1(key.encode()).hexdigest()[:22]


def load(folder):
    plays, meta = [], {}
    files = sorted(glob.glob(os.path.join(folder, "**", "*.json"), recursive=True))
    seen_files = 0

    for path in files:
        base = os.path.basename(path)
        if not base.startswith(("Streaming_History_Audio", "StreamingHistory")):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  skipping unreadable {base}: {e}", file=sys.stderr)
            continue
        if not isinstance(rows, list):
            continue
        seen_files += 1

        for r in rows:
            if "ts" in r:  # extended format
                uri = r.get("spotify_track_uri")
                if not uri:
                    continue  # podcast episode or local file
                ts = norm_ts(r["ts"])
                plays.append((uri, ts, r.get("ms_played"), "export", None))
                name = r.get("master_metadata_track_name")
                if name and uri not in meta:
                    meta[uri] = (name,
                                 r.get("master_metadata_album_artist_name"),
                                 r.get("master_metadata_album_album_name"))
            elif "endTime" in r:  # account-data format
                track, artist = r.get("trackName"), r.get("artistName")
                if not track:
                    continue
                uri = synth_uri(artist, track)
                plays.append((uri, norm_ts(r["endTime"]), r.get("msPlayed"),
                              "export_basic", None))
                meta.setdefault(uri, (track, artist, None))

    return plays, meta, seen_files


def main(src):
    if not os.path.exists(src):
        raise SystemExit(f"No such path: {src}")

    tmp = None
    folder = src
    if zipfile.is_zipfile(src):
        tmp = tempfile.mkdtemp(prefix="spotify-export-")
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        folder = tmp
        print(f"Extracted {src} -> {tmp}")

    plays, meta, nfiles = load(folder)
    if not nfiles:
        raise SystemExit(
            "No streaming-history files found. Expected Streaming_History_Audio_*.json "
            "(extended export) or StreamingHistory*.json (account data).")
    print(f"Read {nfiles} file(s): {len(plays)} play rows, {len(meta)} distinct tracks")

    con = connect()
    before = con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]

    con.executemany(
        "INSERT OR IGNORE INTO plays (track_uri, played_at, ms_played, source,"
        " context_uri) VALUES (?,?,?,?,?)", plays)
    # API rows win the primary key but carry no ms_played (the endpoint doesn't
    # report it). Where an export row collides with one, backfill the duration
    # rather than losing it to INSERT OR IGNORE.
    filled = con.executemany(
        "UPDATE plays SET ms_played = ? WHERE track_uri = ? AND played_at = ?"
        " AND ms_played IS NULL",
        [(ms, u, ts) for u, ts, ms, _, _ in plays if ms is not None]).rowcount

    # Only fills gaps: never overwrites richer metadata already fetched from the API.
    con.executemany(
        "INSERT OR IGNORE INTO tracks (track_uri, name, artist_name, album_name)"
        " VALUES (?,?,?,?)",
        [(u, n, a, alb) for u, (n, a, alb) in meta.items()])
    con.commit()

    after = con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
    print(f"Inserted {after - before} new plays "
          f"({len(plays) - (after - before)} already present)")
    if filled:
        print(f"Backfilled ms_played on {filled} existing row(s)")

    for src_name, n, lo, hi in con.execute(
            "SELECT source, COUNT(*), MIN(played_at), MAX(played_at)"
            " FROM plays GROUP BY source ORDER BY 2 DESC"):
        print(f"  {src_name:<13} {n:>7}  {lo[:10]} .. {hi[:10]}")

    if con.execute("SELECT 1 FROM plays WHERE source='export_basic' LIMIT 1").fetchone():
        print("\nNOTE: export_basic rows have minute-precision local timestamps and no\n"
              "track ids, so they may double-count against 'export'/'api' rows.\n"
              "Prefer source IN ('api','export') for counts once the extended\n"
              "export has landed.", file=sys.stderr)
    con.close()

    if tmp:
        print(f"\n(extracted copy left at {tmp} - delete when done)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
