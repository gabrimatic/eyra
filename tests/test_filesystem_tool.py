"""Tests for the filesystem tools."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.approval import ApprovalManager
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

    def test_move_path_schema(self):
        tool = MovePathTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "move_path"

    def test_copy_path_schema(self):
        tool = CopyPathTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "copy_path"

    def test_trash_restore_schema(self):
        assert MoveToTrashTool().to_openai_tool()["function"]["name"] == "move_to_trash"
        assert RestoreFromTrashTool().to_openai_tool()["function"]["name"] == "restore_from_trash"
        assert DeletePermanentlyTool().to_openai_tool()["function"]["name"] == "delete_permanently"

    def test_append_prepend_compare_schemas(self):
        assert AppendFileTool().to_openai_tool()["function"]["name"] == "append_file"
        assert PrependFileTool().to_openai_tool()["function"]["name"] == "prepend_file"
        assert CompareFilesTool().to_openai_tool()["function"]["name"] == "compare_files"

    def test_rename_compress_uncompress_schemas(self):
        assert RenamePathTool().to_openai_tool()["function"]["name"] == "rename_path"
        assert DuplicatePathTool().to_openai_tool()["function"]["name"] == "duplicate_path"
        assert CompressPathTool().to_openai_tool()["function"]["name"] == "compress_path"
        assert UncompressArchiveTool().to_openai_tool()["function"]["name"] == "uncompress_archive"


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
        assert Settings().FILESYSTEM_ALLOWED_PATHS == "~/Documents,~/Desktop,~/Downloads,/tmp"


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
                manager = ApprovalManager()
                writer = WriteFileTool(allowed_roots=(root,), approval_manager=manager)
                reader = ReadFileTool(allowed_roots=(root,))
                await writer.execute(path=path, content="first")
                r = await writer.execute(path=path, content="second")
                assert "already exists" in r.content
                r = await reader.execute(path=path)
                assert "first" in r.content
                assert "second" not in r.content
                r = await writer.execute(path=path, content="second", overwrite=True, confirmed=True)
                assert "Approval required" in r.content
                assert manager.list_pending()
                approval_id = manager.list_pending()[0].id
                assert manager.approve(approval_id) is True
                await writer.execute(path=path, content="second", overwrite=True, approval_id=approval_id)
                r = await reader.execute(path=path)
                assert "second" in r.content
                assert "first" not in r.content
        _run(run())

    def test_trusted_controller_token_can_overwrite_after_human_confirmation(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                path = os.path.join(d, "test.txt")
                root = Path(d)
                writer = WriteFileTool(allowed_roots=(root,), trusted_overwrite_token="runtime-secret")
                await writer.execute(path=path, content="first")

                result = await writer.execute(
                    path=path,
                    content="second",
                    overwrite=True,
                    trusted_overwrite_token="runtime-secret",
                )

                assert "Updated" in result.content
                assert Path(path).read_text() == "second"

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


class TestStructuredFileEditTools:
    def test_append_and_prepend_existing_text_file(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                path = root / "notes.txt"
                path.write_text("middle")

                appended = await AppendFileTool(allowed_roots=(root,)).execute(path=str(path), content="\nend")
                prepended = await PrependFileTool(allowed_roots=(root,)).execute(path=str(path), content="start\n")

                assert "Appended" in appended.content
                assert "Prepended" in prepended.content
                assert path.read_text() == "start\nmiddle\nend"

        _run(run())

    def test_append_refuses_missing_file(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                result = await AppendFileTool(allowed_roots=(Path(d),)).execute(
                    path=str(Path(d) / "missing.txt"),
                    content="nope",
                )

                assert "Not a file" in result.content

        _run(run())

    def test_compare_files_returns_unified_diff(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                left = root / "left.txt"
                right = root / "right.txt"
                left.write_text("same\nold\n")
                right.write_text("same\nnew\n")

                result = await CompareFilesTool(allowed_roots=(root,)).execute(
                    left_path=str(left),
                    right_path=str(right),
                )

                assert "--- left.txt" in result.content
                assert "+++ right.txt" in result.content
                assert "-old" in result.content
                assert "+new" in result.content

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


class TestTrashTools:
    def test_move_to_trash_and_restore_file(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                path = root / "trash-me.txt"
                restored = root / "restored.txt"
                path.write_text("recoverable")

                trash_result = await MoveToTrashTool(allowed_roots=(root,)).execute(path=str(path))
                assert "Moved to Trash" in trash_result.content
                trash_path = Path(trash_result.content.split(" -> ", 1)[1])

                assert not path.exists()
                assert trash_path.exists()

                restore_result = await RestoreFromTrashTool(allowed_roots=(root,)).execute(
                    trash_path=str(trash_path),
                    destination=str(restored),
                )

                assert "Restored" in restore_result.content
                assert restored.read_text() == "recoverable"
                assert not trash_path.exists()

        _run(run())

    def test_move_to_trash_refuses_outside_sandbox(self):
        async def run():
            result = await MoveToTrashTool(allowed_roots=(Path.home() / "Documents",)).execute(path="/etc/passwd")

            assert "Access denied" in result.content

        _run(run())

    def test_delete_permanently_requires_exact_approval(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                target = root / "delete-me.txt"
                target.write_text("danger")
                manager = ApprovalManager()
                tool = DeletePermanentlyTool(allowed_roots=(root,), approval_manager=manager)

                result = await tool.execute(path=str(target))

                assert "Approval required" in result.content
                assert target.exists()
                approval_id = manager.list_pending()[0].id
                assert manager.approve(approval_id) is True

                result = await tool.execute(path=str(target), approval_id=approval_id)

                assert "Permanently deleted:" in result.content
                assert not target.exists()

        _run(run())

    def test_delete_permanently_refuses_sandbox_root(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                manager = ApprovalManager()
                tool = DeletePermanentlyTool(allowed_roots=(root,), approval_manager=manager)

                result = await tool.execute(path=str(root))

                assert "Refusing to permanently delete a sandbox root" in result.content
                assert manager.list_pending() == []
                assert root.exists()

        _run(run())

    def test_mkdir_already_exists(self):
        async def run():
            r = await CreateDirectoryTool(allowed_roots=(PROJECT_ROOT,)).execute(path=str(PROJECT_ROOT))
            assert "Already exists" in r.content
        _run(run())


class TestMoveAndCopyPathTools:
    def test_move_file_checks_sandbox_and_moves(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "report.pdf"
                dest = root / "Downloads" / "report.pdf"
                src.write_text("pdf-ish")

                r = await MovePathTool(allowed_roots=(root,)).execute(source=str(src), destination=str(dest))

                assert "Moved" in r.content
                assert not src.exists()
                assert dest.read_text() == "pdf-ish"

        _run(run())

    def test_rename_path_keeps_item_in_same_directory(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "old.txt"
                src.write_text("rename me")

                result = await RenamePathTool(allowed_roots=(root,)).execute(path=str(src), new_name="new.txt")

                assert "Renamed" in result.content
                assert not src.exists()
                assert (root / "new.txt").read_text() == "rename me"

        _run(run())

    def test_rename_rejects_path_separator_in_new_name(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "old.txt"
                src.write_text("rename me")

                result = await RenamePathTool(allowed_roots=(root,)).execute(path=str(src), new_name="../escape.txt")

                assert "New name must not contain path separators" in result.content
                assert src.exists()

        _run(run())

    def test_duplicate_path_uses_copy_name_when_destination_is_omitted(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "note.txt"
                src.write_text("duplicate me")

                result = await DuplicatePathTool(allowed_roots=(root,)).execute(path=str(src))

                duplicate = root / "note copy.txt"
                assert "Duplicated" in result.content
                assert src.read_text() == "duplicate me"
                assert duplicate.read_text() == "duplicate me"

        _run(run())

    def test_compress_and_uncompress_directory_inside_sandbox(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                folder = root / "folder"
                folder.mkdir()
                (folder / "note.txt").write_text("archive me")
                archive = root / "folder.zip"
                destination = root / "expanded"

                compressed = await CompressPathTool(allowed_roots=(root,)).execute(
                    source=str(folder),
                    destination=str(archive),
                )
                uncompressed = await UncompressArchiveTool(allowed_roots=(root,)).execute(
                    archive=str(archive),
                    destination=str(destination),
                )

                assert "Compressed" in compressed.content
                assert archive.exists()
                assert "Uncompressed" in uncompressed.content
                assert (destination / "folder" / "note.txt").read_text() == "archive me"

        _run(run())

    def test_uncompress_rejects_destination_conflict(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                folder = root / "folder"
                folder.mkdir()
                (folder / "note.txt").write_text("archive me")
                archive = root / "folder.zip"
                destination = root / "expanded"
                destination.mkdir()
                await CompressPathTool(allowed_roots=(root,)).execute(source=str(folder), destination=str(archive))

                result = await UncompressArchiveTool(allowed_roots=(root,)).execute(
                    archive=str(archive),
                    destination=str(destination),
                )

                assert "Destination already exists" in result.content

        _run(run())

    def test_move_refuses_destination_conflict_without_overwrite(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "a.txt"
                dest = root / "b.txt"
                src.write_text("source")
                dest.write_text("dest")

                r = await MovePathTool(allowed_roots=(root,)).execute(source=str(src), destination=str(dest))

                assert "already exists" in r.content
                assert src.read_text() == "source"
                assert dest.read_text() == "dest"

        _run(run())

    def test_move_uses_copy_unlink_instead_of_atomic_rename(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "a.txt"
                dest = root / "b.txt"
                src.write_text("source")

                with patch("os.rename", side_effect=AssertionError("rename should not be used")):
                    r = await MovePathTool(allowed_roots=(root,)).execute(source=str(src), destination=str(dest))

                assert "Moved:" in r.content
                assert not src.exists()
                assert dest.read_text() == "source"

        _run(run())

    def test_move_overwrite_requires_approval_even_if_model_sets_confirmed(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                manager = ApprovalManager()
                src = root / "a.txt"
                dest = root / "b.txt"
                src.write_text("source")
                dest.write_text("dest")

                r = await MovePathTool(allowed_roots=(root,), approval_manager=manager).execute(
                    source=str(src),
                    destination=str(dest),
                    overwrite=True,
                    confirmed=True,
                )

                assert "Approval required" in r.content
                assert src.exists()
                assert dest.read_text() == "dest"

        _run(run())

    def test_copy_file_checks_sandbox_and_copies(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                src = root / "notes.txt"
                dest = root / "copy.txt"
                src.write_text("hello")

                r = await CopyPathTool(allowed_roots=(root,)).execute(source=str(src), destination=str(dest))

                assert "Copied" in r.content
                assert src.read_text() == "hello"
                assert dest.read_text() == "hello"

        _run(run())

    def test_copy_refuses_outside_destination(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as allowed:
                with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as outside:
                    src = Path(allowed) / "notes.txt"
                    src.write_text("hello")
                    dest = Path(outside) / "copy.txt"

                    r = await CopyPathTool(allowed_roots=(Path(allowed),)).execute(
                        source=str(src),
                        destination=str(dest),
                    )

                    assert "Access denied" in r.content
                    assert not dest.exists()

        _run(run())

    def test_copy_overwrite_requires_approval_even_if_model_sets_confirmed(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                manager = ApprovalManager()
                src = root / "a.txt"
                dest = root / "b.txt"
                src.write_text("source")
                dest.write_text("dest")

                r = await CopyPathTool(allowed_roots=(root,), approval_manager=manager).execute(
                    source=str(src),
                    destination=str(dest),
                    overwrite=True,
                    confirmed=True,
                )

                assert "Approval required" in r.content
                assert src.read_text() == "source"
                assert dest.read_text() == "dest"

        _run(run())

    def test_open_and_reveal_use_macos_open_command(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                path = root / "notes.txt"
                path.write_text("hello")

                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                    proc = AsyncMock()
                    proc.returncode = 0
                    proc.communicate.return_value = (b"", b"")
                    mock_exec.return_value = proc

                    opened = await OpenPathTool(allowed_roots=(root,)).execute(path=str(path))
                    revealed = await RevealPathTool(allowed_roots=(root,)).execute(path=str(path))

                assert "Opened" in opened.content
                assert "Revealed" in revealed.content
                assert mock_exec.call_args_list[0][0][:2] == ("open", str(path))
                assert mock_exec.call_args_list[1][0][:3] == ("open", "-R", str(path))

        _run(run())
