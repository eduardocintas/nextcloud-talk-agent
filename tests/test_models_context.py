"""Tests for models + context (SPEC §3.3)."""

from nextcloud_talk_agent.context import build_llm_context, filter_history, format_as_text
from nextcloud_talk_agent.models import Message, Room


def _msg(id: int, actor: str, text: str, **kw) -> Message:
    return Message.from_ocs(
        {
            "id": id,
            "token": "tok",
            "actorType": "users",
            "actorId": actor,
            "actorDisplayName": actor,
            "message": text,
            "messageType": "comment",
            "timestamp": 1700000000 + id,
            **kw,
        }
    )


def test_message_from_ocs_with_file_attachment():
    m = Message.from_ocs(
        {
            "id": 5,
            "actorType": "users",
            "actorId": "raul",
            "actorDisplayName": "Raul Moreno",
            "message": "contract",
            "messageType": "comment",
            "timestamp": 1,
            "messageParameters": {
                "file": {
                    "id": "123",
                    "name": "contract.pdf",
                    "path": "Talk/contract.pdf",
                    "mimetype": "application/pdf",
                    "size": 42,
                }
            },
        }
    )
    assert len(m.attachments) == 1
    assert m.attachments[0].name == "contract.pdf"


def test_mentions_detection():
    m = Message.from_ocs(
        {
            "id": 1,
            "actorId": "edu",
            "actorDisplayName": "Eduardo",
            "message": "@saul can you analyze?",
            "messageType": "comment",
            "timestamp": 1,
            "messageParameters": {
                "mention-user-saul": {"type": "user", "id": "saul", "name": "Saul"}
            },
        }
    )
    assert m.mentions("saul") is True
    assert m.mentions("other") is False


def test_build_llm_context_spec_example():
    msgs = [
        _msg(1, "raul", "Here is the draft of the contract."),
        _msg(2, "edu", "@saul can you analyze the jurisdiction clause?"),
    ]
    ctx = build_llm_context(msgs, limit=15)
    assert ctx == [
        {"role": "user", "name": "raul", "content": "Here is the draft of the contract."},
        {
            "role": "user",
            "name": "edu",
            "content": "@saul can you analyze the jurisdiction clause?",
        },
    ]


def test_build_llm_context_own_turns_are_assistant_and_window_applies():
    msgs = [_msg(i, "raul" if i % 2 else "saul_bot", f"m{i}") for i in range(1, 21)]
    ctx = build_llm_context(msgs, limit=15, agent_actor_id="saul_bot")
    assert len(ctx) == 15
    assert ctx[0]["content"] == "m6"  # window = last 15 of 20
    assistants = [c for c in ctx if c["role"] == "assistant"]
    assert assistants and all(c["name"] == "saul_bot" for c in assistants)


def test_filter_skips_system_and_deleted():
    msgs = [
        _msg(1, "a", "hi"),
        _msg(2, "a", "", messageType="system", systemMessage="call_started"),
        _msg(3, "a", "gone", messageType="comment_deleted"),
    ]
    assert [m.id for m in filter_history(msgs)] == [1]
    assert format_as_text(msgs) == "a: hi"


def test_room_is_direct():
    assert Room.from_ocs({"token": "t", "type": 1}).is_direct is True
    assert Room.from_ocs({"token": "t", "type": 2}).is_direct is False
