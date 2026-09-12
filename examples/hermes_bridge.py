"""Bridge example: plug any LLM callable into Nextcloud Talk.

Set env vars:
  NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_APP_PASSWORD
  TALK_ROOMS (optional, comma-separated room tokens; empty = all rooms)
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from nextcloud_talk_agent import AgentConfig, TalkAgent
from nextcloud_talk_agent.models import ChatContext, Message

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hermes_bridge")


async def my_llm(messages: list[dict[str, str]]) -> str:
    """Replace with Hermes / LangChain / OpenAI call. Receives SPEC §3.3 format:

    [{"role": "user", "name": "Raul Moreno", "content": "..."}, ...]
    """
    last = messages[-1]["content"] if messages else ""
    return f"Procesado ({len(messages)} msgs de contexto). Último: {last}"


class HermesBridge(TalkAgent):
    def __init__(self, *args, llm: Callable[[list[dict[str, str]]], Awaitable[str]], **kwargs):
        super().__init__(*args, **kwargs)
        self._llm = llm

    async def _answer(self, message: Message, context: ChatContext) -> None:
        # typing indicator is set by TalkAgent.typing around callbacks,
        # here we additionally guard long LLM calls explicitly.
        async with self.typing(message.token):
            answer = await self._llm(context.llm_messages)
        await self.reply(context, answer)

    async def on_mention(self, message: Message, context: ChatContext) -> None:
        await self._answer(message, context)

    async def on_direct_message(self, message: Message, context: ChatContext) -> None:
        await self._answer(message, context)


async def main() -> None:
    agent = HermesBridge(
        AgentConfig(
            server_url=os.environ["NEXTCLOUD_URL"],
            username=os.environ["NEXTCLOUD_USER"],
            password=os.environ["NEXTCLOUD_APP_PASSWORD"],
        ),
        llm=my_llm,
    )
    tokens = [t for t in os.getenv("TALK_ROOMS", "").split(",") if t.strip()] or None
    try:
        await agent.run(tokens)
    finally:
        await agent.aclose()


if __name__ == "__main__":
    asyncio.run(main())
