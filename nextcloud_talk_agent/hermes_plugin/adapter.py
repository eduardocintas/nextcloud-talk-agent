"""Nextcloud Talk Platform Adapter for Hermes Agent.

Connects to Nextcloud Talk using the standalone nextcloud-talk-agent library,
listening via dynamic room discovery and long-polling / WebSockets, and dispatching
MessageEvents directly to Hermes Gateway without external daemons.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time
from typing import Any, Dict, List, Optional

from agent.secret_scope import UnscopedSecretError as _UnscopedSecretError
from agent.secret_scope import get_secret as _scoped_get_secret

from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from nextcloud_talk_agent.client import TalkClient, TalkClientConfig
from nextcloud_talk_agent.listener import ListenerConfig, TalkListener
from nextcloud_talk_agent.media import MediaHelper
from nextcloud_talk_agent.models import Message

logger = logging.getLogger("hermes.platform.nextcloud_talk")


def _get_scoped_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        val = _scoped_get_secret(name, default)
    except _UnscopedSecretError:
        val = os.getenv(name)
    return val if val is not None else default


class NextcloudTalkAdapter(BasePlatformAdapter):
    """Hermes Native Gateway Adapter for Nextcloud Talk."""

    supports_code_blocks: bool = True
    _ACK_EMOJI: str = "👀"
    _OK_EMOJI: str = "✅"
    _FAIL_EMOJI: str = "❌"

    def __init__(self, config: Any, **kwargs: Any) -> None:
        platform = Platform("nextcloud_talk")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        # Connection settings
        self.server_url = (
            _get_scoped_secret("NEXTCLOUD_URL")
            or extra.get("server_url", "")
            or os.getenv("NEXTCLOUD_URL", "")
        )
        self.username = (
            _get_scoped_secret("NEXTCLOUD_TALK_USER")
            or extra.get("username", "")
            or os.getenv("NEXTCLOUD_TALK_USER", "")
        )
        self.password = (
            _get_scoped_secret("NEXTCLOUD_TALK_PASSWORD")
            or extra.get("password", "")
            or os.getenv("NEXTCLOUD_TALK_PASSWORD", "")
        )

        # Allowed users (whitelist)
        allowed = extra.get("allowed_users", [])
        if isinstance(allowed, str):
            allowed = [u.strip() for u in allowed.split(",") if u.strip()]
        env_allowed = os.getenv("NEXTCLOUD_TALK_ALLOWED_USERS", "")
        if env_allowed:
            allowed = [u.strip() for u in env_allowed.split(",") if u.strip()]
        self.allowed_users: list[str] = allowed
        self._allowed_users_set = {u.lower() for u in self.allowed_users if u}

        # Internal state
        self._client: Optional[TalkClient] = None
        self._listener: Optional[TalkListener] = None
        self._listen_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Nextcloud Talk"

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Connect to Nextcloud Talk and start the dynamic room listener."""
        if not self.server_url or not self.username or not self.password:
            logger.error("Nextcloud Talk: server_url, username, and password must be configured")
            self._set_fatal_error(
                "config_missing",
                "NEXTCLOUD_URL, NEXTCLOUD_TALK_USER, and NEXTCLOUD_TALK_PASSWORD must be set",
                retryable=False,
            )
            return False

        try:
            cfg = TalkClientConfig(
                server_url=self.server_url,
                username=self.username,
                password=self.password,
            )
            self._client = TalkClient(cfg)
            self._listener = TalkListener(self._client, agent_actor_id=self.username)
            self._listener.on_message(self._on_talk_message)

            self._listen_task = asyncio.create_task(
                self._listener.listen_all_rooms(room_sync_interval=15.0),
                name="hermes-talk-listener",
            )

            self._mark_connected()
            logger.info("Nextcloud Talk: connected to %s as %s", self.server_url, self.username)
            return True
        except Exception as exc:
            logger.error("Nextcloud Talk: connection failed: %s", exc, exc_info=True)
            self._set_fatal_error("connect_failed", str(exc), retryable=True)
            return False

    async def disconnect(self) -> None:
        """Stop listening and close client sessions."""
        if self._listener:
            self._listener.stop()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        self._mark_disconnected()
        logger.info("Nextcloud Talk: disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a message to a Nextcloud Talk room (chat_id = token)."""
        if not self._client:
            return SendResult(success=False, error="Nextcloud Talk client is not connected")

        try:
            reply_to_id = int(reply_to) if reply_to and reply_to.isdigit() else None
            res = await self._client.send_message(
                token=chat_id,
                message=content,
                reply_to=reply_to_id,
            )
            msg_id = str(res.get("id", ""))
            return SendResult(success=True, message_id=msg_id)
        except Exception as exc:
            logger.error("Nextcloud Talk send failed to room %s: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc))

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """Edit a previously sent message in Nextcloud Talk."""
        if not self._client:
            return SendResult(success=False, error="Nextcloud Talk client is not connected")

        try:
            mid = int(message_id)
            res = await self._client.edit_message(token=chat_id, message_id=mid, message=content)
            new_id = str(res.get("id", message_id)) if isinstance(res, dict) else message_id
            return SendResult(success=True, message_id=new_id)
        except Exception as exc:
            logger.error("Nextcloud Talk edit failed in room %s for msg %s: %s", chat_id, message_id, exc)
            return SendResult(success=False, error=str(exc))

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a message in Nextcloud Talk (e.g. for progress cleanup)."""
        if not self._client:
            return False
        try:
            mid = int(message_id)
            status = await self._client.delete_message(token=chat_id, message_id=mid)
            return status in (200, 204)
        except Exception as exc:
            logger.debug("Nextcloud Talk delete_message failed for %s in %s: %s", message_id, chat_id, exc)
            return False

    async def _add_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        if not self._client:
            return False
        try:
            mid = int(message_id)
            await self._client.add_reaction(token=chat_id, message_id=mid, reaction=emoji)
            return True
        except Exception as exc:
            logger.debug("Nextcloud Talk add_reaction failed for %s on %s: %s", emoji, message_id, exc)
            return False

    async def _remove_reaction(self, chat_id: str, message_id: str, emoji: Optional[str] = None) -> bool:
        if not self._client:
            return False
        try:
            mid = int(message_id)
            # Default to removing _ACK_EMOJI if none specified
            rem_emoji = emoji or self._ACK_EMOJI
            await self._client.remove_reaction(token=chat_id, message_id=mid, reaction=rem_emoji)
            return True
        except Exception as exc:
            logger.debug("Nextcloud Talk remove_reaction failed for %s on %s: %s", emoji, message_id, exc)
            return False

    async def on_processing_start(self, event: MessageEvent) -> None:
        """Add 👀 reaction immediately upon receiving user message."""
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if chat_id and message_id and self._ACK_EMOJI:
            await self._add_reaction(chat_id, message_id, self._ACK_EMOJI)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Upload and send an audio file as a native voice message in Talk."""
        if not self._client:
            return SendResult(success=False, error="Nextcloud Talk client is not connected")

        if not os.path.exists(audio_path):
            logger.warning("[Nextcloud Talk] Audio file does not exist: %s", audio_path)
            return SendResult(success=False, error="Audio file not found")

        try:
            helper = MediaHelper(self._client)
            upload_res = await helper.send_voice_message(
                local_audio=audio_path,
                room_token=chat_id,
                caption=caption or "",
            )
            msg_id = ""
            if upload_res.message and isinstance(upload_res.message, dict):
                msg_id = str(upload_res.message.get("id", ""))
            return SendResult(success=True, message_id=msg_id)
        except Exception as exc:
            logger.error("[Nextcloud Talk] Failed to send voice message to %s: %s", chat_id, exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get information about a chat/room (chat_id = room token)."""
        if self._client:
            try:
                room = await self._client.get_room(chat_id)
                name = room.get("name") if isinstance(room, dict) else getattr(room, "name", chat_id)
                room_type_val = room.get("type") if isinstance(room, dict) else getattr(room, "type", 2)
                room_type = "dm" if room_type_val == 1 else "group"
                return {
                    "name": name or chat_id,
                    "type": room_type,
                    "raw": room,
                }
            except Exception as exc:
                logger.debug("Failed to get chat info for %s: %s", chat_id, exc)
        return {
            "name": chat_id,
            "type": "group",
        }

    async def send_typing(self, chat_id: str) -> None:
        """Signal that the agent is typing."""
        if self._client:
            try:
                await self._client.set_typing(chat_id, typing=True)
            except Exception:
                pass

    async def _on_talk_message(self, msg: Message) -> None:
        """Receive message from TalkListener and dispatch to Hermes Gateway."""
        sender_id = msg.actor_id or ""
        sender_name = msg.actor_display_name or sender_id

        # Whitelist enforcement (matches full id, lowercase, or username part before @)
        sender_lower = sender_id.lower()
        sender_prefix = sender_lower.split("@")[0]
        if self._allowed_users_set and sender_lower not in self._allowed_users_set and sender_prefix not in self._allowed_users_set:
            logger.info("Nextcloud Talk: ignoring message from unauthorized user %s (allowed: %s)", sender_id, self._allowed_users_set)
            return

        chat_id = msg.token
        chat_type = "dm" if msg.actor_type == "users" and len(chat_id) < 40 else "group"

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id,
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_name,
        )

        event = MessageEvent(
            text=msg.message or "",
            message_type=MessageType.TEXT,
            source=source,
            message_id=str(msg.id),
            reply_to_message_id=str(msg.reply_to) if msg.reply_to else None,
            timestamp=datetime.datetime.fromtimestamp(msg.timestamp or time.time()),
            raw_message=msg.raw,
        )

        await self.handle_message(event)


def check_requirements() -> bool:
    """Check if nextcloud-talk-agent dependencies are importable."""
    try:
        import httpx  # noqa: F401
        import pydantic  # noqa: F401
        import nextcloud_talk_agent  # noqa: F401
        return True
    except ImportError:
        return False


def validate_config(config: Any) -> bool:
    extra = getattr(config, "extra", {}) or {}
    url = os.getenv("NEXTCLOUD_URL") or extra.get("server_url")
    user = os.getenv("NEXTCLOUD_TALK_USER") or extra.get("username")
    pw = os.getenv("NEXTCLOUD_TALK_PASSWORD") or extra.get("password")
    return bool(url and user and pw)


def register(ctx: Any) -> None:
    """Plugin entry point called by Hermes plugin discovery."""
    ctx.register_platform(
        name="nextcloud_talk",
        label="Nextcloud Talk",
        adapter_factory=lambda cfg: NextcloudTalkAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["NEXTCLOUD_URL", "NEXTCLOUD_TALK_USER", "NEXTCLOUD_TALK_PASSWORD"],
        install_hint="Ensure nextcloud-talk-agent is available on PYTHONPATH",
        allowed_users_env="NEXTCLOUD_TALK_ALLOWED_USERS",
        emoji="💬",
        pii_safe=False,
        platform_hint=(
            "You are chatting via Nextcloud Talk. Markdown is supported. "
            "In direct conversations, talk naturally. Keep responses concise and friendly."
        ),
    )
