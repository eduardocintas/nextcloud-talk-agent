"""Conversation history assembler / formatter for LLMs."""

from __future__ import annotations

from typing import Any, Iterable

from nextcloud_talk_agent.models import Message

# System / noisy messages that add little value to LLM context.
_SKIP_SYSTEM = {
    "call_started",
    "call_ended",
    "call_joined",
    "call_left",
    "conversation_created",
    "user_added",
    "user_removed",
}


def _display_name(msg: Message) -> str:
    return msg.actor_display_name or msg.actor_id or msg.actor_type


def _content(msg: Message) -> str:
    text = (msg.message or "").strip()
    if msg.attachments:
        names = ", ".join(a.name or a.path for a in msg.attachments if a.name or a.path)
        suffix = f" [attachment: {names}]" if names else " [attachment]"
        text = f"{text}{suffix}" if text else suffix.strip()
    return text


def filter_history(
    messages: Iterable[Message],
    *,
    skip_system: bool = True,
    skip_deleted: bool = True,
    skip_empty: bool = True,
) -> list[Message]:
    """Chronologically sort + drop noise (system/deleted/empty)."""
    ordered = sorted(messages, key=lambda m: m.id)
    out: list[Message] = []
    for msg in ordered:
        if skip_deleted and msg.is_deleted:
            continue
        if skip_system and msg.is_system:
            if msg.system_message in _SKIP_SYSTEM:
                continue
            if not (msg.message or "").strip():
                continue
        if skip_empty and not _content(msg):
            continue
        out.append(msg)
    return out


def build_llm_context(
    messages: Iterable[Message],
    *,
    limit: int = 15,
    skip_system: bool = True,
    agent_actor_id: str = "",
) -> list[dict[str, str]]:
    """Format the last ``limit`` messages as ``[{role, name, content}]``.

    Roles follow the SPEC example: every human turn is ``role=user`` with the
    display name attached. Turns written by the agent itself (``actor_id ==
    agent_actor_id``) are mapped to ``role=assistant`` so the LLM can tell its
    own previous replies apart.
    """
    ordered = filter_history(messages, skip_system=skip_system)
    window = ordered[-limit:] if limit > 0 else ordered
    llm: list[dict[str, str]] = []
    for msg in window:
        role = "assistant" if (agent_actor_id and msg.actor_id == agent_actor_id) else "user"
        llm.append({"role": role, "name": _display_name(msg), "content": _content(msg)})
    return llm


def format_as_text(messages: Iterable[Message], *, limit: int = 15) -> str:
    """Render history as ``Name: content`` lines (oldest first)."""
    ordered = filter_history(messages)[-limit:] if limit > 0 else filter_history(messages)
    return "\n".join(f"{_display_name(m)}: {_content(m)}" for m in ordered)


def parse_raw_messages(payload: Any, token: str = "") -> list[Message]:
    """Parse an OCS chat payload (list of dicts) into :class:`Message`."""
    data = payload if isinstance(payload, list) else []
    messages: list[Message] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if token and not item.get("token"):
            item = {**item, "token": token}
        try:
            messages.append(Message.from_ocs(item))
        except Exception:
            continue
    return messages
