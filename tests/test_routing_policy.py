"""Tests for Eyra's local-first runtime router."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityLevel, ComplexityScorer
from chat.session_state import InteractionStyle, QualityMode
from runtime.models import PreflightResult
from runtime.routing.router import RuntimeRouter
from runtime.routing.trace import format_route_trace, trace_to_dict
from runtime.routing.types import Capability, ExecutionClass, RequestEnvelope, RequestSource, RiskTier
from runtime.tooling import build_tool_registry
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


def _envelope(text: str, *, settings: Settings | None = None, source: RequestSource = RequestSource.TEST, is_worker: bool = False):
    settings = settings or Settings()
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=settings.all_model_names,
        screen_capture_available=True,
        tool_capability_checked_models=settings.all_model_names,
        tool_capable_models=settings.all_model_names,
        vision_capability_checked_models=settings.all_model_names,
        vision_capable_models=[settings.VISION_MODEL or settings.MODEL],
    )
    return RequestEnvelope(
        text=text,
        source=source,
        interaction_style=InteractionStyle.TEXT,
        quality_mode=QualityMode.BALANCED,
        messages=[{"role": "user", "content": text}],
        current_goal=None,
        is_worker=is_worker,
        settings=settings,
        preflight=preflight,
    )


def _route(text: str, *, settings: Settings | None = None, source: RequestSource = RequestSource.TEST, is_worker: bool = False):
    envelope = _envelope(text, settings=settings, source=source, is_worker=is_worker)
    return _run(RuntimeRouter(ComplexityScorer()).route(envelope, build_tool_registry(envelope.settings)))


class TestRuntimeRouter:
    def test_hi_routes_to_text_chat_without_risky_tools(self):
        decision = _route("hi")

        assert decision.execution_class == ExecutionClass.TEXT_CHAT
        assert decision.effort.level == ComplexityLevel.SIMPLE
        assert "write_file" not in decision.tool_policy.allowed_tool_names
        assert decision.risk_tier == RiskTier.NONE

    def test_python_len_routes_to_text_chat_with_moderate_effort(self):
        decision = _route("What does len do in Python?")

        assert decision.execution_class == ExecutionClass.TEXT_CHAT
        assert decision.effort.level in {ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX}
        assert "write_file" not in decision.tool_policy.allowed_tool_names

    def test_screen_request_routes_to_controller_owned_screen_analysis(self):
        decision = _route("what is on the screen?")

        assert decision.execution_class == ExecutionClass.SCREEN_ANALYSIS
        assert {Capability.VISION, Capability.SCREEN_CAPTURE}.issubset(decision.required_capabilities)
        assert decision.tool_policy.allowed_tool_names == frozenset()

    def test_local_pdf_path_routes_to_pdf_analysis(self):
        decision = _route("summarize ~/Downloads/a.pdf")

        assert decision.execution_class == ExecutionClass.PDF_ANALYSIS
        assert {Capability.PDF_READ, Capability.FILE_READ}.issubset(decision.required_capabilities)
        assert decision.tool_policy.allowed_tool_names == frozenset()

    def test_folder_organization_routes_to_background_file_task(self):
        decision = _route("organize my Downloads folder")

        assert decision.execution_class == ExecutionClass.BACKGROUND_TASK
        assert Capability.NATIVE_TOOLS in decision.required_capabilities
        assert Capability.FILE_WRITE in decision.required_capabilities
        assert decision.risk_tier == RiskTier.LOCAL_WRITE

    def test_network_disabled_route_denies_browser_tools_and_explains(self):
        decision = _route("open example.com", settings=Settings(NETWORK_TOOLS_ENABLED=False))

        assert decision.execution_class == ExecutionClass.BROWSER_TASK
        assert decision.tool_policy.allowed_tool_names == frozenset()
        assert "Network tools are disabled" in decision.fallback_plan.on_capability_missing

    def test_network_enabled_route_allows_browser_tools(self):
        decision = _route("open example.com", settings=Settings(NETWORK_TOOLS_ENABLED=True))

        assert decision.execution_class == ExecutionClass.BROWSER_TASK
        assert Capability.NETWORK in decision.required_capabilities
        assert "open_url" in decision.tool_policy.allowed_tool_names

    def test_shell_command_routes_to_shell_policy(self):
        disabled = _route("run command ls")
        enabled = _route("run command ls", settings=Settings(OS_TOOLS_ENABLED=True))

        assert disabled.execution_class == ExecutionClass.TOOL_ASSISTED_CHAT
        assert Capability.SHELL in disabled.required_capabilities
        assert disabled.risk_tier == RiskTier.SHELL_EXECUTION
        assert "run_command" not in disabled.tool_policy.allowed_tool_names
        assert "run_command" in enabled.tool_policy.allowed_tool_names

    def test_os_action_routes_to_os_policy(self):
        decision = _route("click the active dialog button", settings=Settings(OS_TOOLS_ENABLED=True))

        assert decision.execution_class == ExecutionClass.TOOL_ASSISTED_CHAT
        assert Capability.OS_AUTOMATION in decision.required_capabilities
        assert decision.risk_tier == RiskTier.OS_CONTROL
        assert "ui_click" in decision.tool_policy.allowed_tool_names

    def test_mcp_request_routes_to_mcp_policy(self):
        disabled = _route("list mcp tools")
        enabled = _route("list mcp tools", settings=Settings(MCP_TOOLS_ENABLED=True))

        assert disabled.execution_class == ExecutionClass.TOOL_ASSISTED_CHAT
        assert Capability.MCP in disabled.required_capabilities
        assert disabled.risk_tier == RiskTier.DELEGATED_AGENT
        assert "list_mcp_tools" not in disabled.tool_policy.allowed_tool_names
        assert "list_mcp_tools" in enabled.tool_policy.allowed_tool_names

    def test_terminal_and_web_parity_for_same_prompt(self):
        terminal = _route("summarize ~/Downloads/a.pdf", source=RequestSource.TERMINAL)
        web = _route("summarize ~/Downloads/a.pdf", source=RequestSource.WEB)

        assert terminal.execution_class == web.execution_class
        assert terminal.required_capabilities == web.required_capabilities
        assert terminal.selected_model == web.selected_model

    def test_remote_provider_boundary_is_traced(self):
        decision = _route("hi", settings=Settings(API_BASE_URL="https://example.com/v1"))

        assert "remote model provider" in decision.trace.privacy_summary

    def test_trace_format_does_not_include_prompt_text(self):
        decision = _route("read ~/Documents/secret.txt")
        rendered = format_route_trace(decision.trace)
        payload = trace_to_dict(decision.trace)

        assert "secret.txt" not in rendered
        assert "secret.txt" not in str(payload)
        assert "selected model" in rendered
