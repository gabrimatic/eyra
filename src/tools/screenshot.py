"""Screenshot tool — captures the current screen on demand."""

import asyncio
import logging
import subprocess

from chat.capture import capture_screenshot_and_encode
from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


async def _get_active_app() -> str | None:
    """Get the name of the frontmost application via osascript."""
    def _run() -> str | None:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    return await asyncio.to_thread(_run)


async def _get_active_window() -> str | None:
    """Get the title of the frontmost window via osascript."""
    def _run() -> str | None:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of front window of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    return await asyncio.to_thread(_run)


class ScreenshotTool(BaseTool):
    name = "take_screenshot"
    description = (
        "Captures a screenshot of the user's screen and returns it as an image. "
        "Also reports which application and window are currently in the foreground. "
        "Call this when the user asks you to look at their screen, see what they're doing, "
        "or when you need visual context about their current display. "
        "Takes no parameters."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    costly = True

    async def execute(self, **kwargs) -> ToolResult:
        try:
            base64_img = await capture_screenshot_and_encode()
        except Exception as e:
            logger.error("Screenshot capture failed: %s", e, exc_info=True)
            return ToolResult(content="Failed to capture screenshot. Screen recording permission may be required.")
        app = await _get_active_app()
        window = await _get_active_window()
        context_parts = ["Screenshot captured."]
        if app:
            context_parts.append(f"Active app: {app}")
        if window:
            context_parts.append(f"Window: {window}")
        return ToolResult(content=" ".join(context_parts), image_base64=base64_img)
