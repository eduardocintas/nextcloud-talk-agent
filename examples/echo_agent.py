"""Minimal echo agent: replies to mentions and 1-to-1 messages."""

import asyncio
import logging
import os

from nextcloud_talk_agent import AgentConfig, TalkAgent
from nextcloud_talk_agent.models import ChatContext, Message

logging.basicConfig(level=logging.INFO)


class EchoAgent(TalkAgent):
    async def on_mention(self, message: Message, context: ChatContext) -> None:
        await self.reply(context, f"Echo @{message.actor_display_name}: {message.message}")

    async def on_direct_message(self, message: Message, context: ChatContext) -> None:
        await self.reply(context, f"Echo: {message.message}")


async def main() -> None:
    agent = EchoAgent(
        AgentConfig(
            server_url=os.environ["NEXTCLOUD_URL"],
            username=os.environ["NEXTCLOUD_USER"],
            password=os.environ["NEXTCLOUD_APP_PASSWORD"],
            history_limit=int(os.getenv("HISTORY_LIMIT", "15")),
        )
    )
    tokens = [t for t in os.getenv("TALK_ROOMS", "").split(",") if t.strip()] or None
    try:
        await agent.run(tokens)
    finally:
        await agent.aclose()


if __name__ == "__main__":
    asyncio.run(main())
