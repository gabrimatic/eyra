"""Clipboard tool — reads the current clipboard content on macOS."""

import asyncio
import subprocess

from tools.base import BaseTool, ToolResult


class ClipboardTool(BaseTool):
    name = "read_clipboard"
    description = (
        "Reads the current clipboard (pasteboard) content from macOS. "
        "Call this when the user mentions their clipboard, something they copied, "
        "or asks you to look at what they just copied. "
        "Takes no parameters. Returns the clipboard text (up to 2KB)."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> ToolResult:
        def _read() -> str:
            try:
                result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
                text = result.stdout.strip()
                if not text:
                    return "Clipboard is empty."
                # Truncate very long clipboard content
                if len(text) > 2000:
                    return text[:2000] + f"\n... (truncated, {len(text)} chars total)"
                return text
            except Exception as e:
                return f"Could not read clipboard: {e}"

        content = await asyncio.to_thread(_read)
        return ToolResult(content=f"Clipboard content:\n{content}")
