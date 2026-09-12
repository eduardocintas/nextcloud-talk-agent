# nextcloud-talk-agent

Standalone Python library to connect autonomous AI agents (Hermes, OpenClaw,
LangChain, custom daemons) to **Nextcloud Talk** as full-fledged service users.

Unlike webhook bots, a service user operates via the native Talk client APIs +
WebDAV: real-time long-polling, full conversation context, typing indicators,
file upload/sharing and native voice-message attachments.

## Install

```bash
pip install nextcloud-talk-agent
# dev / tests
pip install -e ".[dev]"
```

## Quickstart

```python
import asyncio, os
from nextcloud_talk_agent import AgentConfig, TalkAgent, Message, ChatContext

class Echo(TalkAgent):
    async def on_mention(self, msg: Message, ctx: ChatContext):
        await self.reply(ctx, f"echo: {msg.message}")

    async def on_direct_message(self, msg: Message, ctx: ChatContext):
        await self.reply(ctx, f"echo: {msg.message}")

async def main():
    agent = Echo(AgentConfig(
        server_url=os.environ["NEXTCLOUD_URL"],
        username=os.environ["NEXTCLOUD_USER"],
        password=os.environ["NEXTCLOUD_APP_PASSWORD"],  # app password
    ))
    await agent.run()  # all rooms; or agent.run(["roomtoken"])

asyncio.run(main())
```

See `examples/echo_agent.py` and `examples/hermes_bridge.py`.

## Configuration

| Var | Description |
|---|---|
| `NEXTCLOUD_URL` | Server URL, e.g. `https://cloud.example.com` |
| `NEXTCLOUD_USER` | Service-user login |
| `NEXTCLOUD_APP_PASSWORD` | App password (Settings → Security) |
| `TALK_ROOMS` | Optional comma-separated room tokens (empty = all) |
| `HISTORY_LIMIT` | Context window N (default 15) |

Auth: HTTP Basic (user + app password) by default, or `use_bearer=True` for
token auth. Every OCS request sends `OCS-APIRequest: true`.

## API reference

### `TalkClient` (`client.py`)
Async `httpx`-based wrapper.

- `get_rooms() / get_room(token) / get_participants(token) / join_room(token)`
- `get_messages(token, limit, last_known_message_id, look_into_future, timeout, ...)` →
  `ChatPage(messages, last_given, ...)`; reads `X-Chat-Last-Given`.
- `poll_messages(token, last_id, timeout=30)` → long-poll
  `GET /ocs/v2.php/apps/spreed/api/v1/chat/{token}?lookIntoFuture=1`.
- `send_message(token, message, reply_to, silent, thread_id, reference_id)`
- `set_typing(token, typing)` → `POST .../chat/{token}/typing` (best-effort, ignores 404).
- `share_file_to_chat(user_path, room_token, caption, message_type)` →
  `POST /ocs/v2.php/apps/files_sharing/api/v1/shares` (`shareType=10`).
- `share_rich_object(token, object_type, object_id, meta_data)` →
  `POST .../chat/{token}/share`.
- WebDAV: `webdav_mkcol`, `webdav_put`, `webdav_put_file`
  (`PUT /remote.php/dav/files/{user}/{path}`).

### `TalkListener` (`listener.py`)
- `bootstrap(tokens)` seeds `lastKnownMessageId` from latest history.
- `poll_once(token)` long-polls, de-duplicates, skips system/deleted + own messages.
- `listen_forever(tokens)` / `listen_all_rooms()` with exponential backoff
  (`backoff_initial=1s`, factor 2, max 30s) on `304`/errors/timeouts.
- Register callbacks with `listener.on_message(handler)`.

### `context.py` (SPEC §3.3)
- `build_llm_context(messages, limit=15, agent_actor_id=...)` →
  `[{"role": "user"|"assistant", "name": ..., "content": ...}]` chronological.
  Own turns → `assistant`, others → `user`; system/deleted/empty filtered.
- `format_as_text(messages)`, `filter_history(...)`, `parse_raw_messages(...)`.

### `media.py` (SPEC §3.4)
- `MediaHelper(client, folder="Talk")`
- `upload_file / upload_bytes` (WebDAV `Talk/…`, auto-MKCOL, mimetype guess).
- `share_file`, `send_file`, `send_voice_message(local_audio, token)` and
  `send_audio_bytes(filename, data, token)` with `messageType=voice-message`
  so Talk renders the native audio player.

### `TalkAgent` (`agent.py`)
Base class with routing:
- `on_mention(message, context)` — `@AgentName` / `@all` mention.
- `on_direct_message(message, context)` — 1-to-1 room (`roomType 1`).
- `on_message(message, context)` — every message if `respond_to_all=True`.
- Helpers: `reply(ctx_or_token, text)`, `typing(token)` async context manager
  (typing indicator during LLM inference), `send_voice_reply(...)`, `run(tokens)`.

### Models (`models.py`)
Pydantic: `Message` (+`from_ocs`, `mentions(actor_id)`), `Room` (`is_direct`),
`Participant`, `Attachment`, `ChatContext`, enums `RoomType`, `MessageType`.

## Tests

```bash
pytest
```

Unit tests mock all HTTP via `respx`/`httpx.MockTransport`: auth headers,
history fetch, long-poll `lookIntoFuture=1` + `X-Chat-Last-Given`, self-filter,
mention/direct routing, context format, WebDAV upload + share, typing endpoint.

## License

MIT
