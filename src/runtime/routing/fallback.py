"""Fallback messages for local-first routing decisions."""

from __future__ import annotations

from runtime.routing.types import ExecutionClass, FallbackPlan


def build_fallback_plan(execution_class: ExecutionClass) -> FallbackPlan:
    """Create a concise fallback plan for a route class."""
    if execution_class == ExecutionClass.BROWSER_TASK:
        return FallbackPlan(
            on_model_missing="No configured model can handle this browser request.",
            on_tools_unsupported="The selected model cannot use browser tools.",
            on_capability_missing=(
                "Network tools are disabled. Enable NETWORK_TOOLS_ENABLED=true before asking Eyra to browse, "
                "summarize websites, or check weather."
            ),
        )
    if execution_class == ExecutionClass.SCREEN_ANALYSIS:
        return FallbackPlan(
            on_model_missing="Screen analysis needs a configured vision-capable model.",
            on_tools_unsupported="Screen analysis is controller-owned and does not require native model tools.",
            on_capability_missing="Screen capture or vision support is unavailable on this Mac.",
        )
    if execution_class == ExecutionClass.PDF_ANALYSIS:
        return FallbackPlan(
            on_model_missing="No configured model is available to summarize the extracted PDF text.",
            on_tools_unsupported="PDF extraction is controller-owned and does not require native model tools.",
            on_capability_missing="The PDF must be a readable local file inside the filesystem sandbox.",
        )
    if execution_class in {ExecutionClass.BACKGROUND_TASK, ExecutionClass.TOOL_ASSISTED_CHAT, ExecutionClass.FILESYSTEM_ACTION}:
        return FallbackPlan(
            on_model_missing="No configured model can handle this local tool task.",
            on_tools_unsupported=(
                "The selected model cannot use local tools. Text chat still works, but this task needs a "
                "tool-capable model."
            ),
            on_capability_missing="The needed local capability is disabled or unavailable.",
        )
    return FallbackPlan(
        on_model_missing="No configured model is available for this request.",
        on_tools_unsupported="The selected model cannot use local tools.",
        on_capability_missing="A required local capability is unavailable.",
    )
