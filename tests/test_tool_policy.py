"""Tests for route-aware tool allowlists."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.routing.tool_policy import route_tool_policy
from runtime.routing.types import Capability, ExecutionClass, RiskTier
from runtime.tooling import build_tool_registry
from tools.base import BaseTool, ToolResult
from tools.registry import ToolRegistry
from utils.settings import Settings


def _policy(settings: Settings, execution_class: ExecutionClass, capabilities: set[Capability], risk: RiskTier):
    return route_tool_policy(
        execution_class=execution_class,
        required_capabilities=frozenset(capabilities),
        risk_tier=risk,
        settings=settings,
        tool_registry=build_tool_registry(settings),
    )


class TestToolPolicy:
    def test_text_chat_does_not_expose_mutating_filesystem_tools(self):
        policy = _policy(Settings(), ExecutionClass.TEXT_CHAT, {Capability.TEXT}, RiskTier.NONE)

        assert "get_current_time" in policy.allowed_tool_names
        assert "write_file" not in policy.allowed_tool_names
        assert "delete_permanently" not in policy.allowed_tool_names

    def test_screen_analysis_exposes_no_model_chosen_screenshot_tools(self):
        policy = _policy(
            Settings(),
            ExecutionClass.SCREEN_ANALYSIS,
            {Capability.TEXT, Capability.VISION, Capability.SCREEN_CAPTURE},
            RiskTier.PRIVATE_READ,
        )

        assert policy.allowed_tool_names == frozenset()
        assert "take_screenshot" in policy.denied_tool_reasons

    def test_pdf_analysis_exposes_no_extra_tools(self):
        policy = _policy(
            Settings(),
            ExecutionClass.PDF_ANALYSIS,
            {Capability.TEXT, Capability.PDF_READ, Capability.FILE_READ},
            RiskTier.PRIVATE_READ,
        )

        assert policy.allowed_tool_names == frozenset()
        assert "read_pdf" in policy.denied_tool_reasons

    def test_network_tools_unavailable_when_setting_disabled(self):
        policy = _policy(
            Settings(NETWORK_TOOLS_ENABLED=False),
            ExecutionClass.BROWSER_TASK,
            {Capability.TEXT, Capability.NETWORK, Capability.BROWSER_CONTROL},
            RiskTier.NETWORKED,
        )

        assert "web_search" not in policy.allowed_tool_names

    def test_network_tools_available_when_setting_enabled(self):
        policy = _policy(
            Settings(NETWORK_TOOLS_ENABLED=True),
            ExecutionClass.BROWSER_TASK,
            {Capability.TEXT, Capability.NETWORK, Capability.BROWSER_CONTROL},
            RiskTier.NETWORKED,
        )

        assert "web_search" in policy.allowed_tool_names
        assert "open_url" in policy.allowed_tool_names

    def test_background_file_route_does_not_expose_unrelated_private_reads(self):
        policy = _policy(
            Settings(OS_TOOLS_ENABLED=True),
            ExecutionClass.BACKGROUND_TASK,
            {Capability.TEXT, Capability.NATIVE_TOOLS, Capability.FILE_READ, Capability.FILE_WRITE},
            RiskTier.LOCAL_WRITE,
        )

        assert "read_file" in policy.allowed_tool_names
        assert "write_file" in policy.allowed_tool_names
        assert "read_clipboard" not in policy.allowed_tool_names
        assert "take_screenshot" not in policy.allowed_tool_names
        assert "extract_screen_text" not in policy.allowed_tool_names

    def test_read_only_background_file_route_does_not_expose_mutating_filesystem_tools(self):
        policy = _policy(
            Settings(),
            ExecutionClass.BACKGROUND_TASK,
            {Capability.TEXT, Capability.NATIVE_TOOLS, Capability.FILE_READ},
            RiskTier.LOW_READ_ONLY,
        )

        assert "read_file" in policy.allowed_tool_names
        assert "list_directory" in policy.allowed_tool_names
        assert "compare_files" in policy.allowed_tool_names
        assert "write_file" not in policy.allowed_tool_names
        assert "edit_file" not in policy.allowed_tool_names
        assert "append_file" not in policy.allowed_tool_names
        assert "copy_path" not in policy.allowed_tool_names
        assert policy.denied_tool_reasons["write_file"] == "mutating filesystem tools require a file-write route"

    def test_clipboard_read_is_private_read_not_os_gated(self):
        generic = _policy(
            Settings(),
            ExecutionClass.TEXT_CHAT,
            {Capability.TEXT},
            RiskTier.NONE,
        )
        clipboard = _policy(
            Settings(OS_TOOLS_ENABLED=False),
            ExecutionClass.TOOL_ASSISTED_CHAT,
            {Capability.TEXT, Capability.NATIVE_TOOLS, Capability.CLIPBOARD_READ},
            RiskTier.PRIVATE_READ,
        )

        assert "read_clipboard" not in generic.allowed_tool_names
        assert "read_clipboard" in clipboard.allowed_tool_names

    def test_realtime_route_exposes_no_registered_local_tools_by_default(self):
        policy = _policy(
            Settings(),
            ExecutionClass.REALTIME_VOICE_TURN,
            {Capability.TEXT, Capability.VOICE_RESPONSE},
            RiskTier.LOW_READ_ONLY,
        )

        assert policy.allowed_tool_names == frozenset()

    def test_unknown_registered_tool_is_blocked_by_default(self):
        class UnknownTool(BaseTool):
            name = "mystery_tool"
            description = "A deliberately unclassified test tool."
            parameters = {"type": "object", "properties": {}}

            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(content="ok")

        registry = ToolRegistry()
        registry.register(UnknownTool())
        policy = route_tool_policy(
            execution_class=ExecutionClass.TOOL_ASSISTED_CHAT,
            required_capabilities=frozenset({Capability.TEXT, Capability.NATIVE_TOOLS}),
            risk_tier=RiskTier.LOW_READ_ONLY,
            settings=Settings(),
            tool_registry=registry,
        )

        assert "mystery_tool" not in policy.allowed_tool_names
        assert policy.denied_tool_reasons["mystery_tool"] == "tool lacks explicit safe route classification"

    def test_agent_read_tools_require_agent_route(self):
        normal = _policy(
            Settings(AGENT_TOOLS_ENABLED=True),
            ExecutionClass.TOOL_ASSISTED_CHAT,
            {Capability.TEXT, Capability.NATIVE_TOOLS},
            RiskTier.LOW_READ_ONLY,
        )
        agent = _policy(
            Settings(AGENT_TOOLS_ENABLED=True),
            ExecutionClass.CODING_AGENT_TASK,
            {Capability.TEXT, Capability.AGENT_READ},
            RiskTier.PRIVATE_READ,
        )

        assert "get_agent_status" not in normal.allowed_tool_names
        assert "list_agent_sessions" not in normal.allowed_tool_names
        assert "get_agent_status" in agent.allowed_tool_names
        assert "list_agent_sessions" in agent.allowed_tool_names

    def test_os_agent_and_mcp_tools_require_opt_in(self):
        disabled = _policy(
            Settings(OS_TOOLS_ENABLED=True, AGENT_TOOLS_ENABLED=True, MCP_TOOLS_ENABLED=True),
            ExecutionClass.TEXT_CHAT,
            {Capability.TEXT},
            RiskTier.NONE,
        )

        assert "run_command" not in disabled.allowed_tool_names
        assert "run_agent_task" not in disabled.allowed_tool_names
        assert "call_mcp_tool" not in disabled.allowed_tool_names

    def test_os_route_does_not_expose_shell_command_tool(self):
        policy = _policy(
            Settings(OS_TOOLS_ENABLED=True),
            ExecutionClass.TOOL_ASSISTED_CHAT,
            {Capability.TEXT, Capability.NATIVE_TOOLS, Capability.OS_AUTOMATION},
            RiskTier.OS_CONTROL,
        )

        assert "ui_click" in policy.allowed_tool_names
        assert "run_command" not in policy.allowed_tool_names
        assert policy.denied_tool_reasons["run_command"] == "shell tools require a shell route"

    def test_agent_status_route_does_not_expose_agent_run_tools(self):
        policy = _policy(
            Settings(AGENT_TOOLS_ENABLED=True),
            ExecutionClass.CODING_AGENT_TASK,
            {Capability.TEXT, Capability.AGENT_READ},
            RiskTier.PRIVATE_READ,
        )

        assert "get_agent_status" in policy.allowed_tool_names
        assert "list_agent_sessions" in policy.allowed_tool_names
        assert "run_agent_task" not in policy.allowed_tool_names
        assert policy.denied_tool_reasons["run_agent_task"] == (
            "agent delegation tools require an agent delegation route"
        )

    def test_delete_permanently_is_destructive_and_approval_protected(self):
        registry = build_tool_registry(Settings())
        meta = registry.metadata_by_name()["delete_permanently"]

        assert meta.destructive is True
        assert meta.requires_approval is True
        assert meta.risk_tier == RiskTier.DESTRUCTIVE

    def test_all_registered_tools_have_explicit_route_classification(self):
        registry = build_tool_registry(
            Settings(
                NETWORK_TOOLS_ENABLED=True,
                OS_TOOLS_ENABLED=True,
                AGENT_TOOLS_ENABLED=True,
                MCP_TOOLS_ENABLED=True,
            )
        )

        unclassified = [
            name
            for name, meta in registry.metadata_by_name().items()
            if not meta.capabilities and not meta.allowed_execution_classes
        ]

        assert unclassified == []
