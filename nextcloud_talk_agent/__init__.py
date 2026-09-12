"""nextcloud-talk-agent: connect AI agents to Nextcloud Talk as service users."""

from nextcloud_talk_agent.agent import TalkAgent
from nextcloud_talk_agent.client import TalkClient, TalkClientConfig
from nextcloud_talk_agent.context import build_llm_context, format_as_text
from nextcloud_talk_agent.listener import TalkListener, ListenerConfig
from nextcloud_talk_agent.media import MediaHelper
from nextcloud_talk_agent.models import (
    Attachment,
    ChatContext,
    Message,
    MessageType,
    Participant,
    Room,
    RoomType,
)

__all__ = [
    "TalkAgent",
    "TalkClient",
    "TalkClientConfig",
    "TalkListener",
    "ListenerConfig",
    "MediaHelper",
    "Message",
    "Room",
    "Participant",
    "Attachment",
    "ChatContext",
    "MessageType",
    "RoomType",
    "build_llm_context",
    "format_as_text",
]

__version__ = "0.1.0"
