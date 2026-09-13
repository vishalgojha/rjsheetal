# ElevenLabs agent tools

The app has two different tool paths:

1. `open_external_app` is an ElevenLabs **client tool**. It runs in the PWA
   on Sheetal's phone and creates a visible, user-tapped handoff to Spotify,
   WhatsApp, the phone dialer, or Maps.
2. `assistant_action` is an ElevenLabs **webhook tool**. It calls the protected
   app endpoint for verified assistant data and actions.

## Client tool: `open_external_app`

Create a client tool with this exact name and enable “wait for response”. The
PWA already registers the handler with `Conversation.startSession`.

Description:

> Prepare an action on Sheetal’s phone. Use this when Sheetal asks to open a
> browser tab, open Spotify, open WhatsApp, call a phone number, or open a place
> in Maps.
> This tool only prepares a visible button on Sheetal's phone. Never claim the
> app opened, a call started, or a message was sent until Sheetal taps and
> confirms it.

Parameters:

| Name | Type | Required | Description |
|---|---|---:|---|
| `app` | string | yes | `browser`, `spotify`, `whatsapp`, `phone`, or `maps` |
| `target` | string | no | Spotify song/artist/playlist, Maps place, or an app URL |
| `text` | string | no | WhatsApp message draft or search text |
| `phone` | string | no | Phone number including country code when needed |

For WhatsApp, the tool prepares a draft only. Sheetal must review and press
Send herself.

## Webhook tool: `assistant_action`

Create a webhook tool with:

- Method: `POST`
- URL: `https://rj.vishalojha.me/api/agent/action`
- Header: `X-RJ-Agent-Token` as a secret containing the same value as
  `RJSHEETAL_AGENT_TOKEN` in Coolify
- Response timeout: 20 seconds

Description:

> Perform or read a verified safe assistant action for Sheetal. Use this for
> tasks, notes, shopping, preferences, current music state, queue, or Spotify
> control. Call it before saying an action succeeded. If it returns `ok:false`,
> explain the returned error and do not pretend the action happened. For
> Spotify music commands, provide `action: music` and put the command in
> `music_action`.

Request body schema:

```json
{
  "type": "object",
  "required": ["action"],
  "properties": {
    "action": {
      "type": "string",
      "enum": [
        "now_playing", "queue", "music", "create_task", "create_note",
        "add_shopping", "remember_preference"
      ],
      "description": "The safe assistant operation to perform."
    },
    "music_action": { "type": "string", "description": "play, pause, toggle, next, previous, play_song, play_playlist, or seek" },
    "query": { "type": "string", "description": "Song title or artist for play_song" },
    "playlist_id": { "type": "string", "description": "Spotify playlist id when selecting a playlist" },
    "position_ms": { "type": "integer", "description": "Seek position in milliseconds" },
    "title": { "type": "string", "description": "Task or note title" },
    "task": { "type": "string", "description": "Task title" },
    "due": { "type": "string", "description": "Due date or reminder wording in IST" },
    "priority": { "type": "string", "description": "low, normal, or high" },
    "notes": { "type": "string", "description": "Optional task notes" },
    "body": { "type": "string", "description": "Note body" },
    "tags": { "type": "string", "description": "Optional note tags" },
    "item": { "type": "string", "description": "Shopping item" },
    "quantity": { "type": "string", "description": "Optional quantity" },
    "category": { "type": "string", "description": "Optional shopping or preference category" },
    "preference": { "type": "string", "description": "Preference to remember" }
  }
}
```

## Agent prompt addition

Add this to the agent's system prompt:

> You are Sheetal's practical personal assistant. Use tools instead of
> claiming you performed an action. Use `assistant_action` for tasks, notes,
> shopping, preferences, current music, queue, and Spotify commands. Use
> `open_external_app` for phone app handoffs. A phone handoff is only prepared
> until Sheetal taps the visible button. For WhatsApp, prepare a draft but
> never send it. Keep confirmations short and only ask for confirmation before
> sending a message, placing a call, making a purchase, deleting data, or
> changing an account. If a tool returns an error, say what is unavailable and
> offer the next useful step.
