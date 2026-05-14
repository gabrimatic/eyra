"""Tool metadata and allowlist policy for local-first routing."""

from __future__ import annotations

from runtime.routing.types import Capability, ExecutionClass, RiskTier, ToolMetadata, ToolPolicyDecision
from tools.base import BaseTool
from tools.registry import ToolRegistry
from utils.settings import Settings

UTILITY_READ_ONLY = {
    "discover_capabilities",
    "get_voice_context",
    "get_current_time",
    "get_system_info",
    "get_frontmost_app",
    "list_open_apps",
    "list_windows",
}

FILE_PRIVATE_READ = {
    "get_finder_selection",
    "read_file",
    "list_directory",
    "compare_files",
    "read_pdf",
    "get_file_info",
    "search_files",
}

SCREEN_PRIVATE_READ = {
    "take_screenshot",
    "extract_screen_text",
    "get_accessibility_tree",
}

SYSTEM_PRIVATE_READ = {
    "read_clipboard",
    "get_system_snapshot",
    "list_processes",
    "get_launch_agent_status",
}

AGENT_READ = {
    "get_agent_status",
    "list_agent_sessions",
    "get_agent_session_content",
    "list_codex_sessions",
    "get_codex_session_content",
    "list_openclaw_sessions",
    "get_openclaw_status",
}

PRIVATE_READ = FILE_PRIVATE_READ | SCREEN_PRIVATE_READ | SYSTEM_PRIVATE_READ | AGENT_READ

LOCAL_WRITE = {
    "write_file",
    "edit_file",
    "append_file",
    "prepend_file",
    "create_directory",
    "move_path",
    "copy_path",
    "rename_path",
    "duplicate_path",
    "compress_path",
    "uncompress_archive",
    "open_path",
    "reveal_path",
    "restore_from_trash",
    "set_clipboard_text",
}

DESTRUCTIVE = {"move_to_trash", "delete_permanently"}

NETWORK_BROWSER = {
    "get_weather",
    "fetch_url",
    "web_search",
    "open_url",
    "click_element",
    "page_screenshot",
    "download_file",
    "upload_file",
    "fill_form_field",
}

OS_SHELL = {
    "run_command",
    "manage_launch_agent",
    "window_action",
    "activate_app",
    "open_app",
    "quit_app",
    "ui_click",
    "ui_scroll",
    "ui_drag",
    "ui_type_text",
    "press_hotkey",
    "run_shortcut",
    "show_notification",
}

AGENT_MCP = {
    "run_agent_task",
    "run_codex_task",
    "run_openclaw_agent",
    "list_mcp_tools",
    "call_mcp_tool",
}


def metadata_for_tool(tool: BaseTool) -> ToolMetadata:
    """Return structured metadata for a registered tool."""
    if getattr(tool, "tool_metadata", None) is not None:
        return tool.tool_metadata
    name = tool.name
    if name in UTILITY_READ_ONLY:
        return ToolMetadata(
            name=name,
            capabilities=frozenset({Capability.TEXT}),
            risk_tier=RiskTier.LOW_READ_ONLY,
            latency_cost="low",
            allowed_execution_classes=frozenset({
                ExecutionClass.TEXT_CHAT,
                ExecutionClass.TOOL_ASSISTED_CHAT,
                ExecutionClass.BACKGROUND_TASK,
            }),
        )
    if name in PRIVATE_READ:
        capabilities: set[Capability] = set()
        allowed_execution_classes = {
            ExecutionClass.TOOL_ASSISTED_CHAT,
            ExecutionClass.BACKGROUND_TASK,
            ExecutionClass.FILESYSTEM_ACTION,
        }
        if name in FILE_PRIVATE_READ:
            capabilities.add(Capability.FILE_READ)
        if name in SCREEN_PRIVATE_READ:
            capabilities.update({Capability.SCREEN_CAPTURE, Capability.VISION})
        if name == "read_pdf":
            capabilities.update({Capability.PDF_READ})
        if name in SYSTEM_PRIVATE_READ:
            capabilities.add(Capability.OS_AUTOMATION)
        if name in AGENT_READ:
            capabilities.add(Capability.AGENT_DELEGATION)
            allowed_execution_classes = {
                ExecutionClass.BACKGROUND_TASK,
                ExecutionClass.CODING_AGENT_TASK,
            }
        return ToolMetadata(
            name=name,
            capabilities=frozenset(capabilities),
            risk_tier=RiskTier.PRIVATE_READ,
            latency_cost="high" if getattr(tool, "costly", False) else "low",
            reads_private_data=True,
            allowed_execution_classes=frozenset(allowed_execution_classes),
        )
    if name in LOCAL_WRITE:
        return ToolMetadata(
            name=name,
            capabilities=frozenset({Capability.FILE_READ, Capability.FILE_WRITE}),
            risk_tier=RiskTier.LOCAL_WRITE,
            latency_cost="low",
            reads_private_data=True,
            mutates_state=True,
            requires_approval=name in {"write_file", "move_path", "copy_path", "set_clipboard_text"},
            allowed_execution_classes=frozenset({
                ExecutionClass.TOOL_ASSISTED_CHAT,
                ExecutionClass.BACKGROUND_TASK,
                ExecutionClass.FILESYSTEM_ACTION,
            }),
        )
    if name in DESTRUCTIVE:
        return ToolMetadata(
            name=name,
            capabilities=frozenset({Capability.FILE_WRITE}),
            risk_tier=RiskTier.DESTRUCTIVE,
            latency_cost="low",
            mutates_state=True,
            destructive=True,
            requires_approval=name == "delete_permanently",
            allowed_execution_classes=frozenset({
                ExecutionClass.TOOL_ASSISTED_CHAT,
                ExecutionClass.BACKGROUND_TASK,
                ExecutionClass.FILESYSTEM_ACTION,
            }),
        )
    if name in NETWORK_BROWSER:
        return ToolMetadata(
            name=name,
            capabilities=frozenset({Capability.NETWORK, Capability.BROWSER_CONTROL}),
            risk_tier=RiskTier.NETWORKED,
            latency_cost="high",
            network_access=True,
            requires_approval=name in {"download_file", "upload_file"},
            allowed_execution_classes=frozenset({
                ExecutionClass.BROWSER_TASK,
                ExecutionClass.TOOL_ASSISTED_CHAT,
                ExecutionClass.BACKGROUND_TASK,
            }),
        )
    if name in OS_SHELL:
        capabilities = {Capability.OS_AUTOMATION}
        risk = RiskTier.OS_CONTROL
        if name == "run_command":
            capabilities.add(Capability.SHELL)
            risk = RiskTier.SHELL_EXECUTION
        return ToolMetadata(
            name=name,
            capabilities=frozenset(capabilities),
            risk_tier=risk,
            latency_cost="high",
            reads_private_data=name in {"get_accessibility_tree"},
            mutates_state=name not in {"show_notification"},
            requires_approval=name not in {"activate_app", "show_notification"},
            allowed_execution_classes=frozenset({
                ExecutionClass.TOOL_ASSISTED_CHAT,
                ExecutionClass.BACKGROUND_TASK,
            }),
        )
    if name in AGENT_MCP:
        is_mcp_tool = name in {"list_mcp_tools", "call_mcp_tool"}
        capability = Capability.MCP if is_mcp_tool else Capability.AGENT_DELEGATION
        if is_mcp_tool:
            allowed_execution_classes = {
                ExecutionClass.TOOL_ASSISTED_CHAT,
                ExecutionClass.BACKGROUND_TASK,
            }
        else:
            allowed_execution_classes = {
                ExecutionClass.CODING_AGENT_TASK,
                ExecutionClass.BACKGROUND_TASK,
            }
        return ToolMetadata(
            name=name,
            capabilities=frozenset({capability}),
            risk_tier=RiskTier.DELEGATED_AGENT,
            latency_cost="high",
            reads_private_data=True,
            mutates_state=name not in {"list_mcp_tools"},
            requires_approval=name not in {"list_mcp_tools"},
            allowed_execution_classes=frozenset(allowed_execution_classes),
        )
    return ToolMetadata(
        name=name,
        capabilities=frozenset(),
        risk_tier=RiskTier.PRIVATE_READ if getattr(tool, "costly", False) else RiskTier.LOW_READ_ONLY,
        latency_cost="high" if getattr(tool, "costly", False) else "low",
        reads_private_data=getattr(tool, "costly", False),
    )


def route_tool_policy(
    *,
    execution_class: ExecutionClass,
    required_capabilities: frozenset[Capability],
    risk_tier: RiskTier,
    settings: Settings,
    tool_registry: ToolRegistry | None,
) -> ToolPolicyDecision:
    """Return an allowlist for tools that fit the route."""
    if tool_registry is None:
        return ToolPolicyDecision(frozenset(), {})
    metadata = tool_registry.metadata_by_name()
    allowed: set[str] = set()
    denied: dict[str, str] = {}

    for name, meta in metadata.items():
        reason = _deny_reason(
            meta,
            execution_class=execution_class,
            required_capabilities=required_capabilities,
            risk_tier=risk_tier,
            settings=settings,
        )
        if reason:
            denied[name] = reason
        else:
            allowed.add(name)

    return ToolPolicyDecision(frozenset(allowed), denied)


def _deny_reason(
    meta: ToolMetadata,
    *,
    execution_class: ExecutionClass,
    required_capabilities: frozenset[Capability],
    risk_tier: RiskTier,
    settings: Settings,
) -> str | None:
    if execution_class == ExecutionClass.TEXT_CHAT:
        if meta.name in UTILITY_READ_ONLY:
            return None
        return "text chat does not expose private or mutating tools"

    if execution_class == ExecutionClass.SCREEN_ANALYSIS:
        return "screen analysis is controller-owned"

    if execution_class == ExecutionClass.PDF_ANALYSIS:
        return "PDF extraction and summarization are controller-owned"

    if Capability.SCREEN_CAPTURE in meta.capabilities:
        return "screen capture is controller-owned"

    if meta.network_access and not settings.NETWORK_TOOLS_ENABLED:
        return "network tools are disabled"
    if Capability.OS_AUTOMATION in meta.capabilities and not settings.OS_TOOLS_ENABLED:
        return "OS tools are disabled"
    if Capability.SHELL in meta.capabilities and not settings.OS_TOOLS_ENABLED:
        return "shell tools are disabled"
    if Capability.MCP in meta.capabilities and not settings.MCP_TOOLS_ENABLED:
        return "MCP tools are disabled"
    if Capability.AGENT_DELEGATION in meta.capabilities and not settings.AGENT_TOOLS_ENABLED:
        return "agent tools are disabled"
    if meta.destructive and risk_tier != RiskTier.DESTRUCTIVE:
        return "destructive tools require destructive route risk"
    if meta.risk_tier == RiskTier.LOCAL_WRITE and meta.mutates_state:
        if Capability.FILE_WRITE not in required_capabilities or risk_tier not in {
            RiskTier.LOCAL_WRITE,
            RiskTier.DESTRUCTIVE,
        }:
            return "mutating filesystem tools require a file-write route"
    if meta.allowed_execution_classes and execution_class not in meta.allowed_execution_classes:
        return f"not allowed for {execution_class.value}"
    if meta.capabilities and not (meta.capabilities & required_capabilities):
        if meta.name not in UTILITY_READ_ONLY:
            return "tool capabilities are unrelated to this route"
    return None
