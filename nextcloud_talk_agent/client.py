"""Async Nextcloud Talk + WebDAV API client.

Endpoints (Nextcloud Talk):
- Chat history / long-poll: ``GET /ocs/v2.php/apps/spreed/api/v1/chat/{token}``
- Send message:            ``POST /ocs/v2.php/apps/spreed/api/v1/chat/{token}``
- Typing indicator:        ``POST /ocs/v2.php/apps/spreed/api/v1/chat/{token}/typing``
- Rooms:                   ``GET /ocs/v2.php/apps/spreed/api/v4/room``
- Share file to chat:      ``POST /ocs/v2.php/apps/files_sharing/api/v1/shares``
  (``shareType=10``, ``shareWith=<room token>``)
- WebDAV upload:           ``PUT /remote.php/dav/files/{user}/{path}``
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx

OCS_BASE = "/ocs/v2.php/apps/spreed/api/v1"
ROOMS_BASE = "/ocs/v2.php/apps/spreed/api/v4/room"
SHARE_API = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
SHARE_TYPE_ROOM = 10


class TalkApiError(RuntimeError):
    """Raised when a Nextcloud OCS/WebDAV request fails."""

    def __init__(self, message: str, *, status: int = 0, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass
class TalkClientConfig:
    server_url: str
    username: str
    password: str  # Nextcloud app password
    use_bearer: bool = False
    timeout: float = 65.0
    verify_ssl: bool = True
    user_agent: str = "nextcloud-talk-agent/0.1.0"

    def normalized_server(self) -> str:
        return self.server_url.rstrip("/")


@dataclass
class ChatPage:
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_given: int = 0  # X-Chat-Last-Given
    last_common_read: int = 0  # X-Chat-Last-Common-Read
    status: int = 200


def ocs_headers(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


class TalkClient:
    """Thin async wrapper around Talk OCS API + WebDAV."""

    def __init__(
        self,
        config: TalkClientConfig,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.config = config
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout),
            verify=config.verify_ssl,
            headers={"User-Agent": config.user_agent},
        )

    # -- lifecycle ------------------------------------------------------
    async def __aenter__(self) -> "TalkClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self._external_client:
            await self._client.aclose()

    # -- auth / urls ----------------------------------------------------
    @property
    def _auth(self) -> httpx.BasicAuth | tuple[str, str] | None:
        if self.config.use_bearer:
            return None
        return httpx.BasicAuth(self.config.username, self.config.password)

    def _auth_headers(self) -> dict[str, str]:
        if self.config.use_bearer:
            return {"Authorization": f"Bearer {self.config.password}"}
        return {}

    def _url(self, path: str) -> str:
        return f"{self.config.normalized_server()}{path}"

    def _check_ocs(self, data: Any, *, status: int) -> Any:
        """Unwrap ``{'ocs': {'meta': ..., 'data': ...}}`` or raise."""
        if isinstance(data, dict) and "ocs" in data:
            ocs = data["ocs"]
            meta = ocs.get("meta", {})
            statuscode = str(meta.get("statuscode", ""))
            if statuscode not in ("200", "201", "100"):
                raise TalkApiError(
                    f"OCS error {statuscode}: {meta.get('message')}",
                    status=status,
                    payload=data,
                )
            return ocs.get("data")
        return data

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        content: Any = None,
        headers: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        merged = ocs_headers(self._auth_headers())
        if headers:
            merged.update(headers)
        resp = await self._client.request(
            method,
            self._url(path),
            params=params,
            json=json,
            data=data,
            content=content,
            headers=merged,
            auth=self._auth,
        )
        if resp.status_code in (304,):
            return resp
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            raise TalkApiError(
                f"{method} {path} failed with HTTP {resp.status_code}",
                status=resp.status_code,
                payload=payload,
            )
        return resp

    # -- rooms ----------------------------------------------------------
    async def get_rooms(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", ROOMS_BASE, headers={"Accept": "application/json"})
        data = self._check_ocs(resp.json(), status=resp.status_code)
        return data if isinstance(data, list) else []

    async def get_room(self, token: str) -> dict[str, Any]:
        resp = await self._request("GET", f"{ROOMS_BASE}/{token}")
        return self._check_ocs(resp.json(), status=resp.status_code)

    async def get_participants(self, token: str) -> list[dict[str, Any]]:
        resp = await self._request("GET", f"{ROOMS_BASE}/{token}/participants")
        data = self._check_ocs(resp.json(), status=resp.status_code)
        return data if isinstance(data, list) else []

    async def join_room(self, token: str) -> dict[str, Any]:
        resp = await self._request("POST", f"{ROOMS_BASE}/{token}/participants/active")
        return self._check_ocs(resp.json(), status=resp.status_code)

    # -- chat -----------------------------------------------------------
    async def get_messages(
        self,
        token: str,
        *,
        limit: int = 100,
        last_known_message_id: int = 0,
        look_into_future: int = 0,
        timeout: int = 30,
        set_read_marker: int = 0,
        include_last_known: int = 0,
    ) -> ChatPage:
        """Fetch history (``look_into_future=0``) or long-poll (``=1``)."""
        params: dict[str, Any] = {
            "lookIntoFuture": look_into_future,
            "limit": limit,
            "setReadMarker": set_read_marker,
            "includeLastKnown": include_last_known,
        }
        if last_known_message_id:
            params["lastKnownMessageId"] = last_known_message_id
        if look_into_future:
            params["timeout"] = timeout
        resp = await self._request("GET", f"{OCS_BASE}/chat/{token}", params=params)
        if resp.status_code == 304:
            return ChatPage(messages=[], status=304)
        data = self._check_ocs(resp.json(), status=resp.status_code)
        messages = data if isinstance(data, list) else []
        page = ChatPage(messages=messages, status=resp.status_code)
        try:
            page.last_given = int(resp.headers.get("X-Chat-Last-Given", 0) or 0)
        except ValueError:
            page.last_given = 0
        try:
            page.last_common_read = int(resp.headers.get("X-Chat-Last-Common-Read", 0) or 0)
        except ValueError:
            page.last_common_read = 0
        if not page.last_given and messages:
            try:
                page.last_given = int(messages[-1].get("id", 0) or 0)
            except (TypeError, ValueError):
                pass
        return page

    async def poll_messages(
        self, token: str, last_known_message_id: int, *, timeout: int = 30, limit: int = 100
    ) -> ChatPage:
        """Long-polling helper: ``GET .../chat/{token}?lookIntoFuture=1``."""
        return await self.get_messages(
            token,
            limit=limit,
            last_known_message_id=last_known_message_id,
            look_into_future=1,
            timeout=timeout,
            set_read_marker=0,
        )

    async def send_message(
        self,
        token: str,
        message: str,
        *,
        reply_to: Optional[int] = None,
        silent: bool = False,
        thread_id: Optional[int] = None,
        reference_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message}
        if reply_to is not None:
            payload["replyTo"] = reply_to
        if silent:
            payload["silent"] = True
        if thread_id is not None:
            payload["threadId"] = thread_id
        if reference_id is not None:
            payload["referenceId"] = reference_id
        resp = await self._request("POST", f"{OCS_BASE}/chat/{token}", json=payload)
        return self._check_ocs(resp.json(), status=resp.status_code)

    async def edit_message(self, token: str, message_id: int, message: str) -> dict[str, Any]:
        """Edit an existing message: ``PUT .../chat/{token}/{message_id}``."""
        resp = await self._request(
            "PUT",
            f"{OCS_BASE}/chat/{token}/{message_id}",
            json={"message": message},
        )
        return self._check_ocs(resp.json(), status=resp.status_code)

    async def add_reaction(self, token: str, message_id: int, reaction: str) -> dict[str, Any]:
        """Add an emoji reaction to a message: ``POST .../reaction/{token}/{message_id}``."""
        resp = await self._request(
            "POST",
            f"{OCS_BASE}/reaction/{token}/{message_id}",
            json={"reaction": reaction},
        )
        return self._check_ocs(resp.json(), status=resp.status_code)

    async def remove_reaction(self, token: str, message_id: int, reaction: str) -> dict[str, Any]:
        """Remove an emoji reaction from a message: ``DELETE .../reaction/{token}/{message_id}``."""
        resp = await self._request(
            "DELETE",
            f"{OCS_BASE}/reaction/{token}/{message_id}",
            json={"reaction": reaction},
        )
        return self._check_ocs(resp.json(), status=resp.status_code)

    async def delete_message(self, token: str, message_id: int) -> int:
        resp = await self._request("DELETE", f"{OCS_BASE}/chat/{token}/{message_id}")
        return resp.status_code

    async def set_typing(self, token: str, typing: bool) -> None:
        """Typing indicator: ``POST .../chat/{token}/typing`` (best-effort)."""
        try:
            await self._request(
                "POST", f"{OCS_BASE}/chat/{token}/typing", json={"typing": typing}
            )
        except TalkApiError as exc:
            # Older servers expose no typing endpoint (404) — do not break the agent.
            if exc.status == 404:
                return
            raise

    async def mark_read(self, token: str, last_read_message: Optional[int] = None) -> None:
        payload: dict[str, Any] = {}
        if last_read_message is not None:
            payload["lastReadMessage"] = last_read_message
        await self._request("POST", f"{ROOMS_BASE}/{token}/read", json=payload or None)

    # -- file sharing ---------------------------------------------------
    async def share_file_to_chat(
        self,
        user_path: str,
        room_token: str,
        *,
        caption: str = "",
        message_type: str = "comment",
        reply_to: Optional[int] = None,
    ) -> dict[str, Any]:
        """Share an existing user file into a Talk room (shareType=10)."""
        import json as _json

        metadata: dict[str, Any] = {"messageType": message_type}
        if caption:
            metadata["caption"] = caption
        if reply_to is not None:
            metadata["replyTo"] = reply_to
        form = {
            "shareType": str(SHARE_TYPE_ROOM),
            "shareWith": room_token,
            "path": user_path,
            "talkMetaData": _json.dumps(metadata),
        }
        resp = await self._request(
            "POST",
            SHARE_API,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return self._check_ocs(resp.json(), status=resp.status_code)

    async def share_rich_object(
        self, token: str, object_type: str, object_id: str, meta_data: str = "{}"
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            f"{OCS_BASE}/chat/{token}/share",
            json={"objectType": object_type, "objectId": object_id, "metaData": meta_data},
        )
        return self._check_ocs(resp.json(), status=resp.status_code)

    # -- WebDAV ---------------------------------------------------------
    def _dav_url(self, user_path: str) -> str:
        encoded = "/".join(quote(part) for part in user_path.strip("/").split("/"))
        return self._url(f"/remote.php/dav/files/{self.config.username}/{encoded}")

    async def webdav_mkcol(self, user_path: str) -> None:
        headers = dict(self._auth_headers())
        resp = await self._client.request(
            "MKCOL", self._dav_url(user_path), headers=headers, auth=self._auth
        )
        if resp.status_code not in (201, 405):  # 405 = already exists
            raise TalkApiError(
                f"WebDAV MKCOL failed: HTTP {resp.status_code}",
                status=resp.status_code,
                payload=resp.text,
            )

    async def webdav_put(self, user_path: str, content: bytes, content_type: str) -> str:
        """Upload bytes via WebDAV. Returns the user-relative path."""
        headers = dict(self._auth_headers())
        headers["Content-Type"] = content_type
        resp = await self._client.request(
            "PUT", self._dav_url(user_path), content=content, headers=headers, auth=self._auth
        )
        if resp.status_code not in (200, 201, 204):
            raise TalkApiError(
                f"WebDAV PUT failed: HTTP {resp.status_code}",
                status=resp.status_code,
                payload=resp.text,
            )
        return "/" + user_path.strip("/")

    async def webdav_put_file(
        self, local_path: str, remote_path: str, content_type: Optional[str] = None
    ) -> str:
        import mimetypes

        with open(local_path, "rb") as fh:
            data = fh.read()
        ctype = content_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        return await self.webdav_put(remote_path, data, ctype)

    def basic_auth_header(self) -> str:
        raw = f"{self.config.username}:{self.config.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()
