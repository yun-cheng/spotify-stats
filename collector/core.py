"""Shared plumbing: paths, database access, and a rate-limit-aware API caller."""
import json, os, sqlite3, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB   = os.environ.get("SPOTIFY_STATS_DB",
                      os.path.join(ROOT, "var", "spotify.db"))
SCHEMA = os.path.join(HERE, "schema.sql")


# Columns added after the initial schema. CREATE TABLE IF NOT EXISTS won't add
# them to a database that already exists, so they are applied explicitly.
MIGRATIONS = [
    ("tracks", "album_release_date", "TEXT"),
    ("tracks", "album_type",         "TEXT"),
]


def connect():
    """Open the database, creating or migrating it as needed."""
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    with open(SCHEMA) as f:
        con.executescript(f.read())
    for table, col, decl in MIGRATIONS:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con.commit()
    return con


def api(path, token, retries=5):
    """GET an API path, honouring Retry-After on 429."""
    req = urllib.request.Request(
        "https://api.spotify.com/v1" + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            wait = int(e.headers.get("Retry-After", "2")) + 1
            print(f"  rate limited, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit("Gave up after repeated rate limiting.")
