# Specification: nextcloud-talk-agent (Python Client & Hermes Platform Plugin)

## 1. Overview
`nextcloud-talk-agent` connects autonomous AI agents (specifically Hermes Agent as a native platform adapter) to **Nextcloud Talk** as full-fledged service users.

Service users operate via Nextcloud Talk's native client APIs and WebDAV, enabling:
- Real-time event reception (Dynamic Room Discovery + Long-Polling `lookIntoFuture=1` and Signaling Server WebSockets support).
- Full conversation context extraction ($N$ previous messages from the room or thread).
- Native audio/voice message attachments (TTS voice notes rendered as audio players in Talk).
- File upload and sharing (contracts, documents, PDFs).
- Typing notifications ("Agent is typing...").
- Hermes Agent Platform Plugin (`kind: platform`, `BasePlatformAdapter`) for zero-daemon, in-process runtime execution.

---

## 2. Architecture & Core Components

```
nextcloud_talk_agent/
├── __init__.py
├── client.py        # Nextcloud Talk API & WebDAV wrapper
├── listener.py      # Event loop listener with Dynamic Room Discovery (auto-detects new 1-on-1 and group rooms)
├── context.py       # Conversation history assembler & formatter for LLMs
├── media.py         # Voice note and file attachment helper (WebDAV upload + Talk share)
├── agent.py         # High-level TalkAgent base class with event callbacks
├── models.py        # Pydantic models (Message, Room, Participant, Attachment)
└── hermes_plugin/   # Hermes Native Platform Plugin
    ├── __init__.py
    ├── plugin.yaml  # Hermes platform plugin definition
    └── adapter.py   # NextcloudTalkAdapter(BasePlatformAdapter) implementation
```

---

## 3. Technical Requirements

### 3.1 Authentication
- Connects using Nextcloud Server URL, Username, and App Password (or service token).
- Headers: `OCS-APIRequest: true`, `Accept: application/json`, Basic or Bearer auth.

### 3.2 Event Listening & Dynamic Room Discovery (CRITICAL FIX)
- The listener MUST dynamically poll `/ocs/v2.php/apps/spreed/api/v4/room` periodically (e.g. every 10-15s) in the background.
- When any user starts a new 1-to-1 direct conversation or adds the agent to a room:
  - The new room token MUST be automatically spawned into a polling task without restarting the listener.
- Dynamic room discovery ensures 100% reception across all direct messages and mentions.

### 3.3 Hermes Platform Plugin Adapter (`BasePlatformAdapter`)
- Implements Hermes `BasePlatformAdapter` interface:
  - `start()`, `stop()`, `send()`, `reply()`
  - Maps Nextcloud Talk incoming messages to Hermes `MessageEvent`.
  - Enforces `allowed_users` whitelist (e.g. only responding to `eduardocintas` / authorized admin).
  - Runs in-process inside Hermes Gateway (`hermes gateway run`) with ZERO external systemd daemons.

### 3.4 Media, Voice Notes & Typing
- Uploads audio/attachments via WebDAV (`/remote.php/dav/files/{user}/Talk/{filename}`).
- Typing state toggling during agent inference.

---

## 4. Deliverables
1. Fix `nextcloud_talk_agent/listener.py` with continuous background room discovery.
2. Implement `nextcloud_talk_agent/hermes_plugin/` (`plugin.yaml` + `adapter.py`).
3. Unit tests validating dynamic room discovery and adapter lifecycle.
4. Conventional git commit under `edu <eduardocintas@gmail.com>`.
