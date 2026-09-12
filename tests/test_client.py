"""Tests for TalkClient: auth, chat, typing, share, WebDAV (mocked HTTP)."""

import httpx
import pytest
import respx

from nextcloud_talk_agent.client import TalkApiError, TalkClient, TalkClientConfig

BASE = "https://cloud.example.com"


def make_client(**kw) -> TalkClient:
    cfg = TalkClientConfig(server_url=BASE, username="saul_bot", password="app-pass", **kw)
    return TalkClient(cfg)


def ocs(data, statuscode="200"):
    return {"ocs": {"meta": {"status": "ok", "statuscode": statuscode}, "data": data}}


@respx.mock
@pytest.mark.asyncio
async def test_auth_headers_and_ocs_request():
    client = make_client()
    route = respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v4/room").mock(
        return_value=httpx.Response(200, json=ocs([]))
    )
    rooms = await client.get_rooms()
    assert rooms == []
    req = route.calls[0].request
    assert req.headers["OCS-APIRequest"] == "true"
    assert req.headers["Authorization"].startswith("Basic ")
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_bearer_auth():
    client = make_client(use_bearer=True)
    route = respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v4/room").mock(
        return_value=httpx.Response(200, json=ocs([]))
    )
    await client.get_rooms()
    assert route.calls[0].request.headers["Authorization"] == "Bearer app-pass"
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_get_messages_history_params_and_last_given_header():
    client = make_client()
    msgs = [{"id": 10, "message": "hi"}, {"id": 11, "message": "yo"}]
    route = respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK").mock(
        return_value=httpx.Response(
            200, json=ocs(msgs), headers={"X-Chat-Last-Given": "11"}
        )
    )
    page = await client.get_messages("TOK", limit=15)
    assert [m["id"] for m in page.messages] == [10, 11]
    assert page.last_given == 11
    params = dict(route.calls[0].request.url.params)
    assert params["limit"] == "15"
    assert params["lookIntoFuture"] == "0"
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_poll_uses_look_into_future_1():
    client = make_client()
    route = respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK").mock(
        return_value=httpx.Response(200, json=ocs([{"id": 12}]))
    )
    page = await client.poll_messages("TOK", 11, timeout=30)
    assert dict(route.calls[0].request.url.params)["lookIntoFuture"] == "1"
    assert dict(route.calls[0].request.url.params)["lastKnownMessageId"] == "11"
    assert page.messages[0]["id"] == 12
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_poll_304_returns_empty_page():
    client = make_client()
    respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK").mock(
        return_value=httpx.Response(304)
    )
    page = await client.poll_messages("TOK", 11)
    assert page.status == 304
    assert page.messages == []
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_send_message_payload():
    client = make_client()
    route = respx.post(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK").mock(
        return_value=httpx.Response(201, json=ocs({"id": 99}))
    )
    out = await client.send_message("TOK", "hello", reply_to=5)
    import json

    assert json.loads(route.calls[0].request.content) == {"message": "hello", "replyTo": 5}
    assert out == {"id": 99}
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_set_typing_posts_typing_flag():
    client = make_client()
    route = respx.post(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK/typing").mock(
        return_value=httpx.Response(200, json=ocs({}))
    )
    await client.set_typing("TOK", True)
    import json

    assert route.calls[0].request.url.path.endswith("/chat/TOK/typing")
    assert json.loads(route.calls[0].request.content) == {"typing": True}
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_set_typing_404_is_ignored():
    client = make_client()
    respx.post(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK/typing").mock(
        return_value=httpx.Response(404)
    )
    await client.set_typing("TOK", True)  # must not raise
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_share_file_to_chat_uses_sharetype_10():
    client = make_client()
    route = respx.post(f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares").mock(
        return_value=httpx.Response(200, json=ocs({"id": "s1"}))
    )
    out = await client.share_file_to_chat("/Talk/r.ogg", "TOK", message_type="voice-message")
    assert out == {"id": "s1"}
    body = route.calls[0].request.content.decode()
    assert "shareType=10" in body
    assert "shareWith=TOK" in body
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_webdav_put_and_mkcol():
    client = make_client()
    put = respx.put(f"{BASE}/remote.php/dav/files/saul_bot/Talk/r.ogg").mock(
        return_value=httpx.Response(201)
    )
    mkcol = respx.route(method="MKCOL").mock(return_value=httpx.Response(201))
    await client.webdav_mkcol("Talk")
    path = await client.webdav_put("Talk/r.ogg", b"audio-bytes", "audio/ogg")
    assert path == "/Talk/r.ogg"
    assert put.calls[0].request.headers["Content-Type"] == "audio/ogg"
    assert mkcol.called
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_edit_message():
    client = make_client()
    route = respx.put(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK/42").mock(
        return_value=httpx.Response(200, json=ocs({"id": 42, "message": "edited"}))
    )
    res = await client.edit_message("TOK", 42, "edited")
    assert res["id"] == 42
    assert route.calls[0].request.headers["OCS-APIRequest"] == "true"
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_ocs_error_raises():
    client = make_client()
    respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v4/room").mock(
        return_value=httpx.Response(200, json=ocs([], statuscode="404"))
    )
    with pytest.raises(TalkApiError):
        await client.get_rooms()
    await client.aclose()

