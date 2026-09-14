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
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA_DIR = os.environ.get("RJSHEETAL_DATA") or HERE
QUEUE_FILE = os.path.join(DATA_DIR, "requests.json")
AUDIO_FILE = os.path.join(DATA_DIR, "audio.json")
MEMORY_FILE = os.path.join(DATA_DIR, "radio-memory.json")
DEFAULT_SEEDED_FILE = os.path.join(DATA_DIR, "default-track-seeded")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
NOTES_FILE = os.path.join(DATA_DIR, "notes.json")
SHOPPING_FILE = os.path.join(DATA_DIR, "shopping.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
MUSIC_COMMAND_FILE = os.path.join(DATA_DIR, "music-command.json")
MUSIC_STATE_FILE = os.path.join(DATA_DIR, "music-state.json")
NANGO_CONNECTION_FILE = os.path.join(DATA_DIR, "nango-connection.json")
PRIVATE_CODE_FILE = os.path.join(DATA_DIR, "assistant-pin.json")

# Coolify sets PORT; default to 8080 for local dev / plain docker runs.
PORT = int(os.environ.get("PORT") or os.environ.get("RJSHEETAL_PORT") or "8080")
STATION = os.environ.get("RJSHEETAL_STATION", "Sheetal")
TAGLINE = os.environ.get("RJSHEETAL_TAGLINE", "Sheetal's personal assistant")
MAX_BUF = 512 * 1024
READ_CHUNK = 32 * 1024
LIVE_STALE_S = 10

MAX_QUEUE = int(os.environ.get("RJSHEETAL_MAX_QUEUE", "8"))
RATE_WINDOW_S = int(os.environ.get("RJSHEETAL_RATE_WINDOW", "600"))
RATE_LIMIT = int(os.environ.get("RJSHEETAL_RATE_LIMIT", "3"))
SPOTIFY_TIMEOUT_S = float(os.environ.get("RJSHEETAL_SPOTIFY_TIMEOUT", "6"))

SHARED_TOKEN = os.environ.get("RJSHEETAL_TOKEN", "")
AGENT_TOKEN = os.environ.get("RJSHEETAL_AGENT_TOKEN", "").strip() or SHARED_TOKEN
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "7qBNUtXRGP0jPi0H4r8k")
# v3 conversational is for the live agent session, not the REST TTS endpoint
# used by the one-shot RJ fallback.
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_AGENT_ID = os.environ.get("ELEVENLABS_AGENT_ID", "")
NANGO_SECRET_KEY = os.environ.get("NANGO_SECRET_KEY", "").strip()
NANGO_API_BASE = os.environ.get("NANGO_API_BASE", "https://api.nango.dev").strip().rstrip("/")
NANGO_INTEGRATION_ID = os.environ.get("NANGO_INTEGRATION_ID", "gmail").strip() or "gmail"
NANGO_USER_ID = os.environ.get("NANGO_USER_ID", "sheetal").strip() or "sheetal"
# Email is deliberately disabled until the public app has an owner-only gate.
# Set this to a private passphrase in Coolify; never put it in the frontend.
RJSHEETAL_PRIVATE_CODE = os.environ.get("RJSHEETAL_PRIVATE_CODE", "").strip()
DEFAULT_PRIVATE_CODE = "0000000"
LISTENER_NAME = os.environ.get("RJSHEETAL_LISTENER_NAME", "Sheetal")
DEFAULT_TRACK_URI = os.environ.get("RJSHEETAL_DEFAULT_TRACK_URI", "").strip()
SPOTIFY_PLAYLIST_ID = os.environ.get("SPOTIFY_PLAYLIST_ID", "").strip() or "2JXK0KRt8pLkmUqIPPmmQQ"

MOOD_QUERIES = {
    "happy": "Hindi upbeat feel good",
    "happier": "Hindi upbeat feel good",
    "sad": "Hindi soft emotional",
    "soft": "Hindi soft romantic acoustic",
    "romantic": "Hindi romantic",
    "focus": "Hindi instrumental chill",
    "calm": "Hindi calm acoustic",
    "travel": "Hindi road trip upbeat",
    "energetic": "Hindi dance workout",
    "sleep": "Hindi relaxing acoustic",
}


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
MEMORY_LOCK = threading.RLock()
ASSISTANT_LOCK = threading.RLock()
MUSIC_LOCK = threading.RLock()
RATE_LOCK = threading.Lock()
HITS = {}   # ip -> [timestamps]
AGENT_HITS = {}  # ip -> [timestamps]


def load_queue():
    return read_json(QUEUE_FILE, [])


def save_queue(q):
    write_json(QUEUE_FILE, q)


def load_memory():
    return read_json(MEMORY_FILE, {
        "plays": {}, "skips": {}, "replays": {}, "requests": [],
        "conversations": [], "moods": [], "preferences": [],
        "taste_notes": [], "updated": 0,
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
        "preferences": memory.get("preferences", [])[-20:],
        "taste_notes": memory.get("taste_notes", [])[-20:],
        "updated": memory.get("updated", 0),
    }


def record_memory(event):
    """Store compact behavioural signals; never store audio or access tokens."""
    with MEMORY_LOCK:
        memory = load_memory()
        kind = str(event.get("type", ""))[:24]
        track = str(event.get("track", ""))[:240]
        if kind in ("play", "skip", "replay") and track:
            bucket = memory.setdefault(kind + "s", {})
            bucket[track] = int(bucket.get(track, 0)) + 1
            if kind == "play":
                # Browser Spotify playback is the personal station source. Keep
                # the assistant aligned with the track the listener actually
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
        if kind in ("preference", "taste"):
            value = str(event.get("preference") or event.get("text") or event.get("taste") or "").strip()[:300]
            if value:
                entry = {"text": value, "category": str(event.get("category") or "general")[:50], "ts": int(time.time())}
                bucket = "taste_notes" if kind == "taste" else "preferences"
                memory.setdefault(bucket, []).append(entry)
                memory[bucket] = memory[bucket][-80:]
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


def ok_agent_request(ip):
    """Rate-limit assistant webhooks without throttling normal music requests."""
    now = time.time()
    with RATE_LOCK:
        hits = [t for t in AGENT_HITS.get(ip, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= max(RATE_LIMIT * 10, 30):
            AGENT_HITS[ip] = hits
            return False, "assistant actions are briefly rate-limited"
        AGENT_HITS[ip] = hits + [now]
    return True, ""


# ---------------------------------------------------------------- personal assistant data
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(IST)


def timestamp():
    return now_ist().isoformat(timespec="seconds")


def new_id(prefix):
    return prefix + "-" + base64.urlsafe_b64encode(os.urandom(7)).decode().rstrip("=")


def clean_text(value, limit=300):
    return " ".join(str(value or "").split())[:limit].strip()


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_list(path):
    data = read_json(path, [])
    return data if isinstance(data, list) else []


def save_list(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    write_json(path, data)


def create_task(title, due="", priority="normal", notes=""):
    title = clean_text(title, 180)
    if not title:
        return None
    priority = clean_text(priority, 20).lower() or "normal"
    if priority not in ("low", "normal", "high"):
        priority = "normal"
    item = {
        "id": new_id("task"), "title": title, "due": clean_text(due, 80),
        "priority": priority, "notes": clean_text(notes, 400),
        "status": "open", "created_at": timestamp(), "updated_at": timestamp(),
    }
    with ASSISTANT_LOCK:
        tasks = load_list(TASKS_FILE)
        tasks.append(item)
        save_list(TASKS_FILE, tasks[-300:])
    return item


def find_task(task_id="", title=""):
    tasks = load_list(TASKS_FILE)
    task_id = clean_text(task_id, 100)
    title = clean_text(title, 180).lower()
    if task_id:
        return next((t for t in tasks if t.get("id") == task_id and t.get("status") != "done"), None)
    if title:
        exact = next((t for t in tasks if t.get("status") != "done" and str(t.get("title", "")).lower() == title), None)
        return exact or next((t for t in tasks if t.get("status") != "done" and title in str(t.get("title", "")).lower()), None)
    return None


def complete_task(task_id="", title=""):
    with ASSISTANT_LOCK:
        tasks = load_list(TASKS_FILE)
        target = find_task(task_id, title)
        if not target:
            return None
        for item in tasks:
            if item.get("id") == target.get("id"):
                item["status"] = "done"
                item["completed_at"] = timestamp()
                item["updated_at"] = timestamp()
                target = item
                break
        save_list(TASKS_FILE, tasks)
        return target


def remove_task(task_id="", title=""):
    with ASSISTANT_LOCK:
        tasks = load_list(TASKS_FILE)
        target = find_task(task_id, title)
        if not target:
            return None
        kept = [item for item in tasks if item.get("id") != target.get("id")]
        save_list(TASKS_FILE, kept)
        return target


def create_note(title, body, tags=""):
    title = clean_text(title, 120) or "Untitled note"
    body = str(body or "").strip()[:2000]
    if not body:
        return None
    item = {"id": new_id("note"), "title": title, "body": body,
            "tags": clean_text(tags, 160), "created_at": timestamp(), "updated_at": timestamp()}
    with ASSISTANT_LOCK:
        notes = load_list(NOTES_FILE)
        notes.append(item)
        save_list(NOTES_FILE, notes[-300:])
    return item


def remove_note(note_id):
    with ASSISTANT_LOCK:
        notes = load_list(NOTES_FILE)
        target = next((n for n in notes if n.get("id") == clean_text(note_id, 100)), None)
        if not target:
            return None
        save_list(NOTES_FILE, [n for n in notes if n.get("id") != target.get("id")])
        return target


def add_shopping_item(item, quantity="", category=""):
    name = clean_text(item, 140)
    if not name:
        return None
    entry = {"id": new_id("shop"), "item": name, "quantity": clean_text(quantity, 60),
             "category": clean_text(category, 60), "status": "open",
             "created_at": timestamp(), "updated_at": timestamp()}
    with ASSISTANT_LOCK:
        items = load_list(SHOPPING_FILE)
        duplicate = next((x for x in items if x.get("status") != "done" and str(x.get("item", "")).lower() == name.lower()), None)
        if duplicate:
            if entry["quantity"]:
                duplicate["quantity"] = entry["quantity"]
            duplicate["updated_at"] = timestamp()
            save_list(SHOPPING_FILE, items)
            return duplicate
        items.append(entry)
        save_list(SHOPPING_FILE, items[-300:])
    return entry


def toggle_shopping(item_id):
    with ASSISTANT_LOCK:
        items = load_list(SHOPPING_FILE)
        target = next((x for x in items if x.get("id") == clean_text(item_id, 100)), None)
        if not target:
            return None
        target["status"] = "done" if target.get("status") != "done" else "open"
        target["updated_at"] = timestamp()
        save_list(SHOPPING_FILE, items)
        return target


def remove_shopping_item(item_id):
    with ASSISTANT_LOCK:
        items = load_list(SHOPPING_FILE)
        target = next((x for x in items if x.get("id") == clean_text(item_id, 100)), None)
        if not target:
            return None
        save_list(SHOPPING_FILE, [x for x in items if x.get("id") != target.get("id")])
        return target


def save_plan(plan_date, items, summary=""):
    day = clean_text(plan_date, 40) or now_ist().date().isoformat()
    if not isinstance(items, list):
        items = [part.strip(" -•") for part in str(items or "").replace("\n", ",").split(",") if part.strip(" -•")]
    normalized = [clean_text(x, 180) for x in items if clean_text(x, 180)]
    plan = {"id": new_id("plan"), "date": day, "items": normalized[:30],
            "summary": clean_text(summary, 400), "updated_at": timestamp()}
    with ASSISTANT_LOCK:
        plans = load_list(PLANS_FILE)
        old = next((p for p in plans if p.get("date") == day), None)
        if old:
            plan["id"] = old.get("id", plan["id"])
            plan["created_at"] = old.get("created_at", timestamp())
            plans = [p for p in plans if p.get("date") != day]
        else:
            plan["created_at"] = timestamp()
        plans.append(plan)
        save_list(PLANS_FILE, plans[-180:])
    return plan


def assistant_dashboard():
    tasks = load_list(TASKS_FILE)
    notes = load_list(NOTES_FILE)
    shopping = load_list(SHOPPING_FILE)
    plans = load_list(PLANS_FILE)
    return {
        "now_ist": timestamp(),
        "tasks": [t for t in tasks if t.get("status") != "done"][-50:],
        "notes": notes[-8:],
        "shopping": shopping[-50:],
        "plans": plans[-8:],
        "memory": memory_context(),
    }


def music_state():
    data = read_json(MUSIC_STATE_FILE, {})
    if not isinstance(data, dict):
        return {}
    try:
        updated = datetime.datetime.fromisoformat(str(data.get("updated_at", "")))
        connected = bool(data.get("client_id")) and (now_ist() - updated).total_seconds() < 45
    except (TypeError, ValueError):
        connected = False
    data["connected"] = connected
    return data


def record_music_state(payload):
    if not isinstance(payload, dict):
        return music_state()
    state = {
        "client_id": clean_text(payload.get("client_id"), 100),
        "device": clean_text(payload.get("device"), 100),
        "track_uri": clean_text(payload.get("track_uri"), 180),
        "title": clean_text(payload.get("title"), 180),
        "artist": clean_text(payload.get("artist"), 240),
        "art": str(payload.get("art") or "")[:500],
        "position_ms": max(0, safe_int(payload.get("position_ms"))),
        "duration_ms": max(0, safe_int(payload.get("duration_ms"))),
        "paused": bool(payload.get("paused", True)),
        "updated_at": timestamp(),
    }
    with MUSIC_LOCK:
        write_json(MUSIC_STATE_FILE, state)
    return state


def issue_music_command(action, payload=None):
    action = clean_text(action, 40).lower().replace(" ", "_")
    allowed = {"play", "pause", "toggle", "next", "previous", "play_song", "play_playlist", "queue_next", "seek"}
    if action not in allowed:
        return {"ok": False, "error": "unsupported music action"}
    state = music_state()
    client_id = clean_text((payload or {}).get("client_id") if isinstance(payload, dict) else "", 100) or state.get("client_id", "")
    if not client_id or not state.get("connected"):
        return {"ok": False, "error": "no phone is connected to music yet"}
    command = {
        "id": new_id("music"), "action": action,
        "query": clean_text((payload or {}).get("query") if isinstance(payload, dict) else "", 200),
        "playlist_id": clean_text((payload or {}).get("playlist_id") if isinstance(payload, dict) else "", 100),
        "position_ms": max(0, safe_int((payload or {}).get("position_ms"))) if isinstance(payload, dict) else 0,
        "target_client_id": client_id, "created_at": timestamp(),
    }
    with MUSIC_LOCK:
        write_json(MUSIC_COMMAND_FILE, command)
    return {"ok": True, "command_id": command["id"], "action": action, "target_device": state.get("device", "")}


def agent_music_control(action, query="", playlist_id="", position_ms=0):
    action = clean_text(action, 40).lower()
    if action in ("song", "play song", "track"):
        action = "play_song"
    if action in ("playlist", "play list"):
        action = "play_playlist"
    if action in ("resume", "start", "start music"):
        action = "play"
    if action in ("stop", "stop music"):
        action = "pause"
    if action in ("skip", "skip song", "next song"):
        action = "next"
    if action in ("replay", "repeat", "play again"):
        action = "seek"
        position_ms = 0
    payload = {"query": query, "playlist_id": playlist_id, "position_ms": position_ms}
    if action == "play_song":
        query = clean_text(query, 200)
        if len(query) < 2:
            return {"ok": False, "error": "song title is required"}
        matches = search_tracks(query, limit=1)
        if not matches:
            return {"ok": False, "error": "no Spotify match found"}
        payload["query"] = matches[0]["uri"]
        result = issue_music_command(action, payload)
        if result.get("ok"):
            result.update({"song": matches[0]["name"], "artist": matches[0]["artist"]})
        return result
    return issue_music_command(action, payload)


def agent_action(action, body):
    """Single deterministic webhook surface for the assistant's safe actions."""
    name = clean_text(action, 50).lower().replace("-", "_").replace(" ", "_")
    if name in ("now_playing", "get_now_playing", "music_state"):
        state = music_state()
        return {"ok": True, "verified": True, "action": "now_playing", "title": state.get("title", ""),
                "artist": state.get("artist", ""), "paused": state.get("paused", True),
                "device": state.get("device", ""), "message": "Live music state returned."}
    if name in ("queue", "get_queue"):
        items = [r for r in load_queue() if r.get("status") != "done"]
        return {"ok": True, "verified": True, "action": "queue", "items": [
            {"song": r.get("name", ""), "artist": r.get("artist", ""), "status": r.get("status", "queued")}
            for r in items[:8]
        ]}
    if name in ("music", "music_control", "spotify"):
        result = agent_music_control(body.get("music_action") or body.get("command") or body.get("action_name"),
                                     body.get("query") or body.get("song"), body.get("playlist_id"), body.get("position_ms", 0))
        result["verified"] = bool(result.get("ok"))
        if result.get("ok"):
            result["message"] = "The command was accepted for the connected browser on the selected device."
        return result
    if name in ("create_task", "task"):
        item = create_task(body.get("title") or body.get("task"), body.get("due") or body.get("reminder"), body.get("priority"), body.get("notes"))
        return {"ok": bool(item), "verified": bool(item), "action": "create_task", "task": item,
                **({} if item else {"error": "task title required"})}
    if name in ("create_note", "note"):
        item = create_note(body.get("title"), body.get("body") or body.get("text"), body.get("tags"))
        return {"ok": bool(item), "verified": bool(item), "action": "create_note", "note": item,
                **({} if item else {"error": "note body required"})}
    if name in ("add_shopping", "shopping"):
        item = add_shopping_item(body.get("item") or body.get("name"), body.get("quantity"), body.get("category"))
        return {"ok": bool(item), "verified": bool(item), "action": "add_shopping", "item": item,
                **({} if item else {"error": "shopping item required"})}
    if name in ("remember_preference", "preference", "remember"):
        preference = clean_text(body.get("preference") or body.get("taste") or body.get("text"), 300)
        if not preference:
            return {"ok": False, "verified": False, "error": "preference required"}
        record_memory({"type": "taste" if body.get("taste") else "preference", "preference": preference, "category": body.get("category")})
        return {"ok": True, "verified": True, "action": "remember_preference", "remembered": preference}
    return {"ok": False, "verified": False, "error": "unsupported assistant action"}


# ---------------------------------------------------------------- email / Nango + Gmail
EMAIL_COOKIE = "rj_email_access"


class GmailNotConnected(RuntimeError):
    pass


def email_configured():
    return bool(NANGO_SECRET_KEY and NANGO_INTEGRATION_ID and current_private_code())


def current_private_code():
    stored = read_json(PRIVATE_CODE_FILE, {})
    if isinstance(stored, dict):
        code = str(stored.get("code") or "").strip()
        if code:
            return code
    return RJSHEETAL_PRIVATE_CODE or DEFAULT_PRIVATE_CODE


def save_private_code(code):
    os.makedirs(DATA_DIR, exist_ok=True)
    write_json(PRIVATE_CODE_FILE, {"code": code, "updated_at": timestamp()})
    try:
        os.chmod(PRIVATE_CODE_FILE, 0o600)
    except OSError:
        pass


def email_cookie_value():
    code = current_private_code()
    return hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""


def request_cookie(handler, name):
    raw = handler.headers.get("Cookie", "")
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def email_authorized(handler):
    expected = email_cookie_value()
    return bool(expected and secrets.compare_digest(request_cookie(handler, EMAIL_COOKIE), expected))


def nango_connection_record():
    data = read_json(NANGO_CONNECTION_FILE, {})
    return data if isinstance(data, dict) else {}


def save_nango_connection(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    write_json(NANGO_CONNECTION_FILE, data)
    try:
        os.chmod(NANGO_CONNECTION_FILE, 0o600)
    except OSError:
        pass


def nango_request(method, path, payload=None, headers=None):
    if not NANGO_SECRET_KEY:
        raise RuntimeError("Nango is not configured")
    url = NANGO_API_BASE + "/" + path.lstrip("/")
    body = None
    request_headers = {"Authorization": "Bearer " + NANGO_SECRET_KEY, "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, method=method.upper(), headers=request_headers)
    with urllib.request.urlopen(request, timeout=18) as response:
        raw = response.read()
        return json.loads(raw or b"{}")


def nango_connections():
    params = urllib.parse.urlencode({"tags[end_user_id]": NANGO_USER_ID, "limit": 20})
    result = nango_request("GET", "/connections?" + params)
    connections = result.get("connections") if isinstance(result, dict) else []
    return connections if isinstance(connections, list) else []


def nango_connection_id():
    stored = nango_connection_record()
    stored_id = str(stored.get("connection_id") or "").strip()
    if stored_id:
        return stored_id
    for connection in nango_connections():
        if not isinstance(connection, dict):
            continue
        provider = str(connection.get("provider_config_key") or connection.get("providerConfigKey") or "").strip()
        connection_id = str(connection.get("connection_id") or connection.get("connectionId") or "").strip()
        if provider == NANGO_INTEGRATION_ID and connection_id:
            save_nango_connection({"connection_id": connection_id, "provider": provider,
                                   "user_id": NANGO_USER_ID, "updated_at": timestamp()})
            return connection_id
    return ""


def email_status(handler):
    if not NANGO_SECRET_KEY or not NANGO_INTEGRATION_ID:
        return {"configured": False, "authorized": email_authorized(handler), "connected": False,
                "email": "", "setup_required": True, "provider": "nango"}
    authorized = email_authorized(handler)
    if not authorized:
        return {"configured": True, "authorized": False, "connected": False,
                "email": "", "setup_required": False, "provider": "nango"}
    try:
        connection_id = nango_connection_id()
    except urllib.error.HTTPError as exc:
        log(f"nango status error: HTTP {exc.code}")
        return {"configured": True, "authorized": True, "connected": False, "email": "",
                "setup_required": False, "provider": "nango",
                "error": "Nango could not check the Gmail connection. Verify the Nango key and scopes."}
    except Exception as exc:
        log(f"nango status error: {exc!r}")
        return {"configured": True, "authorized": True, "connected": False, "email": "",
                "setup_required": False, "provider": "nango",
                "error": "Nango is temporarily unavailable."}
    return {"configured": True, "authorized": True, "connected": bool(connection_id),
            "email": "", "account_count": 1 if connection_id else 0,
            "setup_required": False, "provider": "nango"}


def email_connect_url(handler):
    if not NANGO_SECRET_KEY or not NANGO_INTEGRATION_ID:
        raise RuntimeError("Nango credentials are not configured")
    result = nango_request("POST", "/connect/sessions", {
        "tags": {"end_user_id": NANGO_USER_ID, "end_user_display_name": "Sheetal"},
        "allowed_integrations": [NANGO_INTEGRATION_ID],
    })
    data = result.get("data") if isinstance(result, dict) else {}
    link = str((data or {}).get("connect_link") or "").strip()
    if not link:
        raise RuntimeError("Nango did not return a connect link")
    return link

def normalize_email_messages(raw):
    payload = raw.get("data", raw) if isinstance(raw, dict) else raw
    items = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("emails", "messages", "results", "items"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        if not items:
            nested = payload.get("response_data") or payload.get("result")
            if isinstance(nested, dict):
                return normalize_email_messages(nested)
    output = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        labels = item.get("label_ids") or item.get("labelIds") or item.get("labels") or []
        if not isinstance(labels, list):
            labels = [labels]
        output.append({
            "id": str(item.get("id") or item.get("message_id") or ""),
            "thread_id": str(item.get("thread_id") or item.get("threadId") or ""),
            "from": clean_text(item.get("from") or item.get("sender") or item.get("sender_email"), 240),
            "subject": clean_text(item.get("subject") or "(no subject)", 240),
            "date": clean_text(item.get("date") or item.get("received_at") or "", 100),
            "snippet": clean_text(item.get("snippet") or item.get("preview") or item.get("body") or "", 300),
            "unread": bool(item.get("unread") or item.get("is_unread") or "UNREAD" in labels),
        })
    return output


def gmail_messages(handler, query="", limit=8):
    connection_id = nango_connection_id()
    if not connection_id:
        raise GmailNotConnected("connect Gmail first")
    max_results = max(1, min(safe_int(limit, 8), 20))
    params = {"maxResults": max_results}
    if clean_text(query, 180):
        params["q"] = clean_text(query, 180)
    listing = nango_request("GET", "/proxy/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params, doseq=True),
                            headers={"Provider-Config-Key": NANGO_INTEGRATION_ID, "Connection-Id": connection_id})
    output = []
    for item in (listing.get("messages") or [])[:max_results]:
        message = nango_request("GET", "/proxy/gmail/v1/users/me/messages/" + urllib.parse.quote(str(item.get("id") or "")) + "?" + urllib.parse.urlencode({
            "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"],
        }, doseq=True), headers={"Provider-Config-Key": NANGO_INTEGRATION_ID, "Connection-Id": connection_id})
        headers = {str(x.get("name", "")).lower(): str(x.get("value", "")) for x in (message.get("payload", {}).get("headers") or [])}
        labels = message.get("labelIds") or []
        output.append({
            "id": str(message.get("id") or item.get("id") or ""),
            "thread_id": str(message.get("threadId") or ""),
            "from": clean_text(headers.get("from", ""), 240),
            "subject": clean_text(headers.get("subject", "(no subject)"), 240),
            "date": clean_text(headers.get("date", ""), 100),
            "snippet": clean_text(message.get("snippet", ""), 300),
            "unread": "UNREAD" in labels,
        })
    return output


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
    current = status.get("title") or "nothing is playing yet"
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
                   f"{LISTENER_NAME}, अभी queue में हैं: {names}."

    if any(word in low for word in ("now playing", "playing now", "what song", "what is playing", "what's playing", "current song", "क्या बज")):
        return {"name": "get_now_playing", "result": current}, \
               f"{LISTENER_NAME}, अभी आप सुन रही हैं {current}."

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
                                   f"{LISTENER_NAME}, {track['name']} पहले से queue में है।"
                        if len(q) - sum(1 for r in q if r.get("status") == "done") >= MAX_QUEUE:
                            return {"name": "request_song", "result": "queue full"}, \
                                   f"{LISTENER_NAME}, queue अभी full है — थोड़ी देर बाद फिर try कीजिए।"
                        item = {
                            "id": base64.b64encode(os.urandom(6)).decode().replace("+", "").replace("/", ""),
                            "uri": track["uri"], "name": track["name"], "artist": track["artist"],
                            "album": track["album"], "art": track["art"], "dur_ms": track["dur_ms"],
                            "ts": int(time.time()), "status": "queued",
                        }
                        q.append(item)
                        save_queue(q)
                    return {"name": "request_song", "result": track["name"]}, \
                           f"{LISTENER_NAME}, done — {track['name']} by {track['artist']} मैंने queue में डाल दिया है।"
        return {"name": "search_song", "result": "no match"}, \
               f"{LISTENER_NAME}, मुझे वह song नहीं मिला। Search box से एक बार फिर try कीजिए।"

    return {"name": "station_help", "result": "available: now playing, queue, request a song"}, \
           f"{LISTENER_NAME}, मैं आपका personal assistant हूँ। आप समय, अभी क्या बज रहा है, queue, mood, या किसी गाने के बारे में पूछ सकती हैं।"


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


def agent_set_mood(mood):
    """Turn a plain-language mood into a few Spotify queue entries."""
    raw = str(mood or "").strip().lower()[:80]
    key = next((k for k in MOOD_QUERIES if k in raw), raw)
    query = MOOD_QUERIES.get(key)
    if not query:
        return {"ok": False, "error": "mood not recognised", "supported": sorted(MOOD_QUERIES)}
    try:
        tracks = search_tracks(query, limit=3)
    except Exception as exc:
        log(f"mood search error: {exc!r}")
        return {"ok": False, "error": "Spotify mood search unavailable"}
    if not tracks:
        return {"ok": False, "error": "no Spotify tracks found for that mood"}
    added = []
    with QUEUE_LOCK:
        q = load_queue()
        active = {r.get("uri") for r in q if r.get("status") != "done"}
        for track in tracks:
            if len(active) >= MAX_QUEUE:
                break
            if not track.get("uri") or track["uri"] in active:
                continue
            item = {
                "id": base64.b64encode(os.urandom(6)).decode().replace("+", "").replace("/", ""),
                "uri": track["uri"], "name": track["name"], "artist": track["artist"],
                "album": track["album"], "art": track["art"], "dur_ms": track["dur_ms"],
                "ts": int(time.time()), "status": "queued", "source": "mood",
            }
            q.append(item)
            active.add(track["uri"])
            added.append({"song": track["name"], "artist": track["artist"]})
        save_queue(q)
    record_memory({"type": "mood", "mood": key, "text": f"Sheetal asked for {key} music"})
    return {"ok": True, "mood": key, "query": query, "queued": added}


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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authed(self):
        if not SHARED_TOKEN:
            return True
        return (self.headers.get("X-RJ-Token") or "") == SHARED_TOKEN

    def _agent_authed(self):
        if not AGENT_TOKEN:
            return False
        supplied = self.headers.get("X-RJ-Agent-Token") or self.headers.get("X-RJ-Token") or ""
        return secrets.compare_digest(supplied, AGENT_TOKEN)

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
        elif path == "/redesign.css":
            self._serve_file("redesign.css", "text/css; charset=utf-8")
        elif path == "/rebuild.css":
            self._serve_file("rebuild.css", "text/css; charset=utf-8")
        elif path == "/rj-lazy.js":
            self._serve_file("rj-lazy.js", "application/javascript; charset=utf-8")
        elif path == "/spotify-personal.js":
            self._serve_file("spotify-personal.js", "application/javascript; charset=utf-8")
        elif path == "/assistant-life.js":
            self._serve_file("assistant-life.js", "application/javascript; charset=utf-8")
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
        elif path == "/api/assistant/dashboard":
            self._json(200, assistant_dashboard())
        elif path == "/api/email/status":
            self._json(200, email_status(self))
        elif path == "/api/email/connect":
            if not email_configured():
                self._json(503, {"ok": False, "error": "Gmail integration is not configured yet"})
            elif not email_authorized(self):
                self._json(403, {"ok": False, "error": "unlock the personal assistant first"})
            else:
                try:
                    self._redirect(email_connect_url(self))
                except Exception as exc:
                    log(f"nango connect error: {exc!r}")
                    error = "Gmail connection is temporarily unavailable"
                    if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
                        error = "Nango rejected the configured secret key. Update NANGO_SECRET_KEY in Coolify."
                    self._json(502, {"ok": False, "error": error})
        elif path == "/api/email/callback":
            # Nango Connect completes the provider OAuth flow in its hosted UI.
            self._redirect("/?email=connected")
        elif path == "/api/email/inbox":
            if not email_authorized(self):
                self._json(403, {"ok": False, "error": "unlock the personal assistant first"})
                return
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            query = params.get("q", [""])[0]
            limit = params.get("limit", ["8"])[0]
            try:
                self._json(200, {"ok": True, "connected": True, "email": "",
                                 "messages": gmail_messages(self, query, limit)})
            except GmailNotConnected:
                self._json(409, {"ok": False, "connected": False, "error": "connect Gmail first"})
            except Exception as exc:
                log(f"gmail inbox error: {exc!r}")
                self._json(502, {"ok": False, "error": "Gmail is temporarily unavailable."})
        elif path == "/api/tasks":
            self._json(200, {"tasks": load_list(TASKS_FILE)})
        elif path == "/api/notes":
            self._json(200, {"notes": load_list(NOTES_FILE)})
        elif path == "/api/shopping":
            self._json(200, {"shopping": load_list(SHOPPING_FILE)})
        elif path == "/api/plans":
            self._json(200, {"plans": load_list(PLANS_FILE)})
        elif path == "/api/music/state":
            self._json(200, music_state())
        elif path == "/api/music/command":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            client_id = params.get("client_id", [""])[0]
            last_id = params.get("after", [""])[0]
            command = read_json(MUSIC_COMMAND_FILE, {})
            if not isinstance(command, dict) or command.get("id") == last_id or command.get("target_client_id") != client_id:
                self._json(200, {"command": None})
            else:
                self._json(200, {"command": command})
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
            st = music_state()
            self._json(200, {"station": "Sheetal Assistant", "now_playing": st.get("title", ""), "artist": st.get("artist", ""), "paused": st.get("paused", True), "device": st.get("device", ""), "on_air": bool(st.get("title") and not st.get("paused", True))})
        elif path == "/api/agent/queue":
            items = [r for r in load_queue() if r.get("status") != "done"]
            self._json(200, {"station": STATION, "queue": [
                {"song": r.get("name", ""), "artist": r.get("artist", ""), "status": r.get("status", "queued")}
                for r in items[:8]
            ]})
        elif path == "/api/agent/tasks":
            self._json(200, {"tasks": [t for t in load_list(TASKS_FILE) if t.get("status") != "done"]})
        elif path == "/api/agent/notes":
            self._json(200, {"notes": load_list(NOTES_FILE)[-20:]})
        elif path == "/api/agent/shopping":
            self._json(200, {"shopping": [x for x in load_list(SHOPPING_FILE) if x.get("status") != "done"]})
        elif path == "/api/agent/memory":
            self._json(200, memory_context())
        elif path == "/api/agent/music-state":
            self._json(200, music_state())
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
        elif path == "/api/email/unlock":
            supplied = str(body.get("code") or "").strip()
            expected = current_private_code()
            if not expected:
                self._json(503, {"ok": False, "error": "private assistant access is not configured"})
                return
            if not supplied or not secrets.compare_digest(supplied, expected):
                self._json(403, {"ok": False, "error": "that access code is not correct"})
                return
            self._send(200, json.dumps({"ok": True}), extra={
                "Set-Cookie": f"{EMAIL_COOKIE}={email_cookie_value()}; Max-Age=2592000; Path=/; Secure; HttpOnly; SameSite=Lax",
            })
        elif path == "/api/email/change-pin":
            if not email_authorized(self):
                self._json(403, {"ok": False, "error": "unlock the personal assistant first"})
                return
            new_pin = str(body.get("pin") or body.get("code") or "").strip()
            if not new_pin.isdigit() or not 4 <= len(new_pin) <= 12:
                self._json(400, {"ok": False, "error": "PIN must contain 4 to 12 digits"})
                return
            save_private_code(new_pin)
            self._send(200, json.dumps({"ok": True, "message": "Private PIN changed"}), extra={
                "Set-Cookie": f"{EMAIL_COOKIE}={email_cookie_value()}; Max-Age=2592000; Path=/; Secure; HttpOnly; SameSite=Lax",
            })
        elif path == "/api/email/disconnect":
            if not email_authorized(self):
                self._json(403, {"ok": False, "error": "unlock the personal assistant first"})
                return
            try:
                os.remove(NANGO_CONNECTION_FILE)
            except FileNotFoundError:
                pass
            self._send(200, json.dumps({"ok": True, "message": "Local Gmail session cleared"}), extra={
                "Set-Cookie": f"{EMAIL_COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax",
            })
        elif path == "/api/memory/event":
            event = body if isinstance(body, dict) else {}
            if event.get("type") not in ("play", "skip", "replay", "request", "conversation", "mood", "preference", "taste"):
                self._json(400, {"error": "unsupported memory event"})
                return
            try:
                record_memory(event)
                self._json(200, {"ok": True})
            except Exception as e:
                log(f"memory error: {e!r}")
                self._json(500, {"error": "memory unavailable"})
        elif path == "/api/tasks":
            item = create_task(body.get("title"), body.get("due"), body.get("priority"), body.get("notes"))
            self._json(201 if item else 400, {"ok": bool(item), "task": item, **({} if item else {"error": "task title required"})})
        elif path == "/api/tasks/complete":
            item = complete_task(body.get("id"), body.get("title"))
            self._json(200 if item else 404, {"ok": bool(item), "task": item})
        elif path == "/api/tasks/remove":
            item = remove_task(body.get("id"), body.get("title"))
            self._json(200 if item else 404, {"ok": bool(item), "task": item})
        elif path == "/api/notes":
            item = create_note(body.get("title"), body.get("body"), body.get("tags"))
            self._json(201 if item else 400, {"ok": bool(item), "note": item, **({} if item else {"error": "note body required"})})
        elif path == "/api/notes/remove":
            item = remove_note(body.get("id"))
            self._json(200 if item else 404, {"ok": bool(item), "note": item})
        elif path == "/api/shopping":
            item = add_shopping_item(body.get("item"), body.get("quantity"), body.get("category"))
            self._json(201 if item else 400, {"ok": bool(item), "item": item, **({} if item else {"error": "shopping item required"})})
        elif path == "/api/shopping/toggle":
            item = toggle_shopping(body.get("id"))
            self._json(200 if item else 404, {"ok": bool(item), "item": item})
        elif path == "/api/shopping/remove":
            item = remove_shopping_item(body.get("id"))
            self._json(200 if item else 404, {"ok": bool(item), "item": item})
        elif path == "/api/plans":
            item = save_plan(body.get("date"), body.get("items", body.get("plan", [])), body.get("summary"))
            self._json(201, {"ok": True, "plan": item})
        elif path == "/api/music/state":
            self._json(200, {"ok": True, "state": record_music_state(body)})
        elif path == "/api/music/command-result":
            self._json(200, {"ok": True, "received": clean_text(body.get("command_id"), 100)})
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
        elif path == "/api/agent/mood":
            ok, msg = ok_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            self._json(200, agent_set_mood(body.get("mood") or body.get("feeling") or ""))
        elif path == "/api/agent/create-task":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            item = create_task(body.get("title") or body.get("task"), body.get("due") or body.get("reminder"), body.get("priority"), body.get("notes"))
            self._json(200, {"ok": bool(item), "task": item, **({} if item else {"error": "task title required"})})
        elif path == "/api/agent/complete-task":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            item = complete_task(body.get("id"), body.get("title") or body.get("task"))
            self._json(200 if item else 404, {"ok": bool(item), "task": item, **({} if item else {"error": "open task not found"})})
        elif path == "/api/agent/create-note":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            item = create_note(body.get("title"), body.get("body") or body.get("text"), body.get("tags"))
            self._json(200, {"ok": bool(item), "note": item, **({} if item else {"error": "note body required"})})
        elif path == "/api/agent/add-shopping":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            item = add_shopping_item(body.get("item") or body.get("name"), body.get("quantity"), body.get("category"))
            self._json(200, {"ok": bool(item), "item": item, **({} if item else {"error": "shopping item required"})})
        elif path == "/api/agent/plan-day":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            item = save_plan(body.get("date"), body.get("items", body.get("plan", [])), body.get("summary"))
            self._json(200, {"ok": True, "plan": item})
        elif path == "/api/agent/remember-preference":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            preference = clean_text(body.get("preference") or body.get("taste") or body.get("text"), 300)
            if not preference:
                self._json(400, {"ok": False, "error": "preference required"})
                return
            record_memory({"type": "taste" if body.get("taste") else "preference", "preference": preference, "category": body.get("category")})
            self._json(200, {"ok": True, "remembered": preference})
        elif path == "/api/agent/music-control":
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            self._json(200, agent_music_control(body.get("action"), body.get("query"), body.get("playlist_id"), body.get("position_ms", 0)))
        elif path == "/api/agent/action":
            if not self._agent_authed():
                self._json(403, {"ok": False, "verified": False, "error": "assistant action is not authorized"})
                return
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "verified": False, "error": msg})
                return
            result = agent_action(body.get("action") or body.get("name"), body)
            self._json(200, result)
        elif path == "/api/agent/email-inbox":
            if not self._agent_authed():
                self._json(403, {"ok": False, "error": "email assistant tool is not authorized"})
                return
            ok, msg = ok_agent_request(self._client_ip())
            if not ok:
                self._json(429, {"ok": False, "error": msg})
                return
            query = body.get("q") or body.get("query") or ""
            try:
                self._json(200, {"ok": True, "connected": True, "email": "",
                                 "messages": gmail_messages(self, query, body.get("limit", 8))})
            except GmailNotConnected:
                self._json(409, {"ok": False, "connected": False, "error": "Sheetal needs to connect Gmail first"})
            except Exception as exc:
                log(f"agent Nango Gmail error: {exc!r}")
                self._json(502, {"ok": False, "error": "Gmail is temporarily unavailable."})
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
                requested_uri = str(body.get("next_uri") or "")
                next_item = next((item for item in q if item.get("status") == "queued" and item.get("uri") == requested_uri), None) if requested_uri else None
                if not next_item:
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
    # The browser now starts from the selected Spotify playlist at a random
    # position. Do not seed a hard-coded opening song into the persistent queue.
    with QUEUE_LOCK:
        queue = load_queue()
        cleaned = [item for item in queue if item.get("source") != "station-default"]
        if len(cleaned) != len(queue):
            save_queue(cleaned)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"rj.sheetal on 0.0.0.0:{PORT}  (token {'set' if SHARED_TOKEN else 'UNSET'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
