"""High-level ``TalkAgent`` base class with event callbacks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional

from nextcloud_talk_agent.client import TalkClient, TalkClientConfig
from nextcloud_talk_agent.context import build_llm_context, parse_raw_messages
from nextcloud_talk_agent.listener import ListenerConfig, TalkListener
from nextcloud_talk_agent.media import MediaHelper
from nextcloud_talk_agent.models import ChatContext, Message, Room

log = logging.getLogger("nextcloud_talk_agent.agent")


@dataclass
class AgentConfig:
    server_url: str
    username: str
    password: str
    use_bearer: bool = False
    history_limit: int = 15
    respond_to_all: bool = False  # if True, on_message fires for every message
    verify_ssl: bool = True


MessageCallback = Callable[[Message, ChatContext], Awaitable[None]]


class TalkAgent:
    """Subclass and override ``on_mention`` / ``on_direct_message`` / ``on_message``.

    Example::

        class Echo(TalkAgent):
            async def on_mention(self, msg, ctx):
                await self.reply(ctx, f"echo: {msg.message}")
    """

    def __init__(self, config: AgentConfig, client: Optional[TalkClient] = None):
        self.config = config
        self.client = client or TalkClient(
            TalkClientConfig(
                server_url=config.server_url,
                username=config.username,
                password=config.password,
                use_bearer=config.use_bearer,
                verify_ssl=config.verify_ssl,
            )
        )
        self.media = MediaHelper(self.client)
        self.listener = TalkListener(self.client, agent_actor_id=config.username)
        self.listener.on_message(self._route)
        self._room_cache: dict[str, Room] = {}
        self._running = False

    # -- callbacks (override in subclasses) ---------------------------------
    async def on_mention(self, message: Message, context: ChatContext) -> None:
        """Triggered when ``@AgentName`` is mentioned."""

    async def on_direct_message(self, message: Message, context: ChatContext) -> None:
        """Triggered for 1-to-1 rooms."""

    async def on_message(self, message: Message, context: ChatContext) -> None:
        """Triggered for all messages (only if ``respond_to_all=True``)."""

    # -- routing ---------------------------------------------------------------
    async def _get_room(self, token: str) -> Optional[Room]:
        if token in self._room_cache:
            return self._room_cache[token]
        try:
            raw = await self.client.get_room(token)
            room = Room.from_ocs(raw)
            self._room_cache[token] = room
            return room
        except Exception as exc:
            log.debug("get_room %s failed: %s", token, exc)
            return None

    def _is_direct(self, room: Optional[Room], message: Message) -> bool:
        if room is not None:
            return room.is_direct
        # Fallback heuristic: 1-1 rooms usually have exactly 2 participants,
        # but without room info we treat unknown rooms as non-direct.
        return False

    async def _build_context(self, message: Message) -> ChatContext:
        room = await self._get_room(message.token) if message.token else None
        history: list[Message] = []
        try:
            page = await self.client.get_messages(
                message.token, limit=self.config.history_limit
            )
            history = parse_raw_messages(page.messages, message.token)
        except Exception as exc:
            log.debug("history fetch failed: %s", exc)
        llm_messages = build_llm_context(
            [*history, message],
            limit=self.config.history_limit,
            agent_actor_id=self.config.username,
        )
        return ChatContext(message=message, room=room, history=history, llm_messages=llm_messages)

    async def _route(self, message: Message) -> None:
        ctx = await self._build_context(message)
        mentioned = message.mentions(self.config.username)
        direct = self._is_direct(ctx.room, message)
        try:
            if mentioned:
                async with self.typing(message.token):
                    await self.on_mention(message, ctx)
            if direct:
                async with self.typing(message.token):
                    await self.on_direct_message(message, ctx)
            if self.config.respond_to_all:
                async with self.typing(message.token):
                    await self.on_message(message, ctx)
            # Default: mention XOR direct already handled. If neither fired and
            # subclass only overrode on_message without respond_to_all, do nothing.
        except Exception:
            log.exception("agent callback failed for msg %s", message.id)

    # -- helpers for subclasses --------------------------------------------------
    async def reply(
        self,
        ctx_or_token: ChatContext | str,
        text: str,
        *,
        reply_to: Optional[int] = None,
        silent: bool = False,
    ) -> dict[str, Any]:
        token = ctx_or_token.message.token if isinstance(ctx_or_token, ChatContext) else ctx_or_token
        reply_id = reply_to
        if reply_id is None and isinstance(ctx_or_token, ChatContext):
            reply_id = ctx_or_token.message.id
        return await self.client.send_message(token, text, reply_to=reply_id, silent=silent)

    @contextlib.asynccontextmanager
    async def typing(self, token: str):
        """``async with agent.typing(token):`` — set typing indicator around LLM inference."""
        if not token:
            yield
            return
        try:
            await self.client.set_typing(token, True)
        except Exception:
            log.debug("set_typing(true) failed", exc_info=True)
        try:
            yield
        finally:
            try:
                await self.client.set_typing(token, False)
            except Exception:
                log.debug("set_typing(false) failed", exc_info=True)

    async def send_voice_reply(
        self, ctx_or_token: ChatContext | str, local_audio: str, caption: str = ""
    ):
        token = ctx_or_token.message.token if isinstance(ctx_or_token, ChatContext) else ctx_or_token
        return await self.media.send_voice_message(local_audio, token, caption=caption)

    # -- run -----------------------------------------------------------------------
    async def run(
        self,
        tokens: Optional[list[str]] = None,
        *,
        listener_config: Optional[ListenerConfig] = None,
    ) -> None:
        """Start polling. ``tokens=None`` listens on all rooms."""
        if listener_config is not None:
            self.listener.config = listener_config
        self._running = True
        try:
            if tokens is None:
                await self.listener.listen_all_rooms()
            else:
                await self.listener.listen_forever(tokens)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
        self.listener.stop()

    async def aclose(self) -> None:
        await self.client.aclose()
