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
import datetime
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
MEMORY_FILE = os.path.join(DATA_DIR, "radio-memory.json")
DEFAULT_SEEDED_FILE = os.path.join(DATA_DIR, "default-track-seeded")

# Coolify sets PORT; default to 8080 for local dev / plain docker runs.
PORT = int(os.environ.get("PORT") or os.environ.get("RJSHEETAL_PORT") or "8080")
STATION = os.environ.get("RJSHEETAL_STATION", "Sheetal FM")
TAGLINE = os.environ.get("RJSHEETAL_TAGLINE", "Sheetal's live radio station")
MAX_BUF = 512 * 1024
READ_CHUNK = 32 * 1024
LIVE_STALE_S = 10

MAX_QUEUE = int(os.environ.get("RJSHEETAL_MAX_QUEUE", "8"))
RATE_WINDOW_S = int(os.environ.get("RJSHEETAL_RATE_WINDOW", "600"))
RATE_LIMIT = int(os.environ.get("RJSHEETAL_RATE_LIMIT", "3"))
SPOTIFY_TIMEOUT_S = float(os.environ.get("RJSHEETAL_SPOTIFY_TIMEOUT", "6"))

SHARED_TOKEN = os.environ.get("RJSHEETAL_TOKEN", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "7qBNUtXRGP0jPi0H4r8k")
# v3 conversational is for the live agent session, not the REST TTS endpoint
# used by the one-shot RJ fallback.
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_AGENT_ID = os.environ.get("ELEVENLABS_AGENT_ID", "")
LISTENER_NAME = os.environ.get("RJSHEETAL_LISTENER_NAME", "Sheetal")
DEFAULT_TRACK_URI = os.environ.get("RJSHEETAL_DEFAULT_TRACK_URI", "spotify:track:3dcSec3fFteTR6QlQ194aI").strip()
SPOTIFY_PLAYLIST_ID = os.environ.get("SPOTIFY_PLAYLIST_ID", "").strip() or "2JXK0KRt8pLkmUqIPPmmQQ"


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
        self.last_append = 0.0

    def on_air(self):
        return self.total > 0 and time.time() - self.last_append < LIVE_STALE_S

    def append(self, chunk):
        with self.lock:
            self.data += chunk
            self.total += len(chunk)
            self.last_append = time.time()
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


def load_memory():
    return read_json(MEMORY_FILE, {
        "plays": {}, "skips": {}, "replays": {}, "requests": [],
        "conversations": [], "moods": [], "updated": 0,
    })


def memory_context():
    memory = load_memory()
    def top(bucket):
        return [name for name, _ in sorted(bucket.items(), key=lambda pair: pair[1], reverse=True)[:6]]
    return {
        "favorite_tracks": top(memory.get("plays", {})),
        "skipped_tracks": top(memory.get("skips", {})),
        "replayed_tracks": top(memory.get("replays", {})),
        "recent_requests": memory.get("requests", [])[-8:],
        "recent_conversations": memory.get("conversations", [])[-8:],
        "recent_moods": memory.get("moods", [])[-8:],
        "updated": memory.get("updated", 0),
    }


def record_memory(event):
    """Store compact behavioural signals; never store audio or access tokens."""
    memory = load_memory()
    kind = str(event.get("type", ""))[:24]
    track = str(event.get("track", ""))[:240]
    if kind in ("play", "skip", "replay") and track:
        bucket = memory.setdefault(kind + "s", {})
        bucket[track] = int(bucket.get(track, 0)) + 1
        if kind == "play":
            # Browser Spotify playback is the personal station source. Keep
            # the RJ webhook aligned with the track the listener actually
            # started, rather than the retired local AutoDJ metadata file.
            write_json(AUDIO_FILE, {"title": track, "ts": int(time.time()), "personal": True})
    if kind == "request" and track:
        memory.setdefault("requests", []).append({"track": track, "ts": int(time.time())})
        memory["requests"] = memory["requests"][-50:]
    if kind == "mood" and event.get("text"):
        memory.setdefault("moods", []).append({"text": str(event["text"])[:240], "ts": int(time.time())})
        memory["moods"] = memory["moods"][-50:]
    if kind == "conversation" and event.get("text"):
        memory.setdefault("conversations", []).append({
            "source": str(event.get("source", "user"))[:20],
            "text": str(event["text"])[:400], "ts": int(time.time()),
        })
        memory["conversations"] = memory["conversations"][-80:]
    memory["updated"] = int(time.time())
    write_json(MEMORY_FILE, memory)


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
    with urllib.request.urlopen(req, timeout=SPOTIFY_TIMEOUT_S) as r:
        d = json.loads(r.read())
    t["value"] = d["access_token"]
    t["expires"] = time.time() + d["expires_in"] - 30
    return t["value"]


def spotify_get(path):
    req = urllib.request.Request(
        "https://api.spotify.com/v1" + path,
        headers={"Authorization": "Bearer " + spotify_token()})
    with urllib.request.urlopen(req, timeout=SPOTIFY_TIMEOUT_S) as r:
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


def playlist_tracks(limit=20):
    playlist_id = SPOTIFY_PLAYLIST_ID
    if not playlist_id:
        return []
    d = spotify_get(f"/playlists/{urllib.parse.quote(playlist_id, safe='')}/items?limit={limit}&market=IN")
    out = []
    for item in d.get("items", []):
        t = item.get("item") or item.get("track") or {}
        if not t.get("uri"):
            continue
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


def seed_default_track():
    """Put the configured opening track into the queue once per data volume."""
    if not DEFAULT_TRACK_URI or os.path.exists(DEFAULT_SEEDED_FILE) or not CREDS.get("cid"):
        return
    try:
        track = find_track(DEFAULT_TRACK_URI)
        if not track:
            log("default track lookup failed")
            return
        with QUEUE_LOCK:
            q = load_queue()
            if not any(item.get("status") != "done" for item in q):
                q.append({
                    "id": "default-" + base64.b64encode(os.urandom(6)).decode().replace("+", "").replace("/", ""),
                    "uri": track["uri"], "name": track["name"], "artist": track["artist"],
                    "album": track["album"], "art": track["art"], "dur_ms": track["dur_ms"],
                    "ts": int(time.time()), "status": "queued", "source": "station-default",
                })
                save_queue(q)
        open(DEFAULT_SEEDED_FILE, "w", encoding="utf-8").close()
        log(f"seeded default track: {track['name']} — {track['artist']}")
    except Exception as e:
        log(f"default track seed failed: {e!r}")


# ---------------------------------------------------------------- RJ assistant
def rj_tool_call(message):
    """Run the small set of safe station tools the public RJ can use."""
    text = (message or "").strip()
    low = text.lower()
    # Voice commands commonly begin with the station owner's name.
    for wake in ("sheetal ji", "sheetal", "शीतल जी", "शीतल"):
        if low.startswith(wake.lower()):
            text = text[len(wake):].lstrip(" ,:;-—")
            low = text.lower()
            break
    status = read_json(AUDIO_FILE, {})
    current = status.get("title") or "the live Sheetal FM show"
    ist_now = datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

    if any(word in low for word in ("time", "समय", "कितने बजे", "बज रहे")):
        return {"name": "get_time", "result": ist_now.strftime("%H:%M IST")}, \
               f"अभी भारत में {ist_now.strftime('%-I:%M %p')} बजे हैं।"

    if any(word in low for word in ("queue", "queued", "coming up", "next songs")):
        items = [r for r in load_queue() if r.get("status") != "done"]
        if not items:
            return {"name": "get_queue", "result": "The request queue is empty."}, \
                   "अभी request queue खाली है — आप अपनी पसंद का गाना मंगा सकते हैं।"
        names = ", ".join(r.get("name", "a song") for r in items[:3])
        return {"name": "get_queue", "result": names}, \
               f"{LISTENER_NAME} ji, अभी queue में हैं: {names}."

    if any(word in low for word in ("now playing", "playing now", "what song", "what is playing", "what's playing", "current song", "क्या बज")):
        return {"name": "get_now_playing", "result": current}, \
               f"{LISTENER_NAME} ji, अभी आप सुन रही हैं {current}, आपके अपने RJ Sheetal के साथ।"

    request_words = ("play ", "request ", "put on ", "add ", "बजा", "बजाओ", "चलाओ", "मंगा", "सुनना है")
    if any(word in low for word in request_words):
        query = text
        for prefix in ("please play ", "play ", "request ", "put on ", "add ", "song "):
            if low.startswith(prefix):
                query = text[len(prefix):].strip()
                break
        if len(query) >= 2 and CREDS.get("cid"):
            tracks = search_tracks(query, limit=1)
            if tracks:
                track = find_track(tracks[0]["uri"])
                if track:
                    with QUEUE_LOCK:
                        q = load_queue()
                        if any(r.get("uri") == track["uri"] and r.get("status") != "done" for r in q):
                            return {"name": "request_song", "result": "already queued"}, \
                                   f"{LISTENER_NAME} ji, {track['name']} पहले से queue में है।"
                        if len(q) - sum(1 for r in q if r.get("status") == "done") >= MAX_QUEUE:
                            return {"name": "request_song", "result": "queue full"}, \
                                   f"{LISTENER_NAME} ji, queue अभी full है — थोड़ी देर बाद फिर try कीजिए।"
                        item = {
                            "id": base64.b64encode(os.urandom(6)).decode().replace("+", "").replace("/", ""),
                            "uri": track["uri"], "name": track["name"], "artist": track["artist"],
                            "album": track["album"], "art": track["art"], "dur_ms": track["dur_ms"],
                            "ts": int(time.time()), "status": "queued",
                        }
                        q.append(item)
                        save_queue(q)
                    return {"name": "request_song", "result": track["name"]}, \
                           f"{LISTENER_NAME} ji, done — {track['name']} by {track['artist']} मैंने queue में डाल दिया है।"
        return {"name": "search_song", "result": "no match"}, \
               f"{LISTENER_NAME} ji, मुझे वह song नहीं मिला। Search box से एक बार फिर try कीजिए।"

    return {"name": "station_help", "result": "available: now playing, queue, request a song"}, \
           f"{LISTENER_NAME} ji, मैं आपकी live RJ हूँ। आप पूछ सकती हैं अभी क्या बज रहा है, queue में क्या है, या कह सकती हैं कोई गाना बजाओ।"


def elevenlabs_speak(text):
    if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
        return None
    endpoint = "https://api.elevenlabs.io/v1/text-to-speech/" + urllib.parse.quote(ELEVENLABS_VOICE_ID, safe="")
    body = json.dumps({
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.6},
    }).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def agent_request_song(query):
    """Search Spotify and enqueue the best match for an ElevenLabs tool call."""
    query = str(query or "").strip()[:200]
    if len(query) < 2:
        return {"ok": False, "error": "song title is required"}
    tracks = search_tracks(query, limit=1)
    if not tracks:
        return {"ok": False, "error": f"no Spotify match for {query}"}
    track = find_track(tracks[0]["uri"])
    if not track:
        return {"ok": False, "error": "Spotify track lookup failed"}
    with QUEUE_LOCK:
        q = load_queue()
        if any(r.get("uri") == track["uri"] and r.get("status") != "done" for r in q):
            return {"ok": True, "already_queued": True, "song": track["name"], "artist": track["artist"]}
        if len(q) - sum(1 for r in q if r.get("status") == "done") >= MAX_QUEUE:
            return {"ok": False, "error": "queue is full"}
        item = {
            "id": base64.b64encode(os.urandom(6)).decode().replace("+", "").replace("/", ""),
            "uri": track["uri"], "name": track["name"], "artist": track["artist"],
            "album": track["album"], "art": track["art"], "dur_ms": track["dur_ms"],
            "ts": int(time.time()), "status": "queued",
        }
        q.append(item)
        save_queue(q)
    return {"ok": True, "queued": True, "song": track["name"], "artist": track["artist"]}


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
        elif path == "/sw.js":
            self._serve_file("sw.js", "application/javascript; charset=utf-8")
        elif path == "/app.js":
            self._serve_file("app.js", "application/javascript; charset=utf-8")
        elif path == "/rj-lazy.js":
            self._serve_file("rj-lazy.js", "application/javascript; charset=utf-8")
        elif path == "/spotify-personal.js":
            self._serve_file("spotify-personal.js", "application/javascript; charset=utf-8")
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
            current_item = next((item for item in reversed(load_queue()) if item.get("status") == "claimed"), {})
            self._json(200, {
                "station": STATION,
                "tagline": TAGLINE,
                "on_air": self.buf.on_air(),
                "title": st.get("title") or current_item.get("name", ""),
                "artist": st.get("artist") or current_item.get("artist", ""),
                "art": st.get("art") or current_item.get("art", ""),
                "listeners": self.buf.listeners,
                "uptime_s": int(time.time() - self.buf.uptime),
                "queue_len": len(load_queue()),
            })
        elif path == "/api/spotify/config":
            self._json(200, {"client_id": CREDS.get("cid", ""), "playlist_id": SPOTIFY_PLAYLIST_ID, "default_track": DEFAULT_TRACK_URI})
        elif path == "/api/memory/context":
            self._json(200, memory_context())
        elif path == "/api/search":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            try:
                self._json(200, {"results": search_tracks(q[:200])})
            except (TimeoutError, socket.timeout):
                self._json(504, {"results": [], "error": "Spotify search timed out"})
            except Exception as e:
                log(f"search error: {e!r}")
                self._json(502, {"results": [], "error": "Spotify search unavailable"})
        elif path == "/api/playlist":
            try:
                self._json(200, {"configured": bool(SPOTIFY_PLAYLIST_ID), "results": playlist_tracks()})
            except Exception as e:
                log(f"playlist error: {e!r}")
                self._json(502, {"configured": True, "results": [], "error": "Spotify playlist unavailable"})
        elif path == "/api/queue":
            self._json(200, {"queue": load_queue()})
        elif path == "/api/agent/now-playing":
            st = read_json(AUDIO_FILE, {})
            self._json(200, {"station": STATION, "now_playing": st.get("title", ""), "on_air": bool(st.get("personal") or self.buf.on_air())})
        elif path == "/api/agent/queue":
            items = [r for r in load_queue() if r.get("status") != "done"]
            self._json(200, {"station": STATION, "queue": [
                {"song": r.get("name", ""), "artist": r.get("artist", ""), "status": r.get("status", "queued")}
                for r in items[:8]
            ]})
        elif path == "/api/rj":
            self._send(405, json.dumps({"error": "use POST"}), extra={"Allow": "POST"})
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
        elif path == "/api/memory/event":
            event = body if isinstance(body, dict) else {}
            if event.get("type") not in ("play", "skip", "replay", "request", "conversation", "mood"):
                self._json(400, {"error": "unsupported memory event"})
                return
            try:
                record_memory(event)
                self._json(200, {"ok": True})
            except Exception as e:
                log(f"memory error: {e!r}")
                self._json(500, {"error": "memory unavailable"})
        elif path == "/api/rj/announcement":
            if not ELEVENLABS_API_KEY:
                self._json(503, {"error": "RJ announcements are not configured"})
                return
            text = str(body.get("text") or "")[:500]
            if not text:
                self._json(400, {"error": "announcement text required"})
                return
            try:
                audio = elevenlabs_speak(text)
                if not audio:
                    self._json(503, {"error": "RJ announcements are not configured"})
                    return
                self._send(200, audio, "audio/mpeg", {"Cache-Control": "no-store"})
            except Exception as e:
                log(f"announcement error: {e!r}")
                self._json(502, {"error": "RJ announcement unavailable"})
        elif path == "/api/agent/request-song":
            ok, msg = ok_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            query = body.get("song") or body.get("query") or body.get("title") or ""
            self._json(200, agent_request_song(query))
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
        elif path == "/api/queue/next":
            current_uri = str(body.get("current_uri") or "")
            with QUEUE_LOCK:
                q = load_queue()
                if current_uri:
                    for item in q:
                        if item.get("uri") == current_uri and item.get("status") != "done":
                            item["status"] = "done"
                next_item = next((item for item in q if item.get("status") == "queued" and item.get("source") != "station-default"), None)
                if next_item:
                    next_item["status"] = "claimed"
                save_queue(q)
            self._json(200, {"item": next_item})
        elif path == "/api/queue/remove":
            rid = str(body.get("id") or "")
            with QUEUE_LOCK:
                q = load_queue()
                found = False
                for item in q:
                    if item.get("id") == rid and item.get("status") != "done":
                        item["status"] = "done"
                        found = True
                        break
                save_queue(q)
            self._json(200 if found else 404, {"ok": found})
        elif path == "/api/rj":
            message = str(body.get("message") or "")[:500]
            if not message:
                self._json(400, {"error": "message required"})
                return
            ok, msg = ok_request(self._client_ip())
            if not ok:
                self._json(429, {"error": msg})
                return
            try:
                tool, reply = rj_tool_call(message)
                audio = elevenlabs_speak(reply)
                if audio:
                    self._send(200, audio, "audio/mpeg", extra={
                        "X-RJ-Reply": urllib.parse.quote(reply, safe=""),
                        "X-RJ-Tool": tool["name"],
                    })
                else:
                    self._json(200, {"reply": reply, "tool": tool, "voice": False})
            except Exception as e:
                log(f"rj error: {e!r}")
                self._json(502, {"error": "RJ voice is temporarily unavailable"})
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
            chunked = self.headers.get("Transfer-Encoding", "").lower() == "chunked"
            if chunked:
                while True:
                    line = self.rfile.readline(64)
                    if not line:
                        break
                    size_text = line.strip().split(b";", 1)[0]
                    size = int(size_text, 16)
                    if size == 0:
                        self.rfile.readline(64)
                        break
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(READ_CHUNK, remaining))
                        if not chunk:
                            return
                        self.buf.append(chunk)
                        remaining -= len(chunk)
                    self.rfile.read(2)
            else:
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
    seed_default_track()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"rj.sheetal on 0.0.0.0:{PORT}  (token {'set' if SHARED_TOKEN else 'UNSET'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
