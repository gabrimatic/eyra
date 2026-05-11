"""Tests for shared runtime tool registry construction."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.tooling import build_tool_registry
from utils.settings import Settings


def _tool_names(registry):
    return {tool["function"]["name"] for tool in registry.to_openai_tools()}


class TestBuildToolRegistry:
    def test_local_default_tools_stay_available_without_optional_bridges(self):
        registry = build_tool_registry(Settings())

        names = _tool_names(registry)
        assert "discover_capabilities" in names
        assert "get_voice_context" in names
        assert "get_current_time" in names
        assert "take_screenshot" in names
        assert "read_file" in names
        assert "run_command" not in names
        assert "run_agent_task" not in names
        assert "call_mcp_tool" not in names
        assert "web_search" not in names
        assert "manage_launch_agent" not in names

    def test_optional_tools_are_gated_by_settings(self):
        registry = build_tool_registry(
            Settings(
                NETWORK_TOOLS_ENABLED=True,
                OS_TOOLS_ENABLED=True,
                AGENT_TOOLS_ENABLED=True,
                MCP_TOOLS_ENABLED=True,
            )
        )

        names = _tool_names(registry)
        assert "run_command" in names
        assert "fetch_url" in names
        assert "list_processes" in names
        assert "get_system_snapshot" in names
        assert "get_launch_agent_status" in names
        assert "manage_launch_agent" in names
        assert "open_app" in names
        assert "show_notification" in names
        assert "set_clipboard_text" in names
        assert "get_agent_status" in names
        assert "list_agent_sessions" in names
        assert "get_agent_session_content" in names
        assert "list_codex_sessions" in names
        assert "list_openclaw_sessions" in names
        assert "get_codex_session_content" in names
        assert "get_openclaw_status" in names
        assert "run_agent_task" in names
        assert "run_codex_task" in names
        assert "run_openclaw_agent" in names
        assert "list_mcp_tools" in names
        assert "call_mcp_tool" in names
        assert "web_search" in names
