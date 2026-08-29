"""OAuth PKCE for a personal Spotify app. No client secret involved."""
import base64, hashlib, http.server, json, os, secrets, threading, time
import urllib.parse, urllib.request, webbrowser

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES       = "user-read-recently-played user-top-read"
HERE         = os.path.dirname(os.path.abspath(__file__))
ROOT         = os.path.dirname(HERE)
TOKENS       = os.path.join(ROOT, "var", "tokens.json")
CONFIG       = os.path.join(ROOT, "var", "config.json")


def client_id():
    """Read the app's client id from the environment or config.json.

    Public by design in a PKCE flow, but kept out of the repo so the project
    is usable by anyone with their own Spotify app.
    """
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    if not cid and os.path.exists(CONFIG):
        with open(CONFIG) as f:
            cid = json.load(f).get("client_id")
    if not cid:
        raise SystemExit(
            "No client id. Create an app at developer.spotify.com/dashboard\n"
            "with redirect URI " + REDIRECT_URI + ", then either:\n"
            "  cp config.example.json var/config.json   # and paste the id in\n"
            "  export SPOTIFY_CLIENT_ID=<your id>")
    return cid


def _post(data):
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _save(tok):
    # var/ is gitignored, so it won't exist in a fresh clone.
    os.makedirs(os.path.dirname(TOKENS), exist_ok=True)
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    with open(TOKENS, "w") as f:
        json.dump(tok, f, indent=2)
    os.chmod(TOKENS, 0o600)
    return tok


def login():
    """One-time interactive authorization. Opens a browser, catches the redirect."""
    verifier  = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id(), "response_type": "code",
        "redirect_uri": REDIRECT_URI, "scope": SCOPES, "state": state,
        "code_challenge_method": "S256", "code_challenge": challenge,
    })

    box = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            box.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            ok = "code" in box and box.get("state") == state
            self.wfile.write(b"<h2>Authorized. You can close this tab.</h2>" if ok
                             else b"<h2>Authorization failed.</h2>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    print("Opening browser to authorize...\nIf it doesn't open:\n" + url)
    webbrowser.open(url)

    for _ in range(300):
        if box:
            break
        time.sleep(1)
    srv.server_close()

    if "code" not in box:
        raise SystemExit(f"No authorization code received: {box or 'timed out'}")
    if box.get("state") != state:
        raise SystemExit("State mismatch - aborting.")

    tok = _post({
        "grant_type": "authorization_code", "code": box["code"],
        "redirect_uri": REDIRECT_URI, "client_id": client_id(),
        "code_verifier": verifier,
    })
    _save(tok)
    print(f"Authorized. Tokens saved to {TOKENS}")
    return tok


def access_token():
    """Return a valid access token, refreshing if needed."""
    if not os.path.exists(TOKENS):
        raise SystemExit("Not authorized yet. Run:  python3 auth.py")
    with open(TOKENS) as f:
        tok = json.load(f)

    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]

    try:
        new = _post({
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": client_id(),
        })
    except urllib.error.HTTPError as e:
        # Refresh tokens on this app expire after 180 days. Fail loudly:
        # a silent death here is exactly how you lose weeks of history.
        raise SystemExit(
            f"TOKEN REFRESH FAILED ({e.code}). Re-authorize with:  python3 auth.py"
        ) from e

    new.setdefault("refresh_token", tok["refresh_token"])
    return _save(new)["access_token"]


if __name__ == "__main__":
    login()
