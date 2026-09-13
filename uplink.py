#!/usr/bin/env python3
"""Home-side relay for RJ Sheetal.

Streams the local radio's mp3 (http://localhost:8001/stream) up to the
VPS /ingest endpoint, and reports now-playing metadata.  No port
forwarding is needed on the home router — the VPS re-broadcasts the
stream to internet listeners.

Usage:
    RJSHEETAL_URL=https://rj.vishalojha.me \
    RJSHEETAL_TOKEN=... \
    python3 uplink.py

Options (env):
    RJSHEETAL_HOME_URL  local stream to relay (default http://localhost:8001/stream)
    RJSHEETAL_API_URL   local status source (default http://localhost:8001/api)
"""
import http.client
import json
import os
import ssl
import sys
import time
import urllib.request

from rjlink import cfg, _open, log

HOME_URL = os.environ.get("RJSHEETAL_HOME_URL", "http://localhost:8001/stream")
HOME_API = os.environ.get("RJSHEETAL_API_URL", "http://localhost:8001/api")
CHUNK = 32 * 1024
RECONNECT_S = 3
META_EVERY_S = 3


def current_title():
    try:
        with urllib.request.urlopen(HOME_API, timeout=5) as r:
            return (json.loads(r.read()) or {}).get("title", "")
    except Exception:
        return None


def push_ingest(url):
    """Open POST /ingest and stream audio bytes until it dies; returns."""
    parts = url.replace("https://", "").replace("http://", "").split("/", 1)
    host, port, tls = parts[0], 443, True
    if url.startswith("http://"):
        port = 80
        tls = False
    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)
    conn = http.client.HTTPSConnection(host, port) if tls else http.client.HTTPConnection(host, port)
    headers = {"Content-Type": "audio/mpeg", "Connection": "keep-alive"}
    if cfg.token:
        headers["X-RJ-Token"] = cfg.token
    conn.putrequest("POST", "/ingest", skip_accept_encoding=True)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()
    src = urllib.request.urlopen(HOME_URL, timeout=10)
    try:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            conn.send(chunk)
    finally:
        try:
            src.close()
        except Exception:
            pass
    try:
        conn.close()
    except Exception:
        pass


def meta_loop():
    last = ""
    while True:
        title = current_title()
        if title and title != last:
            last = title
            r = send_meta(title)
            log(f"meta: {title!r} -> {r}")
        time.sleep(META_EVERY_S)


def send_meta(title):
    if not cfg.url:
        return {"error": "no url"}
    return _open("/api/metadata", {"title": title}, timeout=6)


def main():
    if not cfg.url:
        log("RJSHEETAL_URL not set — run with RJSHEETAL_URL/RJSHEETAL_TOKEN env or rjlink_creds.py")
        return 1
    log(f"uplink to {cfg.url}  token={'set' if cfg.token else 'UNSET'}")
    import threading
    threading.Thread(target=meta_loop, daemon=True).start()
    while True:
        try:
            log(f"relaying {HOME_URL} -> {cfg.url}/ingest")
            push_ingest(cfg.url)
        except Exception as e:
            log(f"push stopped: {e!r}")
        time.sleep(RECONNECT_S)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass