#!/usr/bin/env python3
"""RJ Sheetal — public radio backend for the VPS.

Serves the mobile-first station site, a Spotify-powered song-request
queue, live status, and relays the home-supplied MP3 stream out to
listeners.  Fully dependency-free (stdlib only), mirrors the home
radio.py architecture:

  Home side:  dj/uplink.py  streams mp3 -> POST /ingest
              dj/autodj.py  polls /api/pending, marks /api/claim, /api/done
  Listeners:  GET /api/stream  (live mp3)
  Public:     GET /            (static site)
              GET /api/search?q=...
              POST /api/request
              GET /api/status, /api/queue
"""
import base64
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA_DIR = os.environ.get("RJSHEETAL_DATA") or HERE
QUEUE_FILE = os.path.join(DATA_DIR, "requests.json")
AUDIO_FILE = os.path.join(DATA_DIR, "audio.json")

# Coolify sets PORT; default to 8080 for local dev / plain docker runs.
PORT = int(os.environ.get("PORT") or os.environ.get("RJSHEETAL_PORT") or "8080")
STATION = os.environ.get("RJSHEETAL_STATION", "RJ Sheetal")
TAGLINE = os.environ.get("RJSHEETAL_TAGLINE", "your favourite radio jockey")
MAX_BUF = 512 * 1024
READ_CHUNK = 32 * 1024

MAX_QUEUE = int(os.environ.get("RJSHEETAL_MAX_QUEUE", "8"))
RATE_WINDOW_S = int(os.environ.get("RJSHEETAL_RATE_WINDOW", "600"))
RATE_LIMIT = int(os.environ.get("RJSHEETAL_RATE_LIMIT", "3"))

SHARED_TOKEN = os.environ.get("RJSHEETAL_TOKEN", "")


def log(msg):
    print(f"[site] {msg}", file=sys.stderr, flush=True)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- audio
class Buffer:
    """Ring buffer of the live stream, plus a thread-safe wait."""

    def __init__(self):
        self.lock = threading.Condition()
        self.data = bytearray()
        self.start = 0       # absolute index of self.data[0]
        self.total = 0       # absolute index of next byte to append
        self.listeners = 0
        self.uptime = time.time()

    def on_air(self):
        return self.total > 0

    def append(self, chunk):
        with self.lock:
            self.data += chunk
            self.total += len(chunk)
            while len(self.data) > MAX_BUF:
                drop = len(self.data) - MAX_BUF
                del self.data[:drop]
                self.start += drop
            self.lock.notify_all()

    def stream_to(self, sock):
        with self.lock:
            self.listeners += 1
        try:
            want = self.total
            sock.settimeout(30)
            while True:
                with self.lock:
                    if want < self.start:
                        want = self.start
                    if want >= self.total:
                        if not self.lock.wait(0.6):
                            continue
                        continue
                    end = min(want + READ_CHUNK, self.total)
                    got = bytes(self.data[want - self.start:end - self.start])
                    want = end
                sock.sendall(got)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            pass
        finally:
            with self.lock:
                self.listeners -= 1
            try:
                sock.close()
            except Exception:
                pass


AUDIO = Buffer()


# ---------------------------------------------------------------- queue
QUEUE_LOCK = threading.Lock()
RATE_LOCK = threading.Lock()
HITS = {}   # ip -> [timestamps]


def load_queue():
    return read_json(QUEUE_FILE, [])


def save_queue(q):
    write_json(QUEUE_FILE, q)


def ok_request(ip):
    now = time.time()
    with RATE_LOCK:
        hits = [t for t in HITS.get(ip, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= RATE_LIMIT:
            HITS[ip] = hits
            return False, "slow down — a few too many requests"
        HITS[ip] = hits + [now]
    return True, ""


# ---------------------------------------------------------------- spotify
SPOT_TOKEN = {"value": None, "expires": 0.0}
CREDS = {}


def _load_creds():
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not (cid and secret):
        try:
            import creds
            cid = cid or getattr(creds, "SPOTIFY_CLIENT_ID", "")
            secret = secret or getattr(creds, "SPOTIFY_CLIENT_SECRET", "")
        except ImportError:
            pass
    CREDS["cid"], CREDS["secret"] = cid or "", secret or ""


def spotify_b64(s):
    return base64.b64encode(s.encode()).decode()


def spotify_token():
    t = SPOT_TOKEN
    if t["value"] and time.time() < t["expires"]:
        return t["value"]
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token", data=body,
        headers={"Authorization": "Basic " + spotify_b64(f"{CREDS['cid']}:{CREDS['secret']}"),
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read())
    t["value"] = d["access_token"]
    t["expires"] = time.time() + d["expires_in"] - 30
    return t["value"]


def spotify_get(path):
    req = urllib.request.Request(
        "https://api.spotify.com/v1" + path,
        headers={"Authorization": "Bearer " + spotify_token()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def search_tracks(q, limit=6):
    if not CREDS.get("cid"):
        return []
    query = urllib.parse.quote(q)
    d = spotify_get(f"/search?q={query}&type=track&limit={limit}&market=IN")
    out = []
    for t in d.get("tracks", {}).get("items", []):
        images = t.get("album", {}).get("images") or []
        out.append({
            "uri": t.get("uri", ""),
            "id": t.get("id", ""),
            "name": t.get("name", ""),
            "artist": ", ".join(a["name"] for a in t.get("artists", [])),
            "album": (t.get("album") or {}).get("name", ""),
            "art": images[0]["url"] if images else "",
            "dur_ms": t.get("duration_ms", 0),
        })
    return out


def find_track(uri):
    tid = uri.split(":")[-1]
    try:
        d = spotify_get(f"/tracks/{tid}")
        images = d.get("album", {}).get("images") or []
        return {
            "uri": d.get("uri", uri),
            "id": d.get("id", tid),
            "name": d.get("name", ""),
            "artist": ", ".join(a["name"] for a in d.get("artists", [])),
            "album": (d.get("album") or {}).get("name", ""),
            "art": images[0]["url"] if images else "",
            "dur_ms": d.get("duration_ms", 0),
        }
    except Exception:
        return None


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "RJSheetal/1.0"
    buf = AUDIO

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _authed(self):
        if not SHARED_TOKEN:
            return True
        return (self.headers.get("X-RJ-Token") or "") == SHARED_TOKEN

    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    # ---- GET ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/healthz":
            self._json(200, {"ok": True})
        elif path == "/manifest.webmanifest":
            self._serve_file("manifest.webmanifest", "application/manifest+json")
        elif path == "/icon.svg":
            self._serve_file("icon.svg", "image/svg+xml")
        elif path == "/apple-touch-icon.png":
            self._serve_file("icon.svg", "image/svg+xml")
        elif path == "/api/stream":
            if not self.buf.on_air():
                self._send(503, '{"error":"off air"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.buf.stream_to(self.connection)
        elif path == "/api/status":
            st = read_json(AUDIO_FILE, {})
            self._json(200, {
                "station": STATION,
                "tagline": TAGLINE,
                "on_air": self.buf.on_air(),
                "title": st.get("title", ""),
                "listeners": self.buf.listeners,
                "uptime_s": int(time.time() - self.buf.uptime),
                "queue_len": len(load_queue()),
            })
        elif path == "/api/search":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            self._json(200, {"results": search_tracks(q[:200])})
        elif path == "/api/queue":
            self._json(200, {"queue": load_queue()})
        elif path == "/api/pending":
            if not self._authed():
                self._json(403, {"error": "forbidden"})
                return
            q = [r for r in load_queue() if r.get("status") == "queued"]
            self._json(200, {"pending": q})
        else:
            self._json(404, {"error": "not found"})

    def _serve_file(self, name, ctype):
        p = os.path.join(STATIC, name)
        try:
            with open(p, "rb") as f:
                self._send(200, f.read(), ctype)
        except Exception:
            self._json(404, {"error": "missing static file"})

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        if path == "/ingest":
            if not self._authed():
                self._json(403, {"error": "forbidden"})
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self._ingest_source()
            return
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return

        if path == "/api/metadata":
            if not self._authed():
                self._json(403, {"error": "forbidden"})
                return
            st = read_json(AUDIO_FILE, {})
            if "title" in body:
                st["title"] = str(body["title"])[:200]
            st["ts"] = time.time()
            write_json(AUDIO_FILE, st)
            self._json(200, {"ok": True})
        elif path == "/api/request":
            ok, msg = ok_request(self._client_ip())
            if not ok:
                self._json(429, {"error": msg})
                return
            uri = str(body.get("uri") or "")
            track = find_track(uri)
            if not track or not uri.startswith("spotify:track:"):
                self._json(400, {"error": "not a valid spotify track"})
                return
            with QUEUE_LOCK:
                q = load_queue()
                if any(r.get("uri") == uri and r.get("status") != "done"
                       for r in q):
                    self._json(409, {"error": "already in queue"})
                    return
                if len(q) - sum(1 for r in q if r.get("status") == "done") >= MAX_QUEUE:
                    self._json(429, {"error": f"queue full ({MAX_QUEUE} max)"})
                    return
                item = {
                    "id": base64.b64encode(os.urandom(6)).decode().replace("+", "").replace("/", ""),
                    "uri": track["uri"],
                    "name": track["name"],
                    "artist": track["artist"],
                    "album": track["album"],
                    "art": track["art"],
                    "dur_ms": track["dur_ms"],
                    "ts": int(time.time()),
                    "status": "queued",
                }
                q.append(item)
                save_queue(q)
            self._json(200, {"ok": True, "item": item})
        elif path == "/api/claim":
            if not self._authed():
                self._json(403, {"error": "forbidden"})
                return
            rid = str(body.get("id") or "")
            with QUEUE_LOCK:
                q = load_queue()
                for r in q:
                    if r.get("id") == rid:
                        r["status"] = "claimed"
                        save_queue(q)
                        self._json(200, {"ok": True})
                        return
            self._json(404, {"error": "not found"})
        elif path == "/api/done":
            if not self._authed():
                self._json(403, {"error": "forbidden"})
                return
            rid = str(body.get("id") or "")
            with QUEUE_LOCK:
                q = load_queue()
                kept = []
                removed = None
                for r in q:
                    if rid and r.get("id") == rid:
                        removed = r
                    elif r.get("uri") == body.get("uri") and r.get("status") != "done":
                        removed = r
                    else:
                        kept.append(r)
                if removed:
                    save_queue(kept)
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    # ---- ingest relay ----
    def _ingest_source(self):
        """Read the home-supplied mp3 stream until the source disconnects.

        Runs in this connection's thread (ThreadingHTTPServer), appending
        bytes into the shared ring buffer.
        """
        try:
            while True:
                chunk = self.rfile.read(READ_CHUNK)
                if not chunk:
                    break
                self.buf.append(chunk)
        except Exception:
            pass
        log("ingest source disconnected")


def main():
    _load_creds()
    if not CREDS["cid"]:
        log("WARNING: no SPOTIFY_CLIENT_ID/SECRET set — song requests disabled")
    os.makedirs(STATIC, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"rj.sheetal on 0.0.0.0:{PORT}  (token {'set' if SHARED_TOKEN else 'UNSET'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()