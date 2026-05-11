"""Tests for the filesystem tools."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.filesystem import (
    CreateDirectoryTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from utils.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


class TestToolSchemas:
    def test_read_file_schema(self):
        tool = ReadFileTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "read_file"
        assert "path" in schema["function"]["parameters"]["properties"]
        assert ReadFileTool.costly is False

    def test_write_file_schema(self):
        tool = WriteFileTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "write_file"
        assert "path" in schema["function"]["parameters"]["properties"]
        assert "content" in schema["function"]["parameters"]["properties"]

    def test_edit_file_schema(self):
        tool = EditFileTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "edit_file"
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props
        assert "find" in props
        assert "replace" in props

    def test_list_directory_schema(self):
        tool = ListDirectoryTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "list_directory"

    def test_create_directory_schema(self):
        tool = CreateDirectoryTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "create_directory"


class TestSecurity:
    def test_read_blocks_outside_home(self):
        async def run():
            r = await ReadFileTool().execute(path="/etc/passwd")
            assert "Access denied" in r.content
        _run(run())

    def test_write_blocks_system_paths(self):
        async def run():
            r = await WriteFileTool().execute(path="/usr/local/evil.txt", content="bad")
            assert "Access denied" in r.content
        _run(run())

    def test_list_blocks_outside_home(self):
        async def run():
            r = await ListDirectoryTool().execute(path="/etc")
            assert "Access denied" in r.content
        _run(run())

    def test_settings_default_sandbox_is_documents_and_tmp(self):
        assert Settings().FILESYSTEM_ALLOWED_PATHS == "~/Documents,/tmp"


class TestReadFileTool:
    def test_read_file(self):
        async def run():
            r = await ReadFileTool(allowed_roots=(PROJECT_ROOT,)).execute(path=str(PROJECT_ROOT / "pyproject.toml"))
            assert "eyra" in r.content
        _run(run())

    def test_read_nonexistent(self):
        async def run():
            r = await ReadFileTool(
                allowed_roots=(Path.home(),),
                default_path=Path.home(),
            ).execute(path="~/nonexistent_file_xyz.txt")
            assert "Not a file" in r.content
        _run(run())

    def test_read_binary_file_returns_clean_error(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = Path(d) / "data.bin"
                path.write_bytes(b"\x00\x01\x02not text")
                r = await ReadFileTool(allowed_roots=(Path(d),)).execute(path=str(path))
                assert "binary" in r.content.lower()
                assert "\x00" not in r.content
        _run(run())


class TestWriteFileTool:
    def test_write_and_read(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = os.path.join(d, "test.txt")
                root = Path(d)
                r = await WriteFileTool(allowed_roots=(root,)).execute(path=path, content="test content")
                assert "Created" in r.content
                r = await ReadFileTool(allowed_roots=(root,)).execute(path=path)
                assert "test content" in r.content
        _run(run())

    def test_write_overwrites(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = os.path.join(d, "test.txt")
                root = Path(d)
                writer = WriteFileTool(allowed_roots=(root,))
                reader = ReadFileTool(allowed_roots=(root,))
                await writer.execute(path=path, content="first")
                r = await writer.execute(path=path, content="second")
                assert "already exists" in r.content
                r = await reader.execute(path=path)
                assert "first" in r.content
                assert "second" not in r.content
                await writer.execute(path=path, content="second", overwrite=True)
                r = await reader.execute(path=path)
                assert "second" in r.content
                assert "first" not in r.content
        _run(run())

    def test_write_refuses_existing_directory(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                r = await WriteFileTool(allowed_roots=(Path(d),)).execute(path=d, content="not a directory")
                assert "not a file" in r.content
        _run(run())

    def test_relative_paths_use_default_path(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                default_path = Path(d)
                tool = WriteFileTool(
                    allowed_roots=(default_path,),
                    default_path=default_path,
                )
                r = await tool.execute(path="notes/test.txt", content="relative")
                assert "Created" in r.content
                assert (default_path / "notes" / "test.txt").read_text() == "relative"
        _run(run())

    def test_relative_default_still_enforces_allowed_roots(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as allowed:
                with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as outside:
                    tool = WriteFileTool(
                        allowed_roots=(Path(allowed),),
                        default_path=Path(outside),
                    )
                    r = await tool.execute(path="blocked.txt", content="nope")
                    assert "Access denied" in r.content
        _run(run())


class TestEditFileTool:
    def test_find_replace(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = os.path.join(d, "test.txt")
                root = Path(d)
                await WriteFileTool(allowed_roots=(root,)).execute(path=path, content="hello world hello")
                r = await EditFileTool(allowed_roots=(root,)).execute(path=path, find="hello", replace="bye")
                assert "2 occurrences" in r.content
                r = await ReadFileTool(allowed_roots=(root,)).execute(path=path)
                assert "bye world bye" in r.content
        _run(run())

    def test_not_found(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = os.path.join(d, "test.txt")
                root = Path(d)
                await WriteFileTool(allowed_roots=(root,)).execute(path=path, content="hello")
                r = await EditFileTool(allowed_roots=(root,)).execute(path=path, find="MISSING", replace="x")
                assert "not found" in r.content.lower()
        _run(run())


class TestListDirectoryTool:
    def test_list_directory(self):
        async def run():
            r = await ListDirectoryTool(allowed_roots=(PROJECT_ROOT,)).execute(path=str(PROJECT_ROOT / "src" / "tools"))
            assert "browser.py" in r.content
            assert "filesystem.py" in r.content
        _run(run())

    def test_list_not_a_directory(self):
        async def run():
            r = await ListDirectoryTool(allowed_roots=(PROJECT_ROOT,)).execute(path=str(PROJECT_ROOT / "pyproject.toml"))
            assert "Not a directory" in r.content
        _run(run())


class TestCreateDirectoryTool:
    def test_mkdir(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = os.path.join(d, "a", "b", "c")
                r = await CreateDirectoryTool(allowed_roots=(Path(d),)).execute(path=path)
                assert "Created" in r.content
                assert os.path.isdir(path)
        _run(run())

    def test_mkdir_already_exists(self):
        async def run():
            r = await CreateDirectoryTool(allowed_roots=(PROJECT_ROOT,)).execute(path=str(PROJECT_ROOT))
            assert "Already exists" in r.content
        _run(run())
