"""Tests for safe macOS context tools."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.macos_context import FinderSelectionTool, FrontmostAppTool


def _run(coro):
    return asyncio.run(coro)


class TestMacosContextTools:
    def test_frontmost_app_reports_name(self):
        async def run():
            with patch("tools.macos_context._osascript", return_value=(0, "Terminal\n", "")):
                result = await FrontmostAppTool().execute()
            assert "Terminal" in result.content

        _run(run())

    def test_finder_selection_filters_outside_sandbox(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                allowed = root / "notes.txt"
                allowed.write_text("hello")
                stdout = f"{allowed}\n/etc/passwd\n"
                with patch("tools.macos_context._osascript", return_value=(0, stdout, "")):
                    result = await FinderSelectionTool(allowed_roots=(root,)).execute()

                assert str(allowed) in result.content
                assert "outside the filesystem sandbox" in result.content
                assert "/etc/passwd" in result.content

        _run(run())

    def test_finder_selection_handles_none(self):
        async def run():
            with patch("tools.macos_context._osascript", return_value=(0, "", "")):
                result = await FinderSelectionTool().execute()
            assert "No Finder selection" in result.content

        _run(run())
