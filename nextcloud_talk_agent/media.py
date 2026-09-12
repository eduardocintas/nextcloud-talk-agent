"""Voice-note and file attachment helper (WebDAV upload + Talk share)."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from typing import Any, Optional

from nextcloud_talk_agent.client import TalkClient

DEFAULT_TALK_FOLDER = "Talk"

VOICE_MIMETYPES = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}


def guess_mimetype(filename: str, default: str = "application/octet-stream") -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in VOICE_MIMETYPES:
        return VOICE_MIMETYPES[ext]
    return mimetypes.guess_type(filename)[0] or default


def is_voice_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in VOICE_MIMETYPES


@dataclass
class UploadResult:
    user_path: str  # e.g. "/Talk/response.ogg"
    share: dict[str, Any]
    message: dict[str, Any] | None = None


class MediaHelper:
    """Upload local files via WebDAV and share them into Talk rooms."""

    def __init__(self, client: TalkClient, folder: str = DEFAULT_TALK_FOLDER):
        self.client = client
        self.folder = folder.strip("/") or DEFAULT_TALK_FOLDER

    async def ensure_folder(self) -> None:
        await self.client.webdav_mkcol(self.folder)

    def remote_name(self, filename: str) -> str:
        base = os.path.basename(filename)
        return f"{self.folder}/{base}"

    async def upload_file(
        self,
        local_path: str,
        *,
        remote_name: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Upload a local file. Returns the user-relative path (``/Talk/x``)."""
        name = remote_name or os.path.basename(local_path)
        user_path = f"{self.folder}/{name}".strip("/")
        ctype = content_type or guess_mimetype(name)
        await self.ensure_folder()
        return await self.client.webdav_put_file(local_path, user_path, ctype)

    async def upload_bytes(
        self, filename: str, data: bytes, content_type: Optional[str] = None
    ) -> str:
        user_path = f"{self.folder}/{os.path.basename(filename)}".strip("/")
        ctype = content_type or guess_mimetype(filename)
        await self.ensure_folder()
        return await self.client.webdav_put(user_path, data, ctype)

    async def share_file(
        self,
        user_path: str,
        room_token: str,
        *,
        caption: str = "",
        message_type: str = "comment",
    ) -> dict[str, Any]:
        if not user_path.startswith("/"):
            user_path = "/" + user_path
        return await self.client.share_file_to_chat(
            user_path, room_token, caption=caption, message_type=message_type
        )

    async def send_file(
        self,
        local_path: str,
        room_token: str,
        *,
        caption: str = "",
        remote_name: Optional[str] = None,
    ) -> UploadResult:
        """Upload + share a generic file; Talk renders image/PDF previews."""
        user_path = await self.upload_file(local_path, remote_name=remote_name)
        share = await self.share_file(user_path, room_token, caption=caption)
        message = self._share_message(share)
        return UploadResult(user_path=user_path, share=share, message=message)

    async def send_voice_message(
        self,
        local_audio: str,
        room_token: str,
        *,
        caption: str = "",
        remote_name: Optional[str] = None,
    ) -> UploadResult:
        """Upload + share audio with ``messageType=voice-message`` so Talk shows
        the native audio player / voice-note bubble."""
        user_path = await self.upload_file(local_path=local_audio, remote_name=remote_name)
        share = await self.share_file(
            user_path, room_token, caption=caption, message_type="voice-message"
        )
        message = self._share_message(share)
        return UploadResult(user_path=user_path, share=share, message=message)

    async def send_audio_bytes(
        self, filename: str, data: bytes, room_token: str, *, caption: str = ""
    ) -> UploadResult:
        """Send TTS output held in memory (e.g. ``response.ogg``)."""
        user_path = await self.upload_bytes(filename, data)
        share = await self.share_file(
            user_path, room_token, caption=caption, message_type="voice-message"
        )
        return UploadResult(user_path=user_path, share=share, message=self._share_message(share))

    @staticmethod
    def _share_message(share: dict[str, Any]) -> dict[str, Any] | None:
        # files_sharing OCS response embeds the chat message in some versions.
        if isinstance(share, dict):
            for key in ("message", "chatMessage", "talkMessage"):
                if isinstance(share.get(key), dict):
                    return share[key]
        return None
