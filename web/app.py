"""Streamlit dashboard for the local listening database.

A consumer: reads the database, never writes to it.

    streamlit run web/app.py
"""
import os, re, subprocess, sys, sqlite3
import altair as alt
import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("SPOTIFY_STATS_DB", os.path.join(ROOT, "var", "spotify.db"))

st.set_page_config(page_title="Listening stats", page_icon="🎧", layout="wide")


@st.cache_resource
def conn():
    # check_same_thread=False: Streamlit reruns across threads.
    return sqlite3.connect(DB, check_same_thread=False)


@st.cache_data(ttl=60)
def q(sql, params=()):
    """Query into a DataFrame. Cached briefly so the poller's writes show up."""
    return pd.read_sql_query(sql, conn(), params=params)


if not os.path.exists(DB):
    st.error(f"No database at `{DB}`. Run `python3 collector/poll.py` first.")
    st.stop()

# export_basic rows have minute-precision local timestamps and no track ids,
# so they are excluded to avoid double-counting against api/export rows.
BASE = "p.source IN ('api','export')"

bounds = q(f"SELECT MIN(date(p.played_at)) lo, MAX(date(p.played_at)) hi"
           f" FROM plays p WHERE {BASE}")
if bounds.empty or bounds.lo[0] is None:
    st.warning("No plays recorded yet.")
    st.stop()
lo, hi = pd.to_datetime(bounds.lo[0]).date(), pd.to_datetime(bounds.hi[0]).date()

# ---------------------------------------------------------------- refresh
def collect_now():
    """Run the collector as a subprocess.

    The dashboard stays a read-only consumer: it asks collector/poll.py to
    fetch rather than writing to the database itself.
    """
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "collector", "poll.py")],
        capture_output=True, text=True, timeout=120, cwd=ROOT)


last = q("SELECT MAX(played_at) p, (SELECT MAX(started_at) FROM runs) r FROM plays")
st.sidebar.header("Data")
if last.p[0]:
    local = q("SELECT datetime(?, 'localtime') t", (last.p[0],)).t[0]
    st.sidebar.caption(f"Newest play: {local[:16]}")

if st.sidebar.button("↻ Fetch new plays", use_container_width=True):
    with st.spinner("Asking Spotify…"):
        try:
            r = collect_now()
        except subprocess.TimeoutExpired:
            r = None
    if r is None:
        st.sidebar.error("Timed out.")
    elif r.returncode:
        # Most likely the 180-day refresh token expired.
        st.sidebar.error((r.stderr or r.stdout or "Poll failed").strip()[:300])
    else:
        m = re.search(r"new=(\d+)", r.stdout or "")
        n = int(m.group(1)) if m else 0
        # Clearing the cache here is enough: the button click already triggers
        # a rerun, and the queries below run after this block. An explicit
        # st.rerun() would restart the script and discard the toast.
        st.cache_data.clear()
        st.toast(f"{n} new play{'' if n == 1 else 's'}" if n else "Already up to date")

# ---------------------------------------------------------------- sidebar
st.sidebar.header("Filters")
start, end = st.sidebar.date_input(
    "Date range", value=(lo, hi), min_value=lo, max_value=hi,
    format="YYYY-MM-DD") if lo < hi else (lo, hi)
if isinstance(start, tuple):            # date_input returns a tuple mid-edit
    start, end = start[0], start[-1]

credit = st.sidebar.radio(
    "Artist credit", ["All credits", "Primary only"],
    help="A track can credit several artists. 'All credits' counts a play "
         "toward every one of them, including features.")

top_n = st.sidebar.slider("Rows per chart", 5, 30, 12)

WHERE = f"{BASE} AND date(p.played_at) BETWEEN ? AND ?"
P = (str(start), str(end))

# ---------------------------------------------------------------- overview
st.title("🎧 Listening stats")
st.caption(f"{start} to {end}")

o = q(f"SELECT COUNT(*) plays, COUNT(DISTINCT p.track_uri) tracks,"
      f" SUM(COALESCE(p.ms_played, t.duration_ms, 0)) ms"
      f" FROM plays p LEFT JOIN tracks t USING (track_uri) WHERE {WHERE}", P)
na = q(f"SELECT COUNT(DISTINCT ta.artist_uri) n FROM plays p"
       f" JOIN track_artists ta USING (track_uri) WHERE {WHERE}", P)

if not o.plays[0]:
    st.info("No plays in this range.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Plays", f"{int(o.plays[0]):,}")
c2.metric("Tracks", f"{int(o.tracks[0]):,}")
c3.metric("Artists", f"{int(na.n[0]):,}")
c4.metric("Hours", f"{(o.ms[0] or 0) / 3_600_000:.1f}")

# ---------------------------------------------------------------- charts
def hbar(df, label, value, title):
    """Horizontal bar chart, largest first, with untruncated labels."""
    return (alt.Chart(df, title=title).mark_bar(cornerRadiusEnd=3)
            .encode(x=alt.X(f"{value}:Q", title="plays",
                            axis=alt.Axis(tickMinStep=1, format="d")),
                    y=alt.Y(f"{label}:N", sort="-x", title=None,
                            # labelOverlap=False keeps every label: Vega drops
                            # alternate ones when rows are too short for the text.
                            axis=alt.Axis(labelLimit=0, labelOverlap=False)),
                    tooltip=[label, value])
            .properties(height=max(200, 34 * len(df))))


tracks = q(f"SELECT t.name || ' — ' || COALESCE(t.artist_name,'?') AS track,"
           f" COUNT(*) plays FROM plays p JOIN tracks t USING (track_uri)"
           f" WHERE {WHERE} GROUP BY p.track_uri"
           f" ORDER BY plays DESC, track LIMIT ?", P + (top_n,))
st.altair_chart(hbar(tracks, "track", "plays", "Top tracks"),
                use_container_width=True)

left, right = st.columns(2)

with left:
    if credit == "All credits":
        artists = q(f"SELECT ar.name artist, COUNT(*) plays FROM plays p"
                    f" JOIN track_artists ta USING (track_uri)"
                    f" JOIN artists ar USING (artist_uri) WHERE {WHERE}"
                    f" GROUP BY ar.artist_uri ORDER BY plays DESC LIMIT ?",
                    P + (top_n,))
    else:
        artists = q(f"SELECT t.artist_name artist, COUNT(*) plays FROM plays p"
                    f" JOIN tracks t USING (track_uri) WHERE {WHERE}"
                    f" AND t.artist_name IS NOT NULL"
                    f" GROUP BY t.artist_uri ORDER BY plays DESC LIMIT ?",
                    P + (top_n,))
    st.altair_chart(hbar(artists, "artist", "plays", f"Top artists ({credit.lower()})"),
                    use_container_width=True)

with right:
    genres = q(f"WITH g AS (SELECT ar.artist_uri, j.value genre FROM artists ar,"
               f" json_each(ar.genres) j)"
               f" SELECT g.genre, COUNT(*) plays FROM plays p"
               f" JOIN track_artists ta USING (track_uri) JOIN g USING (artist_uri)"
               f" WHERE {WHERE} GROUP BY g.genre ORDER BY plays DESC LIMIT ?",
               P + (top_n,))
    if genres.empty:
        st.info("No genres yet — run `python3 collector/fetch_genres.py`.")
    else:
        st.altair_chart(hbar(genres, "genre", "plays", "Top genres"),
                        use_container_width=True)

daily_col = st.container()
with daily_col:
    daily = q(f"SELECT date(p.played_at) day, COUNT(*) plays FROM plays p"
              f" WHERE {WHERE} GROUP BY day ORDER BY day", P)
    st.altair_chart(
        alt.Chart(daily, title="Plays per day").mark_area(
            line=True, opacity=0.3, interpolate="monotone")
        .encode(x=alt.X("day:T", title=None), y=alt.Y("plays:Q", title="plays"),
                tooltip=["day", "plays"])
        .properties(height=260), use_container_width=True)

# ---------------------------------------------------------------- clock
clock = q(f"SELECT CAST(strftime('%w', p.played_at, 'localtime') AS INT) dow,"
          f" CAST(strftime('%H', p.played_at, 'localtime') AS INT) hour,"
          f" COUNT(*) plays FROM plays p WHERE {WHERE} GROUP BY dow, hour", P)
DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
clock["day"] = clock.dow.map(dict(enumerate(DAYS)))
st.altair_chart(
    alt.Chart(clock, title="When you listen (local time)")
    .mark_rect().encode(
        x=alt.X("hour:O", title="hour of day"),
        y=alt.Y("day:N", sort=DAYS, title=None),
        color=alt.Color("plays:Q", scale=alt.Scale(scheme="greens"),
                        legend=alt.Legend(title="plays")),
        tooltip=["day", "hour", "plays"])
    .properties(height=200), use_container_width=True)

# ---------------------------------------------------------------- history
st.subheader("Play history")

hc1, hc2 = st.columns([3, 1])
search = hc1.text_input("Search", placeholder="track, artist or album…",
                        label_visibility="collapsed")
limit = hc2.selectbox("Show", [100, 500, 2000, 10000], index=0,
                      label_visibility="collapsed")

hist_where = WHERE
hist_params = list(P)
if search:
    like = f"%{search}%"
    hist_where += (" AND (t.name LIKE ? OR t.artist_name LIKE ?"
                   " OR t.album_name LIKE ?)")
    hist_params += [like, like, like]

history = q(f"SELECT datetime(p.played_at, 'localtime') AS played,"
            f" t.name AS track, t.artist_name AS artist, t.album_name AS album,"
            f" p.ms_played, p.source"
            f" FROM plays p LEFT JOIN tracks t USING (track_uri)"
            f" WHERE {hist_where} ORDER BY p.played_at DESC LIMIT ?",
            tuple(hist_params) + (limit,))

total_matching = q(f"SELECT COUNT(*) n FROM plays p"
                   f" LEFT JOIN tracks t USING (track_uri) WHERE {hist_where}",
                   tuple(hist_params)).n[0]

if history.empty:
    st.info("No plays match.")
else:
    # ms_played is only present on export rows; the API doesn't report it.
    history["listened"] = history.ms_played.apply(
        lambda ms: "" if pd.isna(ms) else f"{int(ms) // 60000}:{int(ms) % 60000 // 1000:02d}")
    shown = history.drop(columns=["ms_played"])
    st.caption(f"Showing {len(shown):,} of {int(total_matching):,} plays"
               + (f" matching “{search}”" if search else ""))
    st.dataframe(
        shown, use_container_width=True, hide_index=True, height=420,
        column_config={
            "played": st.column_config.DatetimeColumn(
                "Played (local)", format="YYYY-MM-DD HH:mm"),
            "track": st.column_config.TextColumn("Track", width="large"),
            "artist": st.column_config.TextColumn("Artist", width="medium"),
            "album": st.column_config.TextColumn("Album", width="medium"),
            "listened": st.column_config.TextColumn(
                "Listened", help="Only recorded for plays from the data export"),
            "source": st.column_config.TextColumn("Source", width="small"),
        })
    st.download_button(
        "Download as CSV", shown.to_csv(index=False).encode(),
        file_name="play-history.csv", mime="text/csv")

# ---------------------------------------------------------------- health
with st.expander("Collector health"):
    runs = q("SELECT started_at, fetched, inserted, ok, error FROM runs"
             " ORDER BY started_at DESC LIMIT 20")
    failed = int((runs.ok == 0).sum()) if not runs.empty else 0
    if failed:
        st.warning(f"{failed} of the last {len(runs)} runs failed.")
    st.dataframe(runs, use_container_width=True, hide_index=True)
