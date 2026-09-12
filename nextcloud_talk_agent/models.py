"""Pydantic models for Nextcloud Talk entities."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RoomType(int, Enum):
    """Conversation types (Nextcloud Talk ``roomType``)."""

    ONE_TO_ONE = 1
    GROUP = 2
    PUBLIC = 3
    CHANGELOG = 4
    VOICE = 5
    BREAKOUT = 6
    FEDERATED_ONE_TO_ONE = 7


class MessageType(str, Enum):
    COMMENT = "comment"
    SYSTEM = "system"
    COMMAND = "command"
    COMMENT_DELETED = "comment_deleted"
    VOICE_MESSAGE = "voice-message"
    FILE_SHARE = "file-share"
    RECORD_AUDIO = "record-audio"
    RECORD_VIDEO = "record-video"


class ActorType(str, Enum):
    USERS = "users"
    GUESTS = "guests"
    BOTS = "bots"
    FEDERATED_USERS = "federated_users"
    EMAIL = "emails"
    BRIDGED = "bridged"


class Attachment(BaseModel):
    """File/rich-object attached to a chat message."""

    object_type: str = "file"
    object_id: str = ""
    name: str = ""
    path: str = ""
    mimetype: str = ""
    size: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """A single Nextcloud Talk chat message (OCS ``chat`` item)."""

    id: int
    token: str = ""
    actor_type: str = ""
    actor_id: str = ""
    actor_display_name: str = ""
    message: str = ""
    message_type: str = "comment"
    timestamp: int = 0
    is_replyable: bool = False
    reply_to: Optional[int] = None
    reference_id: str = ""
    system_message: str = ""
    thread_id: int = 0
    attachments: list[Attachment] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_ocs(cls, data: dict[str, Any]) -> "Message":
        params = data.get("messageParameters", {}) or {}
        attachments: list[Attachment] = []
        file_param = params.get("file")
        if isinstance(file_param, dict):
            attachments.append(
                Attachment(
                    object_type="file",
                    object_id=str(file_param.get("id", "")),
                    name=str(file_param.get("name", "")),
                    path=str(file_param.get("path", "")),
                    mimetype=str(file_param.get("mimetype", "")),
                    size=int(file_param.get("size", 0) or 0),
                    raw=file_param,
                )
            )
        reply_to = None
        parent = data.get("parent")
        if isinstance(parent, dict) and "id" in parent:
            try:
                reply_to = int(parent["id"])
            except (TypeError, ValueError):
                reply_to = None
        return cls(
            id=int(data.get("id", 0)),
            token=str(data.get("token", "")),
            actor_type=str(data.get("actorType", "")),
            actor_id=str(data.get("actorId", "")),
            actor_display_name=str(data.get("actorDisplayName", "")),
            message=str(data.get("message", "")),
            message_type=str(data.get("messageType", "comment")),
            timestamp=int(data.get("timestamp", 0) or 0),
            is_replyable=bool(data.get("isReplyable", False)),
            reply_to=reply_to,
            reference_id=str(data.get("referenceId", "") or ""),
            system_message=str(data.get("systemMessage", "") or ""),
            thread_id=int(data.get("threadId", 0) or 0),
            attachments=attachments,
            raw=data,
        )

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    @property
    def is_system(self) -> bool:
        return self.message_type == MessageType.SYSTEM or bool(self.system_message)

    @property
    def is_deleted(self) -> bool:
        return self.message_type == MessageType.COMMENT_DELETED

    def mentions(self, actor_id: str) -> bool:
        """Return True if the message mentions ``@{actor_id}`` or ``@all``."""
        params = self.raw.get("messageParameters", {}) or {}
        for key, value in params.items():
            if not isinstance(value, dict):
                continue
            if value.get("type") in ("user", "call", "user-group"):
                if key == f"mention-user-{actor_id}" or value.get("id") == actor_id:
                    return True
                if value.get("id") == "all":
                    return True
        text = self.message or ""
        return f"@{actor_id}" in text or "@all" in text


class Room(BaseModel):
    """A Nextcloud Talk conversation/room."""

    token: str
    name: str = ""
    display_name: str = ""
    type: int = 2
    last_message_id: int = 0
    unread_messages: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_ocs(cls, data: dict[str, Any]) -> "Room":
        last_msg = data.get("lastMessage", {}) or {}
        try:
            last_id = int(last_msg.get("id", 0) or 0)
        except (TypeError, ValueError):
            last_id = 0
        return cls(
            token=str(data.get("token", "")),
            name=str(data.get("name", "")),
            display_name=str(data.get("displayName", data.get("name", ""))),
            type=int(data.get("type", data.get("roomType", 2)) or 2),
            last_message_id=last_id,
            unread_messages=int(data.get("unreadMessages", 0) or 0),
            raw=data,
        )

    @property
    def room_type(self) -> RoomType | int:
        try:
            return RoomType(self.type)
        except ValueError:
            return self.type

    @property
    def is_direct(self) -> bool:
        return self.type in (
            RoomType.ONE_TO_ONE,
            RoomType.FEDERATED_ONE_TO_ONE,
        )


class Participant(BaseModel):
    """A room participant."""

    actor_type: str = ""
    actor_id: str = ""
    display_name: str = ""
    participant_type: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_ocs(cls, data: dict[str, Any]) -> "Participant":
        return cls(
            actor_type=str(data.get("actorType", data.get("source", ""))),
            actor_id=str(data.get("actorId", data.get("userId", data.get("id", "")))),
            display_name=str(data.get("displayName", "")),
            participant_type=int(data.get("participantType", 0) or 0),
            raw=data,
        )


class ChatContext(BaseModel):
    """Message + surrounding history passed to agent callbacks."""

    message: Message
    room: Optional[Room] = None
    history: list[Message] = Field(default_factory=list)
    llm_messages: list[dict[str, str]] = Field(default_factory=list)
