"""Event-loop listener: HTTP long-polling with ``lookIntoFuture=1`` + backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from nextcloud_talk_agent.client import TalkApiError, TalkClient
from nextcloud_talk_agent.context import parse_raw_messages
from nextcloud_talk_agent.models import Message

log = logging.getLogger("nextcloud_talk_agent.listener")


@dataclass
class ListenerConfig:
    poll_timeout: int = 30  # server wait for lookIntoFuture=1 (<=60)
    backoff_initial: float = 1.0
    backoff_max: float = 30.0
    backoff_factor: float = 2.0
    history_limit: int = 100  # page size per poll
    include_last_known: int = 0


MessageHandler = Callable[[Message], Awaitable[None]]


class TalkListener:
    """Long-poll one or more rooms, de-duplicate, filter own messages.

    Usage::

        listener = TalkListener(client, agent_actor_id="saul_bot")
        listener.on_message(my_handler)
        await listener.listen_forever(["roomtoken1"])
    """

    def __init__(
        self,
        client: TalkClient,
        agent_actor_id: str = "",
        config: Optional[ListenerConfig] = None,
    ):
        self.client = client
        self.agent_actor_id = agent_actor_id
        self.config = config or ListenerConfig()
        self._handlers: list[MessageHandler] = []
        self._last_known: dict[str, int] = {}
        self._seen_ids: dict[str, set[int]] = {}
        self._running = False

    # -- handler registration -------------------------------------------
    def on_message(self, handler: MessageHandler) -> MessageHandler:
        self._handlers.append(handler)
        return handler

    # -- state ------------------------------------------------------------
    def seed(self, token: str, last_known_message_id: int) -> None:
        self._last_known[token] = last_known_message_id

    async def bootstrap(self, tokens: list[str]) -> None:
        """Fetch latest history once so polling starts from the newest id."""
        for token in tokens:
            if token in self._last_known:
                continue
            try:
                page = await self.client.get_messages(token, limit=1)
                if page.last_given:
                    self._last_known[token] = page.last_given
                elif page.messages:
                    self._last_known[token] = int(page.messages[-1].get("id", 0) or 0)
                else:
                    self._last_known[token] = 0
            except TalkApiError as exc:
                log.warning("bootstrap %s failed: %s", token, exc)
                self._last_known[token] = 0

    # -- filtering ---------------------------------------------------------
    def _is_own(self, msg: Message) -> bool:
        return bool(self.agent_actor_id) and msg.actor_id == self.agent_actor_id

    def _dedupe(self, token: str, msg: Message) -> bool:
        """Return True if already seen."""
        seen = self._seen_ids.setdefault(token, set())
        if msg.id in seen:
            return True
        seen.add(msg.id)
        # bound memory: keep last ~2000 ids per room
        if len(seen) > 2000:
            for old in sorted(seen)[: len(seen) - 2000]:
                seen.discard(old)
        return False

    async def _dispatch(self, msg: Message) -> None:
        for handler in self._handlers:
            try:
                await handler(msg)
            except Exception:
                log.exception("message handler failed for msg %s", msg.id)

    # -- polling -------------------------------------------------------------
    async def poll_once(self, token: str) -> list[Message]:
        """Single long-poll round; returns new inbound (non-own) messages."""
        last = self._last_known.get(token, 0)
        page = await self.client.poll_messages(
            token,
            last,
            timeout=self.config.poll_timeout,
            limit=self.config.history_limit,
        )
        if page.status == 304 or not page.messages:
            if page.last_given:
                self._last_known[token] = max(last, page.last_given)
            return []
        messages = parse_raw_messages(page.messages, token)
        fresh: list[Message] = []
        for msg in sorted(messages, key=lambda m: m.id):
            if msg.id <= last and msg.id in self._seen_ids.get(token, set()):
                continue
            if self._dedupe(token, msg):
                continue
            self._last_known[token] = max(self._last_known.get(token, 0), msg.id)
            if msg.is_system or msg.is_deleted:
                continue
            if self._is_own(msg):
                continue
            fresh.append(msg)
        if page.last_given:
            self._last_known[token] = max(self._last_known[token], page.last_given)
        return fresh

    def _backoff(self, failures: int) -> float:
        delay = min(
            self.config.backoff_initial * (self.config.backoff_factor**failures),
            self.config.backoff_max,
        )
        return delay + random.uniform(0, delay * 0.1)

    async def _room_loop(self, token: str) -> None:
        failures = 0
        while self._running:
            try:
                fresh = await self.poll_once(token)
                failures = 0
                for msg in fresh:
                    await self._dispatch(msg)
            except asyncio.CancelledError:
                raise
            except (TalkApiError, OSError) as exc:
                failures += 1
                delay = self._backoff(failures)
                log.warning("poll %s failed (%s); retry in %.1fs", token, exc, delay)
                await asyncio.sleep(delay)
            except Exception:
                log.exception("unexpected poll error for %s", token)
                failures += 1
                await asyncio.sleep(self._backoff(failures))

    async def listen_forever(self, tokens: list[str], *, bootstrap: bool = True) -> None:
        """Poll all ``tokens`` concurrently until :meth:`stop` is called."""
        if bootstrap:
            await self.bootstrap(tokens)
        self._running = True
        tasks = [asyncio.create_task(self._room_loop(t), name=f"talk-poll-{t}") for t in tokens]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._running = False
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def listen_all_rooms(self) -> None:
        """Resolve rooms via API and listen on every conversation."""
        rooms = await self.client.get_rooms()
        tokens = [r.get("token", "") for r in rooms if r.get("token")]
        await self.listen_forever(tokens)

    def stop(self) -> None:
        self._running = False
