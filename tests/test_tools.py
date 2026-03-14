"""Tests for the tool system: registry, base tool, screenshot tool, text-format recovery."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from clients.ai_client import _parse_text_tool_calls
from tools.base import ToolResult
from tools.registry import ToolRegistry
from tools.screenshot import ScreenshotTool


def _run(coro):
    return asyncio.run(coro)


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
