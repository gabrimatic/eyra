"""Route trace formatting without prompt or tool argument leakage."""

from __future__ import annotations

import logging

from runtime.routing.types import RoutingTrace

logger = logging.getLogger(__name__)


def _value(item) -> str:
    return getattr(item, "value", str(item))


def trace_to_dict(trace: RoutingTrace) -> dict:
    """Return a JSON-safe route trace summary."""
    return {
        "source": trace.source.value,
        "qualityMode": trace.quality_mode.value,
        "executionClass": trace.execution_class.value,
        "effortLevel": _value(trace.effort_level),
        "selectedModel": trace.selected_model,
        "selectedModelReason": trace.selected_model_reason,
        "requiredCapabilities": sorted(cap.value for cap in trace.required_capabilities),
        "allowedTools": list(trace.allowed_tools),
        "deniedTools": dict(trace.denied_tools),
        "riskTier": trace.risk_tier.value,
        "privacySummary": trace.privacy_summary,
        "fallbackPlan": {
            "onModelMissing": trace.fallback_plan.on_model_missing,
            "onToolsUnsupported": trace.fallback_plan.on_tools_unsupported,
            "onCapabilityMissing": trace.fallback_plan.on_capability_missing,
        },
    }


def format_route_trace(trace: RoutingTrace | None) -> str:
    """Format the last route trace for terminal display."""
    if trace is None:
        return "No route has been planned yet."
    denied = trace.denied_tools
    denied_lines = [f"  - {name}: {reason}" for name, reason in sorted(denied.items())[:8]]
    if len(denied) > 8:
        denied_lines.append(f"  - ... {len(denied) - 8} more")
    return "\n".join([
        "Last route",
        f"source: {trace.source.value}",
        f"quality mode: {trace.quality_mode.value}",
        f"execution class: {trace.execution_class.value}",
        f"effort level: {_value(trace.effort_level)}",
        f"selected model: {trace.selected_model or 'none'}",
        f"model reason: {trace.selected_model_reason}",
        "required capabilities: " + ", ".join(sorted(cap.value for cap in trace.required_capabilities)),
        f"risk tier: {trace.risk_tier.value}",
        f"privacy: {trace.privacy_summary}",
        "allowed tools: " + (", ".join(trace.allowed_tools) if trace.allowed_tools else "none"),
        f"denied tools: {len(denied)}",
        *(denied_lines if denied_lines else []),
        f"fallback model: {trace.fallback_plan.on_model_missing}",
        f"fallback tools: {trace.fallback_plan.on_tools_unsupported}",
        f"fallback capability: {trace.fallback_plan.on_capability_missing}",
    ])


def log_route_trace(trace: RoutingTrace) -> None:
    """Log a route summary without sensitive prompt contents or arguments."""
    logger.info(
        "Route source=%s class=%s effort=%s model=%s risk=%s allowed_tools=%d denied_tools=%d privacy=%s",
        trace.source.value,
        trace.execution_class.value,
        _value(trace.effort_level),
        trace.selected_model,
        trace.risk_tier.value,
        len(trace.allowed_tools),
        len(trace.denied_tools),
        trace.privacy_summary,
    )
