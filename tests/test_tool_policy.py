"""Tests for route-aware tool allowlists."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.routing.tool_policy import route_tool_policy
from runtime.routing.types import Capability, ExecutionClass, RiskTier
from runtime.tooling import build_tool_registry
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

    def test_delete_permanently_is_destructive_and_approval_protected(self):
        registry = build_tool_registry(Settings())
        meta = registry.metadata_by_name()["delete_permanently"]

        assert meta.destructive is True
        assert meta.requires_approval is True
        assert meta.risk_tier == RiskTier.DESTRUCTIVE
