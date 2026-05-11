"""Safe macOS context tools."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.filesystem import _DEFAULT_ALLOWED_ROOTS, _resolve

logger = logging.getLogger(__name__)


async def _osascript(*lines: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "osascript",
        *[arg for line in lines for arg in ("-e", line)],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class FrontmostAppTool(BaseTool):
    name = "get_frontmost_app"
    description = "Get the name of the frontmost macOS app. Local-only."
    parameters = {"type": "object", "properties": {}, "required": []}
    costly = False

    async def execute(self, **_) -> ToolResult:
        try:
            code, stdout, stderr = await _osascript(
                'tell application "System Events" to get name of first application process whose frontmost is true'
            )
            if code != 0:
                detail = stderr.strip() or "macOS did not return the frontmost app."
                return ToolResult(content=f"Could not read frontmost app: {detail}")
            app = stdout.strip()
            return ToolResult(content=f"Frontmost app: {app or 'unknown'}")
        except Exception as e:
            logger.error("get_frontmost_app failed: %s", e, exc_info=True)
            return ToolResult(content=f"macOS context error: {e}")


class FinderSelectionTool(BaseTool):
    name = "get_finder_selection"
    description = (
        "Get selected Finder files or folders that are inside the filesystem sandbox. "
        "Use this to resolve references like 'that file' or 'the selected PDF'."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, **_) -> ToolResult:
        try:
            code, stdout, stderr = await _osascript(
                'tell application "Finder" to set selectedItems to selection',
                'set output to ""',
                'repeat with itemRef in selectedItems',
                'set output to output & POSIX path of (itemRef as alias) & linefeed',
                'end repeat',
                'return output',
            )
            if code != 0:
                detail = stderr.strip() or "Finder did not return a selection."
                return ToolResult(content=f"Could not read Finder selection: {detail}")
            paths = [line.strip() for line in stdout.splitlines() if line.strip()]
            if not paths:
                return ToolResult(content="No Finder selection.")
            allowed = []
            blocked = []
            for path in paths:
                try:
                    allowed.append(str(_resolve(path, self._roots, self._default_path)))
                except PermissionError:
                    blocked.append(path)
            lines = []
            if allowed:
                lines.append("Finder selection inside sandbox:\n" + "\n".join(allowed))
            if blocked:
                lines.append(
                    "Finder selected path outside the filesystem sandbox:\n"
                    + "\n".join(blocked)
                )
            return ToolResult(content="\n\n".join(lines))
        except Exception as e:
            logger.error("get_finder_selection failed: %s", e, exc_info=True)
            return ToolResult(content=f"macOS context error: {e}")
