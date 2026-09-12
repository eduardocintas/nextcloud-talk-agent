"""Tests for TalkListener long-polling + TalkAgent routing + MediaHelper."""

import httpx
import pytest
import respx

from nextcloud_talk_agent.agent import AgentConfig, TalkAgent
from nextcloud_talk_agent.client import TalkClient, TalkClientConfig
from nextcloud_talk_agent.listener import TalkListener
from nextcloud_talk_agent.media import MediaHelper
from nextcloud_talk_agent.models import ChatContext, Message

BASE = "https://cloud.example.com"


def ocs(data):
    return {"ocs": {"meta": {"status": "ok", "statuscode": "200"}, "data": data}}


def chat_msg(id: int, actor: str, text: str, **kw):
    d = {
        "id": id,
        "token": "TOK",
        "actorType": "users",
        "actorId": actor,
        "actorDisplayName": actor,
        "message": text,
        "messageType": "comment",
        "timestamp": 1700000000,
        "isReplyable": True,
    }
    d.update(kw)
    return d


def make_client() -> TalkClient:
    return TalkClient(TalkClientConfig(server_url=BASE, username="saul_bot", password="x"))


@pytest.mark.asyncio
async def test_listener_filters_own_system_deleted_and_dedupes():
    client = make_client()
    listener = TalkListener(client, agent_actor_id="saul_bot")
    listener.seed("TOK", 0)
    received: list[Message] = []
    listener.on_message(lambda m: received.append(m) or _noop())

    async def _noop():
        return None

    payload = [
        chat_msg(1, "raul", "hello"),
        chat_msg(2, "saul_bot", "my own reply"),  # own -> skip
        chat_msg(3, "x", "", messageType="system", systemMessage="call_started"),
        chat_msg(4, "x", "gone", messageType="comment_deleted"),
    ]

    async def fake_poll(token, last_id, timeout=30, limit=100):
        from nextcloud_talk_agent.client import ChatPage

        return ChatPage(messages=payload, last_given=4)

    client.poll_messages = fake_poll  # type: ignore[method-assign]
    fresh = await listener.poll_once("TOK")
    assert [m.id for m in fresh] == [1]
    # second identical poll -> deduped
    fresh2 = await listener.poll_once("TOK")
    assert fresh2 == []
    await client.aclose()


@pytest.mark.asyncio
async def test_listener_advances_last_known_from_header():
    client = make_client()
    listener = TalkListener(client, agent_actor_id="saul_bot")
    listener.seed("TOK", 10)

    async def fake_poll(token, last_id, timeout=30, limit=100):
        from nextcloud_talk_agent.client import ChatPage

        return ChatPage(messages=[], last_given=12, status=304)

    client.poll_messages = fake_poll  # type: ignore[method-assign]
    assert await listener.poll_once("TOK") == []
    assert listener._last_known["TOK"] == 12
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_agent_routes_mention_direct_and_all():
    respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v4/room/ONE").mock(
        return_value=httpx.Response(200, json=ocs({"token": "ONE", "type": 1}))
    )
    respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v4/room/GRP").mock(
        return_value=httpx.Response(200, json=ocs({"token": "GRP", "type": 2}))
    )
    respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/ONE").mock(
        return_value=httpx.Response(200, json=ocs([]))
    )
    respx.get(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/GRP").mock(
        return_value=httpx.Response(200, json=ocs([]))
    )
    respx.post(url__regex=r".*/chat/.*/typing").mock(
        return_value=httpx.Response(200, json=ocs({}))
    )

    events: list[str] = []

    class A(TalkAgent):
        async def on_mention(self, message: Message, context: ChatContext):
            events.append(f"mention:{message.id}")

        async def on_direct_message(self, message: Message, context: ChatContext):
            events.append(f"direct:{message.id}")

        async def on_message(self, message: Message, context: ChatContext):
            events.append(f"all:{message.id}")

    agent = A(
        AgentConfig(server_url=BASE, username="saul_bot", password="x", respond_to_all=True)
    )
    mention = Message.from_ocs(
        chat_msg(
            1,
            "edu",
            "@saul_bot hi",
            token="GRP",
            messageParameters={"mention-user-saul_bot": {"type": "user", "id": "saul_bot"}},
        )
    )
    await agent._route(mention)
    direct = Message.from_ocs(chat_msg(2, "raul", "hola", token="ONE"))
    await agent._route(direct)
    assert "mention:1" in events
    assert "all:1" in events
    assert "direct:2" in events
    await agent.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_media_send_voice_message_uploads_then_shares():
    respx.route(method="MKCOL").mock(return_value=httpx.Response(201))
    put = respx.put(f"{BASE}/remote.php/dav/files/saul_bot/Talk/response.ogg").mock(
        return_value=httpx.Response(201)
    )
    share = respx.post(f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares").mock(
        return_value=httpx.Response(200, json=ocs({"id": "s1"}))
    )
    client = make_client()
    media = MediaHelper(client)
    result = await media.send_audio_bytes("response.ogg", b"fake-ogg", "TOK")
    assert result.user_path == "/Talk/response.ogg"
    assert put.called
    body = share.calls[0].request.content.decode()
    assert "voice-message" in body
    await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_agent_typing_context_manager_calls_true_then_false():
    calls: list[str] = []

    def handler(request):
        import json

        calls.append(json.loads(request.content)["typing"])
        return httpx.Response(200, json=ocs({}))

    respx.post(f"{BASE}/ocs/v2.php/apps/spreed/api/v1/chat/TOK/typing").mock(side_effect=handler)
    agent = TalkAgent(AgentConfig(server_url=BASE, username="saul_bot", password="x"))
    async with agent.typing("TOK"):
        pass
    assert calls == [True, False]
    await agent.aclose()
