"""Tests for the tool system: registry, base tool, screenshot tool, text-format recovery."""

import asyncio
import json
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from clients.ai_client import AIClient, _is_tools_unsupported_error, _parse_text_tool_calls
from tools.base import BaseTool, ToolResult
from tools.registry import ToolRegistry
from tools.screenshot import ScreenshotTool
from utils.semantic_history import build_semantic_history, semantic_history_entry
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


class _EchoTool(BaseTool):
    name = "write_file"
    description = "Test tool"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self):
        self.calls = 0

    async def execute(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(content="ok")


class _ClockTool(BaseTool):
    name = "get_current_time"
    description = "Test clock"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self):
        self.calls = 0

    async def execute(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(content="time ok")


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def __aiter__(self):
        for chunk in self._chunks:
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=chunk, tool_calls=None),
                    )
                ]
            )


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = [list(response) for response in responses]
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self.responses.pop(0))


class TestToolResult:
    def test_text_only(self):
        r = ToolResult(content="hello")
        assert r.content == "hello"
        assert r.image_base64 is None

    def test_with_image(self):
        r = ToolResult(content="captured", image_base64="abc123")
        assert r.image_base64 == "abc123"


class TestToolRegistry:
    def test_register_and_list(self):
        registry = ToolRegistry()
        tool = ScreenshotTool()
        registry.register(tool)
        tools = registry.to_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "take_screenshot"

    def test_execute_known_tool(self):
        registry = ToolRegistry()
        tool = ScreenshotTool()
        registry.register(tool)

        with patch("tools.screenshot.capture_screenshot_and_encode", new_callable=AsyncMock, return_value="base64img"), \
             patch("tools.screenshot._get_active_app", new_callable=AsyncMock, return_value="Terminal"), \
             patch("tools.screenshot._get_active_window", new_callable=AsyncMock, return_value="zsh"):
            result = _run(registry.execute("take_screenshot", "{}"))

        assert result.image_base64 == "base64img"
        assert "Terminal" in result.content

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        result = _run(registry.execute("nonexistent", "{}"))
        assert "Unknown tool" in result.content

    def test_execute_rejects_invalid_json_arguments(self):
        registry = ToolRegistry()
        registry.register(ScreenshotTool())
        result = _run(registry.execute("take_screenshot", "{bad json"))
        assert "Invalid JSON" in result.content

    def test_execute_log_redacts_argument_values(self, caplog):
        registry = ToolRegistry()
        registry.register(_EchoTool())
        caplog.set_level(logging.INFO, logger="tools.registry")

        secret = "sk-test-secret-token"
        result = _run(registry.execute("write_file", json.dumps({
            "path": "~/private-note.txt",
            "content": f"token={secret}",
            "api_key": secret,
        })))

        assert result.content == "ok"
        assert secret not in caplog.text
        assert "~/private-note.txt" not in caplog.text
        assert "write_file" in caplog.text
        assert "content" in caplog.text

    def test_empty_registry(self):
        registry = ToolRegistry()
        assert registry.to_openai_tools() == []


class TestScreenshotTool:
    def test_openai_schema(self):
        tool = ScreenshotTool()
        schema = tool.to_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "take_screenshot"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_execute_returns_image(self):
        tool = ScreenshotTool()

        with patch("tools.screenshot.capture_screenshot_and_encode", new_callable=AsyncMock, return_value="img_data"), \
             patch("tools.screenshot._get_active_app", new_callable=AsyncMock, return_value="Safari"), \
             patch("tools.screenshot._get_active_window", new_callable=AsyncMock, return_value="Google"):
            result = _run(tool.execute())

        assert result.image_base64 == "img_data"
        assert "Safari" in result.content
        assert "Google" in result.content

    def test_execute_handles_no_app_info(self):
        tool = ScreenshotTool()

        with patch("tools.screenshot.capture_screenshot_and_encode", new_callable=AsyncMock, return_value="img"), \
             patch("tools.screenshot._get_active_app", new_callable=AsyncMock, return_value=None), \
             patch("tools.screenshot._get_active_window", new_callable=AsyncMock, return_value=None):
            result = _run(tool.execute())

        assert result.image_base64 == "img"
        assert "Screenshot captured" in result.content


class TestTextToolCallParser:
    """Tests for _parse_text_tool_calls — recovery of text-format tool calls from Ollama bug #14745."""

    def test_function_format(self):
        content = '<function=get_weather>{"location": "Tokyo"}</function>'
        result = _parse_text_tool_calls(content)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert result[0]["arguments"] == '{"location": "Tokyo"}'

    def test_tool_call_format(self):
        content = '<tool_call>\n{"name": "read_file", "arguments": {"path": "~/notes.txt"}}\n</tool_call>'
        result = _parse_text_tool_calls(content)
        assert result is not None
        assert result[0]["name"] == "read_file"

    def test_tool_call_format_with_nested_arguments(self):
        content = (
            '<tool_call>{"name": "write_file", "arguments": '
            '{"path": "~/notes.json", "content": "{\\"enabled\\": true}"}}</tool_call>'
        )
        result = _parse_text_tool_calls(content)
        assert result is not None
        assert result[0]["name"] == "write_file"
        assert json.loads(result[0]["arguments"]) == {
            "path": "~/notes.json",
            "content": '{"enabled": true}',
        }

    def test_multiple_calls(self):
        content = (
            '<function=get_weather>{"location": "Tokyo"}</function>'
            '<function=get_current_time>{}</function>'
        )
        result = _parse_text_tool_calls(content)
        assert result is not None
        assert len(result) == 2

    def test_invalid_json(self):
        content = '<function=get_weather>not json</function>'
        result = _parse_text_tool_calls(content)
        assert result is None

    def test_no_tool_calls(self):
        content = "Sure, I can help you with that!"
        result = _parse_text_tool_calls(content)
        assert result is None

    def test_empty_content(self):
        result = _parse_text_tool_calls("")
        assert result is None

    def test_generated_ids(self):
        content = '<function=write_file>{"path": "~/test.txt", "content": "hello"}</function>'
        result = _parse_text_tool_calls(content)
        assert result[0]["id"].startswith("text_")


class TestToolFallbackDetection:
    def test_detects_ollama_tools_unsupported_error(self):
        error = Exception("registry.ollama.ai/library/gemma3:4b does not support tools")
        assert _is_tools_unsupported_error(error) is True

    def test_ignores_unrelated_errors(self):
        assert _is_tools_unsupported_error(Exception("connection failed")) is False


class TestTextToolCallRecovery:
    def test_valid_allowed_text_format_tool_call_executes(self):
        clock = _ClockTool()
        registry = ToolRegistry()
        registry.register(clock)
        completions = _FakeCompletions([
            ['<function=get_current_time>{}</function>'],
            ["done"],
        ])
        client = AIClient(Settings(API_KEY="test"))
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        chunks = _run(_collect(client.stream_with_tools(
            [{"role": "user", "content": "what time is it?"}],
            tools=registry,
            allowed_tool_names={"get_current_time"},
        )))

        assert "".join(chunks) == "done"
        assert clock.calls == 1
        assert len(completions.calls) == 2

    def test_denied_text_format_tool_call_is_refused_not_executed(self):
        clock = _ClockTool()
        writer = _EchoTool()
        registry = ToolRegistry()
        registry.register(clock)
        registry.register(writer)
        completions = _FakeCompletions([
            ['<function=write_file>{"path": "/Users/example/secret.txt", "content": "token=secret"}</function>'],
        ])
        client = AIClient(Settings(API_KEY="test"))
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        chunks = _run(_collect(client.stream_with_tools(
            [{"role": "user", "content": "write a file"}],
            tools=registry,
            allowed_tool_names={"get_current_time"},
        )))

        assert "not allowed" in "".join(chunks)
        assert writer.calls == 0
        assert len(completions.calls) == 1

    def test_malformed_text_format_tool_call_is_preserved(self):
        clock = _ClockTool()
        registry = ToolRegistry()
        registry.register(clock)
        completions = _FakeCompletions([
            ['<function=get_current_time>not json</function>'],
        ])
        client = AIClient(Settings(API_KEY="test"))
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        chunks = _run(_collect(client.stream_with_tools(
            [{"role": "user", "content": "show an example"}],
            tools=registry,
            allowed_tool_names={"get_current_time"},
        )))

        assert "<function=get_current_time>not json</function>" in "".join(chunks)
        assert clock.calls == 0

    def test_xml_like_normal_text_is_preserved(self):
        clock = _ClockTool()
        registry = ToolRegistry()
        registry.register(clock)
        completions = _FakeCompletions([
            ["Use <function=example> in docs when explaining parser syntax."],
        ])
        client = AIClient(Settings(API_KEY="test"))
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        chunks = _run(_collect(client.stream_with_tools(
            [{"role": "user", "content": "explain syntax"}],
            tools=registry,
            allowed_tool_names={"get_current_time"},
        )))

        assert "Use <function=example>" in "".join(chunks)
        assert clock.calls == 0


class TestSemanticHistory:
    def test_semantic_history_omits_raw_tool_arguments_paths_and_secrets(self):
        messages = [
            {"role": "user", "content": "Read /Users/example/private.txt with token=secret-token"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/Users/example/private.txt", "api_key": "sk-secretsecretsecret"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Clipboard: token=secret-token\nFile: /Users/example/private.txt",
            },
        ]

        history = build_semantic_history(messages)
        rendered = json.dumps(history)

        assert "read_file" in rendered
        assert "arguments" not in rendered
        assert "/Users/example" not in rendered
        assert "secret-token" not in rendered
        assert "sk-secret" not in rendered
        assert "~/[user]" in rendered
        assert "[REDACTED]" in rendered

    def test_semantic_history_replaces_images_with_marker(self):
        entry = semantic_history_entry({
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
            ],
        })

        assert entry["content"] == "Look at this\n[image omitted]"


async def _collect(stream):
    return [chunk async for chunk in stream]
