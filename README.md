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
| `COMPOSIO_API_KEY` | Composio project API key for the managed Gmail connection |
| `COMPOSIO_USER_ID` | Stable Composio user id; use `sheetal` |
| `COMPOSIO_CALLBACK_URL` | `https://rj.vishalojha.me/api/email/callback` |
| `RJSHEETAL_PRIVATE_CODE` | Optional starter PIN; defaults to `0000000` and can be changed in the app |
| `RJSHEETAL_AGENT_TOKEN` | Optional secret for a protected ElevenLabs Gmail tool |

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

### Gmail setup

Gmail is an optional, read-only personal assistant capability. Create a
Composio project/API key, enable the Gmail toolkit, and set the Composio
variables above in Coolify. The callback URL must be exactly
`https://rj.vishalojha.me/api/email/callback`. On her phone, Sheetal starts
with the temporary PIN `0000000`, taps **Connect Gmail**, and completes the
hosted Google consent flow. After unlocking, she can tap **Change private PIN**
to choose her own 4–12 digit PIN. The changed PIN is persisted in `/data` and
survives redeployments.

The app creates a restricted Composio session with the read-only
`GMAIL_FETCH_EMAILS` tool. No Google client secret is needed in Coolify.

The first version only reads message metadata and snippets. Sending, replying,
archiving, deleting, and calendar changes should be added later with explicit
confirmation in the assistant.

For an ElevenLabs custom tool, call `POST /api/agent/email-inbox` with
`X-RJ-Agent-Token` and a JSON body such as `{"query":"is:unread","limit":8}`.
The endpoint is protected separately from the mobile browser cookie and reads
through the same Composio-managed Gmail connection.

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
- read a protected Gmail inbox and search it with Gmail query syntax after
  Sheetal explicitly connects her account;

The agent can also use the protected `POST /api/agent/action` router for
verified music state, queue, tasks, notes, shopping, and preference actions.
Mobile app handoffs use the ElevenLabs client tool `open_external_app`: the
PWA prepares a Spotify, WhatsApp, phone, or Maps action and Sheetal taps the
visible button. The assistant must never claim an app opened or a message was
sent until the phone confirms it.

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
