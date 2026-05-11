"""Tests for message routing and response context construction."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from tools.registry import ToolRegistry
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    def __init__(self):
        self.messages: list[dict] | None = None

    async def stream_with_tools(self, messages, **kwargs):
        self.messages = messages
        yield "ok"

    async def generate_completion_stream(self, messages, **kwargs):
        self.messages = messages
        yield "ok"


class TestProcessTaskStream:
    def test_current_goal_is_added_as_context(self, monkeypatch):
        client = _RecordingClient()
        monkeypatch.setattr("chat.message_handler.get_ai_client", lambda *_, **__: client)

        chunks = _run(_collect(process_task_stream(
            "summarize the plan",
            complexity_scorer=ComplexityScorer(),
            settings=Settings(USE_MOCK_CLIENT=True),
            messages=[{"role": "user", "content": "summarize the plan"}],
            tool_registry=ToolRegistry(),
            current_goal="keep answers focused on release readiness",
        )))

        assert chunks == ["ok"]
        assert client.messages is not None
        system_text = "\n".join(
            msg.get("content", "") for msg in client.messages if msg.get("role") == "system"
        )
        assert "User-set session goal" in system_text
        assert "keep answers focused on release readiness" in system_text


async def _collect(stream):
    return [chunk async for chunk in stream]
