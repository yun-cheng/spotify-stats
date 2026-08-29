"""Attach genre tags to artists using Last.fm.

Spotify withholds genres from development-mode apps, so they come from
Last.fm's crowd-sourced tags. Those tags are rich but noisy — they include
places, moods, label names and personal bookmarks alongside real genres — so
they are normalised and filtered before being stored.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
import datetime as dt
from core import ROOT, connect

API = "https://ws.audioscrobbler.com/2.0/"
MIN_COUNT = 15     # Last.fm scores tags 0-100 relative to the artist's top tag
MAX_TAGS  = 6
PAUSE     = 0.25   # stay well inside Last.fm's ~5 req/sec limit

# Tags that are popular but aren't genres.
STOPLIST = {
    "seen live", "female vocalists", "male vocalists", "female vocalist",
    "male vocalist", "favorites", "favourites", "favorite songs", "awesome",
    "beautiful", "love", "chill", "chillout music", "good", "great",
    "my favourites", "my favorites", "spotify", "singer songwriter",
    "singer-songwriter", "under 2000 listeners", "usa", "united states",
    "uk", "american", "british", "french artist", "canadian", "australian",
}
PLACE_RE = re.compile(r"^(new york|los angeles|chicago|london|paris|illinois|"
                      r"california|texas|england|scotland|germany|japan)$")


def normalise(tag):
    """Lowercase and collapse punctuation so 'Hip-Hop' and 'hip hop' merge."""
    t = tag.lower().strip()
    t = t.replace("&", "and")
    t = re.sub(r"[-_/]+", " ", t)
    return re.sub(r"\s+", " ", t)


def usable(tag, artist_name):
    if tag in STOPLIST or PLACE_RE.match(tag):
        return False
    if tag == normalise(artist_name):        # tags naming the artist
        return False
    if len(tag) < 2 or tag.isdigit():
        return False
    return True


def api_key():
    key = os.environ.get("LASTFM_API_KEY")
    if not key:
        cfg = os.path.join(ROOT, "var", "config.json")
        if os.path.exists(cfg):
            with open(cfg) as f:
                key = json.load(f).get("lastfm_api_key")
    if not key:
        raise SystemExit(
            "No Last.fm API key. Create one at last.fm/api/account/create,\n"
            "then add it to var/config.json as \"lastfm_api_key\", or set\n"
            "LASTFM_API_KEY in the environment.")
    return key


def top_tags(artist, key):
    q = urllib.parse.urlencode({"method": "artist.gettoptags", "artist": artist,
                                "api_key": key, "format": "json",
                                "autocorrect": "1"})
    try:
        with urllib.request.urlopen(API + "?" + q, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            time.sleep(5)
            return top_tags(artist, key)
        raise
    if "error" in d:                 # 6 = artist not found
        return []
    raw = (d.get("toptags") or {}).get("tag", [])
    if isinstance(raw, dict):        # single tag comes back unwrapped
        raw = [raw]

    seen, out = {}, []
    for t in raw:
        try:
            count = int(t.get("count", 0))
        except (TypeError, ValueError):
            continue
        if count < MIN_COUNT:
            continue
        name = normalise(t.get("name", ""))
        if not usable(name, artist):
            continue
        # Merging variants keeps the highest score rather than double-counting.
        if name not in seen or count > seen[name]:
            seen[name] = count
    for name, count in sorted(seen.items(), key=lambda kv: -kv[1])[:MAX_TAGS]:
        out.append(name)
    return out


def main():
    con = connect()
    todo = con.execute(
        "SELECT artist_uri, name FROM artists"
        " WHERE genres IS NULL AND name IS NOT NULL").fetchall()
    if not todo:
        print("Nothing to do - every artist already has genres.")
        return

    key = api_key()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f"Fetching genres for {len(todo)} artist(s)...")
    found = 0
    for i, (uri, name) in enumerate(todo, 1):
        tags = top_tags(name, key)
        if tags:
            found += 1
        # Store even an empty result, so unknown artists aren't refetched forever.
        con.execute("UPDATE artists SET genres = ?, genre_source = ?,"
                    " enriched_at = ? WHERE artist_uri = ?",
                    (json.dumps(tags), "lastfm", now, uri))
        if i % 20 == 0 or i == len(todo):
            con.commit()
            print(f"  {i}/{len(todo)}")
        time.sleep(PAUSE)
    con.commit()
    print(f"{found}/{len(todo)} artist(s) had usable tags.")
    con.close()


if __name__ == "__main__":
    main()
