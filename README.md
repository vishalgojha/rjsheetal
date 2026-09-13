# Sheetal Personal Assistant

Sheetal’s private assistant at **https://rj.vishalojha.me**. The assistant is
the primary experience: it can remember useful preferences, keep tasks and
notes, manage a shopping list, make a day plan, answer questions, and control
Spotify when Sheetal asks. Spotify is an optional control layer, not the
product’s main screen.

## Deploy with Coolify

Create a public-repository application from this repository and set:

| key | purpose |
|---|---|
| `SPOTIFY_CLIENT_ID` | Spotify app client id |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIFY_PLAYLIST_ID` | Optional starting playlist |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS/assistant integration |
| `ELEVENLABS_AGENT_ID` | Optional agent id override |

Add a persistent Coolify volume mounted at `/data`. This stores Sheetal’s
tasks, notes, shopping list, plans, preferences, music state, and request
history across redeployments. Add the exact Spotify redirect URI
`https://rj.vishalojha.me/` in the Spotify developer dashboard.

## Phone setup

Sheetal connects her own Spotify Premium account once on each phone. The
browser uses PKCE, so the Spotify client secret never reaches the phone.

1. Open the site in Safari on iPhone or Chrome on Android.
2. Add it to the Home Screen if desired.
3. Tap **Connect Spotify** and finish Spotify sign-in.
4. Ask the assistant to play, pause, resume, skip, replay, choose a song, or
   choose a playlist.

Only one Spotify device can play for the account at a time. The main screen
shows a compact “Playing on” handoff when more than one phone/browser is
connected. Keep the browser/PWA open for reliable web playback; locked or
suspended mobile browsers can stop Web Playback. Typed assistant chat remains
available when microphone access is unavailable.

## Assistant capabilities

The ElevenLabs agent uses webhook tools backed by this app to:

- create, list, and complete tasks;
- save and read notes;
- add and read shopping items;
- save a simple day plan in IST;
- remember explicit preferences and taste signals;
- read music state and control Spotify on the active browser;
- use the attached Rekhta knowledge base when a relevant reflective thought
  genuinely fits.

These are persistent in-app records. They are not phone notifications or
external bookings unless a separate integration is added.

## Local development

```bash
PORT=8080 RJSHEETAL_DATA=/tmp/rjdata \
SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... python3 site.py

curl localhost:8080/healthz
```

The application is intentionally a small Python HTTP service with static
browser assets, so it can run directly in Coolify without a database.
