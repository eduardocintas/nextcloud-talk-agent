import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from nextcloud_talk_agent.client import ChatPage
from nextcloud_talk_agent.listener import ListenerConfig, TalkListener
from nextcloud_talk_agent.models import Message

@pytest.mark.asyncio
async def test_dynamic_room_discovery():
    client = MagicMock()
    # Initial rooms: ["room1"]
    # After second check: ["room1", "room2"]
    call_count = 0
    async def mock_get_rooms():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [{"token": "room1"}]
        return [{"token": "room1"}, {"token": "room2"}]

    client.get_rooms = AsyncMock(side_effect=mock_get_rooms)
    client.get_messages = AsyncMock(return_value=ChatPage(messages=[], status=200))
    async def mock_poll(*args, **kwargs):
        await asyncio.sleep(0.01)
        return ChatPage(messages=[], status=304)

    client.poll_messages = AsyncMock(side_effect=mock_poll)

    listener = TalkListener(client, agent_actor_id="test_agent")
    
    # Run listen_all_rooms with dynamic room discovery
    task = asyncio.create_task(listener.listen_all_rooms(room_sync_interval=0.1))
    await asyncio.sleep(0.3)
    
    assert "room1" in listener.active_room_tokens
    assert "room2" in listener.active_room_tokens
    
    listener.stop()
    await asyncio.wait_for(task, timeout=1.0)
