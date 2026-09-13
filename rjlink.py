"""Home <-> VPS link for RJ Sheetal.

Shared by dj/uplink.py (audio relay + metadata) and dj/autodj.py
(request pickup).  Talks to the public rjsheetal backend.

Configuration (env vars, or a local rjlink_creds.py in the same dir):

    RJSHEETAL_URL    e.g. https://rj.vishalojha.me   (no trailing slash)
    RJSHEETAL_TOKEN  the shared X-RJ-Token set on the VPS

Relay-only processes (uplink) need the token; autodj also uses it for
request claim/done.  If URL/token are missing the module reports
"link down" and every call is a no-op, so the radio never crashes.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


class CFG:
    url = ""
    token = ""

    def load(self):
        url = os.environ.get("RJSHEETAL_URL", "")
        token = os.environ.get("RJSHEETAL_TOKEN", "")
        try:
            import rjlink_creds as c
            url = url or getattr(c, "RJSHEETAL_URL", "")
            token = token or getattr(c, "RJSHEETAL_TOKEN", "")
        except ImportError:
            pass
        self.url = url.rstrip("/")
        self.token = token
        return bool(self.url)


cfg = CFG()
cfg.load()


def _open(path, data=None, timeout=10):
    if not cfg.url:
        return None
    headers = {}
    if cfg.token:
        headers["X-RJ-Token"] = cfg.token
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(cfg.url + path, data=body, headers=headers)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"error": f"http {e.code}"}
    except Exception as e:
        return {"error": repr(e)}


def send_metadata(title):
    return _open("/api/metadata", {"title": title or ""})


def fetch_pending():
    return _open("/api/pending")


def claim(rid):
    return _open("/api/claim", {"id": rid})


def done(rid, uri):
    return _open("/api/done", {"id": rid, "uri": uri})


def link_status():
    return "up" if cfg.url else "down"


def log(msg):
    print(f"[rjlink] {msg}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    print(f"url={cfg.url or '(unset)'} token={'set' if cfg.token else '(unset)'} status={link_status()}")
    if cfg.url:
        s = _open("/api/status")
        print("status:", json.dumps(s, ensure_ascii=False))