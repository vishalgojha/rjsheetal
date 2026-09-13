# RJ Sheetal — Live Radio

Mobile-first radio station at **rj.vishalojha.me**. Listeners stream your live show
and request any song from the Spotify catalog. The installable PWA keeps the live
player available in the background, with lock-screen play/pause controls and a
voice-first RJ panel for station commands. Runs on a Hetzner VPS via **Coolify**
(Docker + Traefik + Let's Encrypt, zero config).

```
listener ──► rj.vishalojha.me (Hetzner VPS, Coolify)
                 │  /            mobile site (this repo, served by site.py)
                 ├─ /api/stream  live MP3 relay from home
                 ├─ /api/search  Spotify catalog search   (client-credentials)
                 ├─ /api/request listener song requests  → queue
                 └─ /api/rj      RJ voice + station tools → ElevenLabs TTS
                 └─ /api/pending home DJ polls & claims requests
                          ▲
                          │  https (outbound only — no port-forwarding needed)
                        home PC  (dj/uplink.py → dj/autodj.py → veena radio)
```

## 1. VPS (Hetzner)

1. Create a Hetzner CX22+ Ubuntu 24.04 server. Note its public IP.
2. In your DNS (any provider), point the domain at it:
   ```
   rj.vishalojha.me   A    <vps-public-ip>
   ```
3. Open **UDP 443** in the Hetzner firewall? No — only TCP is needed; open `TCP 80, 443`.
4. Install Coolify on the VPS (its installer brings Docker + Traefik):
   ```bash
   ssh root@<vps-ip>
   curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
   ```
   Finish the web setup at `http://<vps-ip>:8000` (or the printed URL).

## 2. Add the app in Coolify

1. **Databases → Create** (or skip; we use a volume, no DB).
2. **New resource → Public Repository** → paste `https://github.com/vishalgojha/rjsheetal.git`
   (branch `main`). Coolify builds from the included `Dockerfile`.
3. Set these **Environment Variables**:
   | key | value |
   |---|---|
   | `SPOTIFY_CLIENT_ID` | your Spotify app client id |
   | `SPOTIFY_CLIENT_SECRET` | your Spotify app client secret |
   | `RJSHEETAL_TOKEN` | a long random shared secret (used by the home DJ) |
4. **Storage → Add a volume**: mount `/data` (holds the request queue — survives restarts).
5. **Domains → Add** advanced: `rj.vishalojha.me` and enable **HTTPS (Letsencrypt)**.
6. Coolify auto-sets `PORT` — the app listens on whatever Coolify injects.

> Spotify app: create one free at https://developer.spotify.com/dashboard → App → copy
> Client ID & Secret. No redirect URI needed (we only use client-credentials + API).

## 3. Home PC (the DJ machine)

```bash
cd ~/"Documents/Default Project/dj"

# 1) drop the two link modules next to autodj/ui/radio
cp ../../rjsheetal/rjlink.py ../../rjsheetal/uplink.py .

# 2) tell the DJ where the public site lives
cat > rjlink_creds.py <<'EOF'
RJSHEETAL_URL   = "https://rj.vishalojha.me"
RJSHEETAL_TOKEN = "<the RJSHEETAL_TOKEN you set in Coolify>"
EOF

# 3) run the audio upload relay (keeps stream + now-playing synced)
python3 uplink.py          # Ctrl+C stops it

# 4) restart the auto-DJ so it picks up audience requests
#    autodj now: fetches /api/pending, plays requests before pool tracks,
#    and reports them back as "on air" so the public queue clears.
```

Run `uplink.py` under systemd or a terminal/tmux on the DJ machine. It only needs
**outbound** HTTPS to the VPS — no router ports, no static IP required.

## 4. Public API (used by the site)

| method | path | auth | purpose |
|---|---|---|---|
| GET | `/api/status` | – | on-air, now-playing title, listeners, queue length |
| GET | `/api/search?q=` | – | Spotify track search (6 results) |
| POST | `/api/request` `{uri}` | – | add a track to the request queue (rate-limited) |
| GET | `/api/queue` | – | full request queue |
| POST | `/ingest` | token | home → VPS audio relay stream |
| POST | `/api/metadata` `{title}` | token | now-playing title from home |
| GET | `/api/pending` | token | home DJ poll for new requests |
| POST | `/api/claim` `{id}` | token | home claims a request (shows "ON AIR") |
| POST | `/api/done` `{id}` | token | home marks a request played (removes it) |
| GET | `/api/stream` | – | live MP3 for the player |
| GET | `/healthz` | – | Coolify health check |

## Local dev / testing without the flow meter

```bash
PORT=8080 RJSHEETAL_TOKEN=dev RJSHEETAL_DATA=/tmp/rjdata \
SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... python3 site.py

curl localhost:8080/healthz
curl "localhost:8080/api/search?q=chaiyya"
curl -X POST localhost:8080/api/request -H 'Content-Type: application/json' \
     -d '{"uri":"spotify:track:5H4rKylLnO8KrmdXTRhj5s"}'
```
