"""Shared tool-registry construction for terminal and web sessions."""

from pathlib import Path

from tools.approval import ApprovalManager
from tools.browser import BrowserSession, ClickElementTool, OpenUrlTool, PageScreenshotTool, WebSearchTool
from tools.clipboard import ClipboardTool
from tools.filesystem import (
    CopyPathTool,
    CreateDirectoryTool,
    EditFileTool,
    ListDirectoryTool,
    MovePathTool,
    OpenPathTool,
    ReadFileTool,
    RevealPathTool,
    WriteFileTool,
    parse_allowed_roots,
)
from tools.macos_context import FinderSelectionTool, FrontmostAppTool
from tools.mcp_stdio import CallMcpTool, ListMcpTools
from tools.operator import (
    DiscoverCapabilitiesTool,
    FetchUrlTool,
    FileInfoTool,
    GetAgentSessionContentTool,
    GetAgentStatusTool,
    GetCodexSessionContentTool,
    GetLaunchAgentStatusTool,
    GetOpenClawStatusTool,
    GetSystemSnapshotTool,
    GetVoiceContextTool,
    ListAgentSessionsTool,
    ListCodexSessionsTool,
    ListOpenClawSessionsTool,
    ListProcessesTool,
    ManageLaunchAgentTool,
    OpenAppTool,
    RunAgentTaskTool,
    RunCodexTaskTool,
    RunCommandTool,
    RunOpenClawAgentTool,
    SearchFilesTool,
    SetClipboardTool,
    ShowNotificationTool,
)
from tools.pdf import ReadPdfTool
from tools.registry import ToolRegistry
from tools.screenshot import ScreenshotTool
from tools.system_info import SystemInfoTool
from tools.time_tool import TimeTool
from tools.weather import WeatherTool
from utils.settings import Settings


def build_tool_registry(
    settings: Settings,
    browser_session: BrowserSession | None = None,
    approval_manager: ApprovalManager | None = None,
) -> ToolRegistry:
    """Build Eyra's tool registry with optional bridges gated by settings."""
    registry = ToolRegistry()
    registry.register(DiscoverCapabilitiesTool(settings))
    registry.register(GetVoiceContextTool(settings))
    registry.register(TimeTool())
    registry.register(ClipboardTool())
    registry.register(SystemInfoTool())
    registry.register(ScreenshotTool())
    registry.register(FrontmostAppTool())

    fs_roots = parse_allowed_roots(settings.FILESYSTEM_ALLOWED_PATHS)
    fs_default = Path(settings.FILESYSTEM_DEFAULT_PATH)
    registry.register(FinderSelectionTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(ReadFileTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(WriteFileTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(EditFileTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(ListDirectoryTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(CreateDirectoryTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(MovePathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(CopyPathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(OpenPathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(RevealPathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(ReadPdfTool(allowed_roots=fs_roots, default_path=fs_default))

    if settings.NETWORK_TOOLS_ENABLED:
        session = browser_session or BrowserSession()
        registry.register(WeatherTool())
        registry.register(FetchUrlTool())
        registry.register(WebSearchTool(session=session))
        registry.register(OpenUrlTool(session=session))
        registry.register(ClickElementTool(session=session))
        registry.register(PageScreenshotTool(session=session))

    if settings.OS_TOOLS_ENABLED:
        registry.register(RunCommandTool(allowed_roots=fs_roots, default_path=fs_default, approval_manager=approval_manager))
        registry.register(FileInfoTool(allowed_roots=fs_roots, default_path=fs_default))
        registry.register(SearchFilesTool(allowed_roots=fs_roots, default_path=fs_default))
        registry.register(ListProcessesTool())
        registry.register(GetSystemSnapshotTool())
        registry.register(GetLaunchAgentStatusTool())
        registry.register(ManageLaunchAgentTool(approval_manager=approval_manager))
        registry.register(OpenAppTool(approval_manager=approval_manager))
        registry.register(ShowNotificationTool())
        registry.register(SetClipboardTool(approval_manager=approval_manager))

    if settings.AGENT_TOOLS_ENABLED:
        registry.register(GetAgentStatusTool())
        registry.register(ListAgentSessionsTool())
        registry.register(GetAgentSessionContentTool())
        registry.register(ListCodexSessionsTool())
        registry.register(ListOpenClawSessionsTool())
        registry.register(GetCodexSessionContentTool())
        registry.register(GetOpenClawStatusTool())
        registry.register(RunAgentTaskTool(allowed_roots=fs_roots, default_path=fs_default, approval_manager=approval_manager))
        registry.register(RunCodexTaskTool(allowed_roots=fs_roots, default_path=fs_default, approval_manager=approval_manager))
        registry.register(RunOpenClawAgentTool(allowed_roots=fs_roots, default_path=fs_default, approval_manager=approval_manager))

    if settings.MCP_TOOLS_ENABLED:
        registry.register(ListMcpTools(config_path=settings.MCP_CONFIG_PATH))
        registry.register(CallMcpTool(config_path=settings.MCP_CONFIG_PATH))

    return registry
