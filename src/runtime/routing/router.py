"""Deterministic local-first runtime router."""

from __future__ import annotations

import re

from chat.complexity_scorer import ComplexityScorer
from runtime.intents import (
    extract_pdf_path,
    needs_screen_context,
    requires_filesystem,
    requires_model_driven_tools,
    requires_network,
    should_background_task,
)
from runtime.privacy import evaluate_privacy_boundary
from runtime.routing.effort import estimate_effort
from runtime.routing.fallback import build_fallback_plan
from runtime.routing.model_registry import ModelRegistry
from runtime.routing.tool_policy import route_tool_policy
from runtime.routing.trace import log_route_trace
from runtime.routing.types import (
    Capability,
    ExecutionClass,
    RequestEnvelope,
    RiskTier,
    RoutingDecision,
    RoutingTrace,
)
from tools.registry import ToolRegistry


class RuntimeRouter:
    """Plan local execution without network calls or model calls."""

    def __init__(self, scorer: ComplexityScorer):
        self.scorer = scorer

    async def route(
        self,
        envelope: RequestEnvelope,
        tool_registry: ToolRegistry | None = None,
    ) -> RoutingDecision:
        text = envelope.text or ""
        effort = await estimate_effort(self.scorer, text, envelope.messages)
        execution_class = self._execution_class(envelope)
        required_capabilities = self._required_capabilities(envelope, execution_class)
        risk_tier = self._risk_tier(text, execution_class)
        fallback_plan = build_fallback_plan(execution_class)
        model_registry = ModelRegistry(envelope.settings, envelope.preflight)
        selected_model, selected_model_reason = model_registry.select_model(
            quality_mode=envelope.quality_mode,
            effort_level=effort.level,
            required_capabilities=required_capabilities,
            is_worker=envelope.is_worker,
        )
        if execution_class == ExecutionClass.BROWSER_TASK and not envelope.settings.NETWORK_TOOLS_ENABLED:
            selected_model_reason = f"{selected_model_reason}; network capability denied by settings"
        tool_policy = route_tool_policy(
            execution_class=execution_class,
            required_capabilities=required_capabilities,
            risk_tier=risk_tier,
            settings=envelope.settings,
            tool_registry=tool_registry,
        )
        privacy_summary = self._privacy_summary(envelope, execution_class)
        trace = RoutingTrace(
            source=envelope.source,
            quality_mode=envelope.quality_mode,
            execution_class=execution_class,
            effort_level=effort.level,
            selected_model=selected_model,
            selected_model_reason=selected_model_reason,
            required_capabilities=required_capabilities,
            allowed_tools=tuple(sorted(tool_policy.allowed_tool_names)),
            denied_tools=dict(sorted(tool_policy.denied_tool_reasons.items())),
            risk_tier=risk_tier,
            privacy_summary=privacy_summary,
            fallback_plan=fallback_plan,
        )
        if envelope.settings.ROUTING_DEBUG:
            log_route_trace(trace)
        return RoutingDecision(
            execution_class=execution_class,
            effort=effort,
            selected_model=selected_model,
            selected_model_reason=selected_model_reason,
            required_capabilities=required_capabilities,
            risk_tier=risk_tier,
            tool_policy=tool_policy,
            require_tools=Capability.NATIVE_TOOLS in required_capabilities,
            fallback_plan=fallback_plan,
            trace=trace,
        )

    def _execution_class(self, envelope: RequestEnvelope) -> ExecutionClass:
        text = envelope.text
        if envelope.source.value == "realtime_voice":
            return ExecutionClass.REALTIME_VOICE_TURN
        if needs_screen_context(text):
            return ExecutionClass.SCREEN_ANALYSIS
        if extract_pdf_path(text):
            return ExecutionClass.PDF_ANALYSIS
        if re.search(r"\b(codex|openclaw|agent|coding job)\b", text, re.I):
            return ExecutionClass.CODING_AGENT_TASK
        if requires_network(text):
            return ExecutionClass.BROWSER_TASK
        if envelope.is_worker or should_background_task(text):
            return ExecutionClass.BACKGROUND_TASK
        if requires_filesystem(text):
            return ExecutionClass.FILESYSTEM_ACTION if requires_model_driven_tools(text) else ExecutionClass.DIRECT_ACTION
        return ExecutionClass.TEXT_CHAT

    @staticmethod
    def _required_capabilities(envelope: RequestEnvelope, execution_class: ExecutionClass) -> frozenset[Capability]:
        caps = {Capability.TEXT}
        if envelope.interaction_style.value == "voice":
            caps.add(Capability.VOICE_RESPONSE)
        if execution_class == ExecutionClass.SCREEN_ANALYSIS:
            caps.update({Capability.VISION, Capability.SCREEN_CAPTURE})
        elif execution_class == ExecutionClass.PDF_ANALYSIS:
            caps.update({Capability.PDF_READ, Capability.FILE_READ})
        elif execution_class == ExecutionClass.BROWSER_TASK:
            caps.update({Capability.NATIVE_TOOLS, Capability.NETWORK, Capability.BROWSER_CONTROL})
        elif execution_class == ExecutionClass.CODING_AGENT_TASK:
            caps.update({Capability.AGENT_DELEGATION, Capability.FILE_READ, Capability.FILE_WRITE})
        elif execution_class in {
            ExecutionClass.TOOL_ASSISTED_CHAT,
            ExecutionClass.BACKGROUND_TASK,
            ExecutionClass.FILESYSTEM_ACTION,
        }:
            caps.add(Capability.NATIVE_TOOLS)
            if requires_filesystem(envelope.text):
                caps.add(Capability.FILE_READ)
                if re.search(r"\b(write|edit|create|move|copy|rename|delete|trash|remove|organize)\b", envelope.text, re.I):
                    caps.add(Capability.FILE_WRITE)
            if requires_network(envelope.text):
                caps.add(Capability.NETWORK)
        return frozenset(caps)

    @staticmethod
    def _risk_tier(text: str, execution_class: ExecutionClass) -> RiskTier:
        lowered = text.lower()
        if execution_class == ExecutionClass.CODING_AGENT_TASK:
            return RiskTier.DELEGATED_AGENT
        if re.search(r"\b(delete permanently|permanent delete|erase)\b", lowered):
            return RiskTier.DESTRUCTIVE
        if execution_class == ExecutionClass.BROWSER_TASK:
            return RiskTier.NETWORKED
        if re.search(r"\b(run command|shell|terminal command)\b", lowered):
            return RiskTier.SHELL_EXECUTION
        if re.search(r"\b(click|type|hotkey|window|launchagent|quit app|open app)\b", lowered):
            return RiskTier.OS_CONTROL
        if re.search(r"\b(write|edit|create|move|copy|rename|trash|remove|organize)\b", lowered):
            return RiskTier.LOCAL_WRITE
        if execution_class in {ExecutionClass.SCREEN_ANALYSIS, ExecutionClass.PDF_ANALYSIS, ExecutionClass.FILESYSTEM_ACTION}:
            return RiskTier.PRIVATE_READ
        return RiskTier.LOW_READ_ONLY if execution_class != ExecutionClass.TEXT_CHAT else RiskTier.NONE

    @staticmethod
    def _privacy_summary(envelope: RequestEnvelope, execution_class: ExecutionClass) -> str:
        data_classes: list[str] = ["prompt"]
        if execution_class == ExecutionClass.SCREEN_ANALYSIS:
            data_classes.append("screenshot")
        if execution_class == ExecutionClass.PDF_ANALYSIS:
            data_classes.append("pdf_text")
        if execution_class in {ExecutionClass.FILESYSTEM_ACTION, ExecutionClass.BACKGROUND_TASK}:
            data_classes.append("local_tool_results")
        model_boundary = evaluate_privacy_boundary(
            envelope.settings,
            action="model.route",
            data_classes=data_classes,
        )
        parts = [model_boundary.explanation]
        if execution_class == ExecutionClass.BROWSER_TASK:
            network_boundary = evaluate_privacy_boundary(
                envelope.settings,
                action="network.route",
                data_classes=["requested_url_or_query"],
            )
            parts.append(network_boundary.explanation)
        if execution_class == ExecutionClass.REALTIME_VOICE_TURN:
            realtime_boundary = evaluate_privacy_boundary(
                envelope.settings,
                action="realtime.voice_turn",
                data_classes=["microphone_audio", "transcript"],
            )
            parts.append(realtime_boundary.explanation)
        return " ".join(parts)
