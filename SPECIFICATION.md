# Specification: nextcloud-talk-agent (Python Client & Agent Framework)

## 1. Overview
`nextcloud-talk-agent` is a standalone Python library and framework designed to connect autonomous AI agents (such as Hermes Agent, OpenClaw, LangChain, or custom daemons) to **Nextcloud Talk** as full-fledged service users.

Unlike webhook bots (which are isolated, receive no conversation context, and cannot attach files), service user agents operate via Nextcloud Talk's native client APIs and WebDAV, enabling:
- Real-time event reception (Long-Polling `lookIntoFuture=1` / WebSockets).
- Full conversation context extraction ($N$ previous messages from the room or thread).
- Native audio/voice message attachments (TTS voice notes rendered as audio players in Talk).
- File upload and sharing (contracts, documents, PDFs).
- Typing notifications ("Agent is typing...").
- Clean decoupled architecture for upstream contribution to Hermes or as a standalone PyPI package.

---

## 2. Architecture & Core Components

```
nextcloud_talk_agent/
├── __init__.py
├── client.py        # Nextcloud Talk API & WebDAV wrapper
├── listener.py      # Event loop listener (HTTP Long-Polling lookIntoFuture=1 with backoff)
├── context.py       # Conversation history assembler & formatter for LLMs
├── media.py         # Voice note and file attachment helper (WebDAV upload + Talk share)
├── agent.py         # High-level TalkAgent base class with event callbacks
└── models.py        # Pydantic / dataclass definitions (Message, Room, Participant, Attachment)
```

---

## 3. Technical Requirements

### 3.1 Authentication
- Connects using Nextcloud Server URL, Username, and App Password (generated from Nextcloud security settings).
- Support for Bearer or Basic Auth headers with `OCS-APIRequest: true`.

### 3.2 Event Listening (Reactive Loop)
- Endpoint: `GET /ocs/v2.php/apps/spreed/api/v1/chat/{token}?lookIntoFuture=1`
- The listener holds the connection until new messages arrive, extracts new incoming comments, filters out messages sent by the agent itself, and invokes the appropriate handler:
  - `on_mention(message, context)`: Triggered when `@AgentName` is mentioned.
  - `on_direct_message(message, context)`: Triggered in 1-to-1 rooms.
  - `on_message(message, context)`: Triggered for all messages (if enabled).

### 3.3 Context Building
- When a mention or message triggers the agent, retrieve the last $N$ messages (default 15) using `/ocs/v2.php/apps/spreed/api/v1/chat/{token}?limit=15`.
- Formats messages chronologically:
  ```json
  [
    {"role": "user", "name": "Raul Moreno", "content": "Here is the draft of the contract."},
    {"role": "user", "name": "Eduardo Cintas", "content": "@Saul can you analyze the jurisdiction clause?"}
  ]
  ```

### 3.4 Media & Voice Messages (TTS)
- Upload audio file (e.g. `response.ogg` or `.mp3`) via WebDAV to `/remote.php/dav/files/{user}/Talk/{filename}`.
- Share file in conversation via Talk API or post message with attachment metadata so Nextcloud Talk renders the native audio player.

### 3.5 Typing State
- Trigger typing indicator before and during LLM inference:
  - `POST /ocs/v2.php/apps/spreed/api/v1/chat/{token}/typing` or state signaling.

---

## 4. Development Deliverables
1. `pyproject.toml` with standard packaging (using `httpx`, `nc_py_api` or native async HTTP).
2. Clean, typed, documented Python code with `asyncio`.
3. Unit tests with mocked Nextcloud endpoints.
4. Example scripts:
   - `examples/echo_agent.py`
   - `examples/hermes_bridge.py`
5. `README.md` with installation, configuration, and API reference.
