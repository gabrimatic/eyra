"""Shared tool-registry construction for terminal and web sessions."""

from pathlib import Path

from runtime.external_agents import AgentAdapterRegistry
from tools.approval import ApprovalManager
from tools.browser import (
    BrowserSession,
    ClickElementTool,
    DownloadFileTool,
    FillFormFieldTool,
    OpenUrlTool,
    PageScreenshotTool,
    UploadFileTool,
    WebSearchTool,
)
from tools.clipboard import ClipboardTool
from tools.filesystem import (
    AppendFileTool,
    CompareFilesTool,
    CompressPathTool,
    CopyPathTool,
    CreateDirectoryTool,
    DeletePermanentlyTool,
    DuplicatePathTool,
    EditFileTool,
    ListDirectoryTool,
    MovePathTool,
    MoveToTrashTool,
    OpenPathTool,
    PrependFileTool,
    ReadFileTool,
    RenamePathTool,
    RestoreFromTrashTool,
    RevealPathTool,
    UncompressArchiveTool,
    WriteFileTool,
    parse_allowed_roots,
)
from tools.macos_context import FinderSelectionTool, FrontmostAppTool
from tools.mcp_stdio import CallMcpTool, ListMcpTools
from tools.operator import (
    ActivateAppTool,
    DiscoverCapabilitiesTool,
    ExtractScreenTextTool,
    FetchUrlTool,
    FileInfoTool,
    GetAccessibilityTreeTool,
    GetAgentSessionContentTool,
    GetAgentStatusTool,
    GetCodexSessionContentTool,
    GetLaunchAgentStatusTool,
    GetOpenClawStatusTool,
    GetSystemSnapshotTool,
    GetVoiceContextTool,
    ListAgentSessionsTool,
    ListCodexSessionsTool,
    ListOpenAppsTool,
    ListOpenClawSessionsTool,
    ListProcessesTool,
    ListWindowsTool,
    ManageLaunchAgentTool,
    OpenAppTool,
    PressHotkeyTool,
    QuitAppTool,
    RunAgentTaskTool,
    RunCodexTaskTool,
    RunCommandTool,
    RunOpenClawAgentTool,
    RunShortcutTool,
    SearchFilesTool,
    SetClipboardTool,
    ShowNotificationTool,
    UiClickTool,
    UiDragTool,
    UiScrollTool,
    UiTypeTextTool,
    WindowActionTool,
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
    trusted_overwrite_token: str = "",
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
    registry.register(AppendFileTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(PrependFileTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(CompareFilesTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(RenamePathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(DuplicatePathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(CompressPathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(UncompressArchiveTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(
        WriteFileTool(
            allowed_roots=fs_roots,
            default_path=fs_default,
            approval_manager=approval_manager,
            trusted_overwrite_token=trusted_overwrite_token,
        )
    )
    registry.register(EditFileTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(ListDirectoryTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(CreateDirectoryTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(
        MovePathTool(
            allowed_roots=fs_roots,
            default_path=fs_default,
            approval_manager=approval_manager,
            trusted_overwrite_token=trusted_overwrite_token,
        )
    )
    registry.register(
        CopyPathTool(
            allowed_roots=fs_roots,
            default_path=fs_default,
            approval_manager=approval_manager,
            trusted_overwrite_token=trusted_overwrite_token,
        )
    )
    registry.register(OpenPathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(RevealPathTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(MoveToTrashTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(RestoreFromTrashTool(allowed_roots=fs_roots, default_path=fs_default))
    registry.register(
        DeletePermanentlyTool(allowed_roots=fs_roots, default_path=fs_default, approval_manager=approval_manager)
    )
    registry.register(ReadPdfTool(allowed_roots=fs_roots, default_path=fs_default))

    if settings.NETWORK_TOOLS_ENABLED:
        session = browser_session or BrowserSession()
        registry.register(WeatherTool())
        registry.register(FetchUrlTool())
        registry.register(WebSearchTool(session=session))
        registry.register(OpenUrlTool(session=session))
        registry.register(ClickElementTool(session=session))
        registry.register(
            DownloadFileTool(
                session=session,
                allowed_roots=fs_roots,
                default_path=fs_default,
                approval_manager=approval_manager,
            )
        )
        registry.register(FillFormFieldTool(session=session))
        registry.register(
            UploadFileTool(
                session=session,
                allowed_roots=fs_roots,
                default_path=fs_default,
                approval_manager=approval_manager,
            )
        )
        registry.register(PageScreenshotTool(session=session))

    if settings.OS_TOOLS_ENABLED:
        registry.register(RunCommandTool(allowed_roots=fs_roots, default_path=fs_default, approval_manager=approval_manager))
        registry.register(FileInfoTool(allowed_roots=fs_roots, default_path=fs_default))
        registry.register(SearchFilesTool(allowed_roots=fs_roots, default_path=fs_default))
        registry.register(ListProcessesTool())
        registry.register(GetSystemSnapshotTool())
        registry.register(GetAccessibilityTreeTool())
        registry.register(ExtractScreenTextTool(ocr_command=settings.SCREEN_OCR_COMMAND))
        registry.register(ListOpenAppsTool())
        registry.register(ListWindowsTool())
        registry.register(WindowActionTool(approval_manager=approval_manager))
        registry.register(GetLaunchAgentStatusTool())
        registry.register(ManageLaunchAgentTool(approval_manager=approval_manager))
        registry.register(ActivateAppTool())
        registry.register(OpenAppTool(approval_manager=approval_manager))
        registry.register(QuitAppTool(approval_manager=approval_manager))
        registry.register(UiClickTool(approval_manager=approval_manager))
        registry.register(UiScrollTool(approval_manager=approval_manager))
        registry.register(UiDragTool(approval_manager=approval_manager))
        registry.register(UiTypeTextTool(approval_manager=approval_manager))
        registry.register(PressHotkeyTool(approval_manager=approval_manager))
        registry.register(ShowNotificationTool())
        registry.register(RunShortcutTool(approval_manager=approval_manager))
        registry.register(SetClipboardTool(approval_manager=approval_manager))

    external_agents_enabled = bool(
        getattr(settings, "AGENT_TOOLS_ENABLED", False) or getattr(settings, "EXTERNAL_AGENT_TOOLS_ENABLED", False)
    )
    if external_agents_enabled:
        agent_registry = AgentAdapterRegistry.from_settings(settings, allowed_roots=fs_roots, default_path=fs_default)
        registry.register(GetAgentStatusTool())
        registry.register(ListAgentSessionsTool())
        registry.register(GetAgentSessionContentTool())
        registry.register(ListCodexSessionsTool())
        registry.register(ListOpenClawSessionsTool())
        registry.register(GetCodexSessionContentTool())
        registry.register(GetOpenClawStatusTool())
        registry.register(
            RunAgentTaskTool(
                allowed_roots=fs_roots,
                default_path=fs_default,
                approval_manager=approval_manager,
                agent_registry=agent_registry,
            )
        )
        registry.register(
            RunCodexTaskTool(
                allowed_roots=fs_roots,
                default_path=fs_default,
                approval_manager=approval_manager,
                agent_registry=agent_registry,
            )
        )
        registry.register(
            RunOpenClawAgentTool(
                allowed_roots=fs_roots,
                default_path=fs_default,
                approval_manager=approval_manager,
                agent_registry=agent_registry,
            )
        )

    if settings.MCP_TOOLS_ENABLED:
        registry.register(ListMcpTools(config_path=settings.MCP_CONFIG_PATH))
        registry.register(CallMcpTool(config_path=settings.MCP_CONFIG_PATH, approval_manager=approval_manager))

    return registry
