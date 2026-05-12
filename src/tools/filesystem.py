"""Filesystem tools — read, write, edit, move, copy, and open local paths."""

import asyncio
import difflib
import hashlib
import logging
import shutil
import uuid
import zipfile
from pathlib import Path

from tools.approval import GLOBAL_APPROVAL_MANAGER, ApprovalManager, approval_required_message
from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_MAX_READ_BYTES = 64_000  # ~64 KB text read limit
_MAX_LIST_ENTRIES = 200
_DEFAULT_ALLOWED_ROOTS = (
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path("/tmp").resolve(),
)


def parse_allowed_roots(paths_str: str) -> tuple[Path, ...]:
    """Parse a comma-separated list of paths into resolved Path objects."""
    roots = []
    for p in paths_str.split(","):
        p = p.strip()
        if p:
            roots.append(Path(p).expanduser().resolve())
    return tuple(roots) if roots else _DEFAULT_ALLOWED_ROOTS


def _resolve(path_str: str, allowed_roots: tuple[Path, ...], default_path: Path | None = None) -> Path:
    """Resolve a path, expanding ~, and ensure it's under an allowed root.

    Relative paths are resolved under FILESYSTEM_DEFAULT_PATH so tools do not
    accidentally read or write in whatever directory launched the app.
    """
    if not path_str or not path_str.strip():
        raise ValueError("Missing path.")
    raw = Path(path_str).expanduser()
    if not raw.is_absolute() and default_path is not None:
        raw = default_path / raw
    p = raw.resolve()
    if not any(p == root or root in p.parents for root in allowed_roots):
        raise PermissionError(f"Access denied: path must be under {' or '.join(str(r) for r in allowed_roots)}")
    return p


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"


def _content_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _trusted_overwrite(token: str, expected: str) -> bool:
    return bool(token and expected and token == expected)


def _approval_or_result(
    manager: ApprovalManager,
    *,
    tool_name: str,
    title: str,
    details: dict[str, object],
    approval_id: str = "",
) -> ToolResult | None:
    if approval_id and manager.consume(approval_id, tool_name, title, details):
        return None
    approval = manager.request(tool_name, title, details)
    return ToolResult(content=approval_required_message(approval))


class ReadFileTool(BaseTool):
    name = "read_file"
    description = (
        "Read the text content of a file on the user's computer. "
        "Call this when the user asks to open, view, or inspect a file. "
        'Example: {"path": "~/notes.txt"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or ~ path to the file. Example: '~/notes.txt', '/tmp/data.csv'.",
            },
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("read_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {p}")
        size = p.stat().st_size
        truncated = size > _MAX_READ_BYTES
        with open(p, "rb") as f:
            data = f.read(_MAX_READ_BYTES)
        if b"\x00" in data:
            return ToolResult(content=f"Cannot read {p.name}: file appears to be binary, not text.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(content=f"Cannot read {p.name}: file appears to be binary, not text.")
        header = f"File: {p} ({_human_size(size)})"
        if truncated:
            header += f" (showing first {_MAX_READ_BYTES // 1000}KB)"
        return ToolResult(content=f"{header}\n\n{text}")


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "Create a text file with the given content. "
        "If the file already exists, set overwrite to true only when the user explicitly asked to replace it. "
        "Call this when the user asks to save, create, or write a file. "
        'Example: {"path": "~/notes.txt", "content": "Hello world"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or ~ path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The full text content to write into the file.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Set true only when replacing an existing file is intentional.",
            },
            "approval_id": {
                "type": "string",
                "description": "Server-issued approval id for this exact overwrite action.",
            },
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve file overwrites."},
        },
        "required": ["path", "content"],
    }
    costly = False

    def __init__(
        self,
        allowed_roots: tuple[Path, ...] = (),
        default_path: Path | None = None,
        approval_manager: ApprovalManager | None = None,
        trusted_overwrite_token: str = "",
    ):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER
        self._trusted_overwrite_token = trusted_overwrite_token

    async def execute(
        self,
        path: str = "",
        content: str = "",
        overwrite: bool = False,
        approval_id: str = "",
        confirmed: bool = False,
        trusted_overwrite_token: str = "",
        **_,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(
                self._run,
                path,
                content,
                overwrite is True,
                approval_id,
                trusted_overwrite_token,
            )
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("write_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, content: str, overwrite: bool, approval_id: str, trusted_overwrite_token: str) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if p.exists() and not p.is_file():
            return ToolResult(content=f"Path exists and is not a file: {p}")
        if p.exists() and not overwrite:
            return ToolResult(
                content=(
                    f"File already exists: {p}. "
                    "Call write_file with overwrite=true only if the user asked to replace it."
                )
            )
        if p.exists() and overwrite and not _trusted_overwrite(trusted_overwrite_token, self._trusted_overwrite_token):
            approval = _approval_or_result(
                self._approvals,
                tool_name=self.name,
                title="file overwrite",
                details={
                    "path": str(p),
                    "content_length": len(content.encode()),
                    "content_sha256": _content_fingerprint(content),
                },
                approval_id=approval_id,
            )
            if approval is not None:
                return approval
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content)
        verb = "Updated" if existed else "Created"
        return ToolResult(content=f"{verb}: {p} ({_human_size(len(content.encode()))})")


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "Find and replace text inside an existing file. "
        "Call this to make targeted edits without rewriting the whole file. "
        'Example: {"path": "~/notes.txt", "find": "old text", "replace": "new text"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or ~ path to the file to edit.",
            },
            "find": {
                "type": "string",
                "description": "The exact text to search for. All occurrences will be replaced.",
            },
            "replace": {
                "type": "string",
                "description": "The text to substitute in place of each match. Use \"\" to delete the matched text.",
            },
        },
        "required": ["path", "find", "replace"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", find: str = "", replace: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, find, replace)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("edit_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, find: str, replace: str) -> ToolResult:
        if not find:
            return ToolResult(content="Missing 'find' text.")
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {p}")
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            return ToolResult(content=f"Cannot edit {p.name}: file appears to be binary, not text.")
        count = text.count(find)
        if count == 0:
            return ToolResult(content=f"Text not found in {p.name}. No changes made.")
        new_text = text.replace(find, replace)
        p.write_text(new_text)
        return ToolResult(content=f"Edited {p.name}: replaced {count} occurrence{'s' if count != 1 else ''}.")


class AppendFileTool(BaseTool):
    name = "append_file"
    description = "Append text to an existing sandboxed text file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute, ~, or relative path to the file."},
            "content": {"type": "string", "description": "Text to append."},
        },
        "required": ["path", "content"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", content: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, content)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("append_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, content: str) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {p}")
        try:
            existing = p.read_text()
        except UnicodeDecodeError:
            return ToolResult(content=f"Cannot append to {p.name}: file appears to be binary, not text.")
        p.write_text(existing + content)
        return ToolResult(content=f"Appended to: {p} ({_human_size(len(content.encode()))})")


class PrependFileTool(BaseTool):
    name = "prepend_file"
    description = "Prepend text to an existing sandboxed text file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute, ~, or relative path to the file."},
            "content": {"type": "string", "description": "Text to prepend."},
        },
        "required": ["path", "content"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", content: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, content)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("prepend_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, content: str) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {p}")
        try:
            existing = p.read_text()
        except UnicodeDecodeError:
            return ToolResult(content=f"Cannot prepend to {p.name}: file appears to be binary, not text.")
        p.write_text(content + existing)
        return ToolResult(content=f"Prepended to: {p} ({_human_size(len(content.encode()))})")


class CompareFilesTool(BaseTool):
    name = "compare_files"
    description = "Compare two sandboxed text files and return a bounded unified diff."
    parameters = {
        "type": "object",
        "properties": {
            "left_path": {"type": "string", "description": "First file path."},
            "right_path": {"type": "string", "description": "Second file path."},
        },
        "required": ["left_path", "right_path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, left_path: str = "", right_path: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, left_path, right_path)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("compare_files failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _read_text_file(self, path: str) -> tuple[Path, str] | ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {p}")
        if p.stat().st_size > _MAX_READ_BYTES:
            return ToolResult(content=f"Cannot compare {p.name}: file is larger than {_MAX_READ_BYTES // 1000}KB.")
        data = p.read_bytes()
        if b"\x00" in data:
            return ToolResult(content=f"Cannot compare {p.name}: file appears to be binary, not text.")
        try:
            return p, data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(content=f"Cannot compare {p.name}: file appears to be binary, not text.")

    def _run(self, left_path: str, right_path: str) -> ToolResult:
        left = self._read_text_file(left_path)
        if isinstance(left, ToolResult):
            return left
        right = self._read_text_file(right_path)
        if isinstance(right, ToolResult):
            return right
        left_file, left_text = left
        right_file, right_text = right
        if left_text == right_text:
            return ToolResult(content=f"Files are identical: {left_file} and {right_file}")
        diff = difflib.unified_diff(
            left_text.splitlines(),
            right_text.splitlines(),
            fromfile=left_file.name,
            tofile=right_file.name,
            lineterm="",
        )
        return ToolResult(content="\n".join(list(diff)[:400]))


class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = (
        "List the files and folders inside a directory. "
        "Call this when the user asks what is in a folder or wants to browse a path. "
        'Example: {"path": "~/Documents"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or ~ path to the directory to list.",
            },
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("list_directory failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_dir():
            return ToolResult(content=f"Not a directory: {p}")
        try:
            entries = sorted(
                (e for i, e in enumerate(p.iterdir()) if i < _MAX_LIST_ENTRIES + 1),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            return ToolResult(content=f"Permission denied: cannot list {p}")
        truncated = len(entries) > _MAX_LIST_ENTRIES
        if truncated:
            entries = entries[:_MAX_LIST_ENTRIES]
        lines = []
        for e in entries:
            prefix = "📁 " if e.is_dir() else "   "
            try:
                size = f"  ({_human_size(e.stat().st_size)})" if e.is_file() else ""
            except OSError:
                size = ""
            lines.append(f"{prefix}{e.name}{size}")
        header = f"Directory: {p}\n{len(entries)} items"
        if truncated:
            header += f" (showing first {_MAX_LIST_ENTRIES})"
        return ToolResult(content=header + "\n\n" + "\n".join(lines))


class CreateDirectoryTool(BaseTool):
    name = "create_directory"
    description = (
        "Create a new directory, including any missing parent directories. "
        "Call this when the user wants to make a new folder or project structure. "
        'Example: {"path": "~/Projects/new-project"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or ~ path to the directory to create.",
            },
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("create_directory failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if p.exists():
            return ToolResult(content=f"Already exists: {p}")
        p.mkdir(parents=True, exist_ok=True)
        return ToolResult(content=f"Created directory: {p}")


class MovePathTool(BaseTool):
    name = "move_path"
    description = (
        "Move a file or folder within the allowed filesystem sandbox. "
        "If the destination exists, overwrite must be true and only when the user explicitly asked to replace it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "File or folder to move."},
            "destination": {"type": "string", "description": "Destination file or folder path."},
            "overwrite": {
                "type": "boolean",
                "description": "Set true only when replacing the destination is explicitly requested.",
            },
            "approval_id": {
                "type": "string",
                "description": "Server-issued approval id for this exact overwrite action.",
            },
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve move overwrites."},
        },
        "required": ["source", "destination"],
    }
    costly = False

    def __init__(
        self,
        allowed_roots: tuple[Path, ...] = (),
        default_path: Path | None = None,
        approval_manager: ApprovalManager | None = None,
        trusted_overwrite_token: str = "",
    ):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER
        self._trusted_overwrite_token = trusted_overwrite_token

    async def execute(
        self,
        source: str = "",
        destination: str = "",
        overwrite: bool = False,
        approval_id: str = "",
        confirmed: bool = False,
        trusted_overwrite_token: str = "",
        **_,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(
                self._run,
                source,
                destination,
                overwrite is True,
                approval_id,
                trusted_overwrite_token,
            )
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("move_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, source: str, destination: str, overwrite: bool, approval_id: str, trusted_overwrite_token: str) -> ToolResult:
        src = _resolve(source, self._roots, self._default_path)
        dest = _resolve(destination, self._roots, self._default_path)
        if not src.exists():
            return ToolResult(content=f"Source does not exist: {src}")
        if dest.exists():
            if not overwrite:
                return ToolResult(
                    content=(
                        f"Destination already exists: {dest}. "
                        "Call move_path with overwrite=true only if the user asked to replace it."
                    )
                )
            if not _trusted_overwrite(trusted_overwrite_token, self._trusted_overwrite_token):
                approval = _approval_or_result(
                    self._approvals,
                    tool_name=self.name,
                    title="move overwrite",
                    details={"source": str(src), "destination": str(dest)},
                    approval_id=approval_id,
                )
                if approval is not None:
                    return approval
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
            shutil.rmtree(src)
        else:
            shutil.copy2(src, dest)
            src.unlink()
        return ToolResult(content=f"Moved: {src} -> {dest}")


class CopyPathTool(BaseTool):
    name = "copy_path"
    description = (
        "Copy a file or folder within the allowed filesystem sandbox. "
        "If the destination exists, overwrite must be true and only when the user explicitly asked to replace it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "File or folder to copy."},
            "destination": {"type": "string", "description": "Destination file or folder path."},
            "overwrite": {
                "type": "boolean",
                "description": "Set true only when replacing the destination is explicitly requested.",
            },
            "approval_id": {
                "type": "string",
                "description": "Server-issued approval id for this exact overwrite action.",
            },
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve copy overwrites."},
        },
        "required": ["source", "destination"],
    }
    costly = False

    def __init__(
        self,
        allowed_roots: tuple[Path, ...] = (),
        default_path: Path | None = None,
        approval_manager: ApprovalManager | None = None,
        trusted_overwrite_token: str = "",
    ):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER
        self._trusted_overwrite_token = trusted_overwrite_token

    async def execute(
        self,
        source: str = "",
        destination: str = "",
        overwrite: bool = False,
        approval_id: str = "",
        confirmed: bool = False,
        trusted_overwrite_token: str = "",
        **_,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(
                self._run,
                source,
                destination,
                overwrite is True,
                approval_id,
                trusted_overwrite_token,
            )
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("copy_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, source: str, destination: str, overwrite: bool, approval_id: str, trusted_overwrite_token: str) -> ToolResult:
        src = _resolve(source, self._roots, self._default_path)
        dest = _resolve(destination, self._roots, self._default_path)
        if not src.exists():
            return ToolResult(content=f"Source does not exist: {src}")
        if dest.exists():
            if not overwrite:
                return ToolResult(
                    content=(
                        f"Destination already exists: {dest}. "
                        "Call copy_path with overwrite=true only if the user asked to replace it."
                    )
                )
            if not _trusted_overwrite(trusted_overwrite_token, self._trusted_overwrite_token):
                approval = _approval_or_result(
                    self._approvals,
                    tool_name=self.name,
                    title="copy overwrite",
                    details={"source": str(src), "destination": str(dest)},
                    approval_id=approval_id,
                )
                if approval is not None:
                    return approval
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        return ToolResult(content=f"Copied: {src} -> {dest}")


class RenamePathTool(BaseTool):
    name = "rename_path"
    description = "Rename a sandboxed file or folder in place. The new name cannot include path separators."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or folder to rename."},
            "new_name": {"type": "string", "description": "New filename only, not a path."},
        },
        "required": ["path", "new_name"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", new_name: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, new_name)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("rename_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, new_name: str) -> ToolResult:
        source = _resolve(path, self._roots, self._default_path)
        new_name = new_name.strip()
        if not source.exists():
            return ToolResult(content=f"Path does not exist: {source}")
        if not new_name or "/" in new_name or "\\" in new_name or new_name in {".", ".."}:
            return ToolResult(content="New name must not contain path separators.")
        destination = _resolve(str(source.parent / new_name), self._roots, self._default_path)
        if destination.exists():
            return ToolResult(content=f"Destination already exists: {destination}")
        source.rename(destination)
        return ToolResult(content=f"Renamed: {source} -> {destination}")


class DuplicatePathTool(BaseTool):
    name = "duplicate_path"
    description = "Duplicate a sandboxed file or folder. If destination is omitted, uses '<name> copy'."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or folder to duplicate."},
            "destination": {"type": "string", "description": "Optional duplicate destination."},
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", destination: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, destination)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("duplicate_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _default_duplicate_path(self, source: Path) -> Path:
        if source.is_dir():
            candidate = source.with_name(f"{source.name} copy")
        else:
            candidate = source.with_name(f"{source.stem} copy{source.suffix}")
        if not candidate.exists():
            return candidate
        for index in range(2, 1000):
            if source.is_dir():
                candidate = source.with_name(f"{source.name} copy {index}")
            else:
                candidate = source.with_name(f"{source.stem} copy {index}{source.suffix}")
            if not candidate.exists():
                return candidate
        raise FileExistsError("Could not find an available duplicate name.")

    def _run(self, path: str, destination: str) -> ToolResult:
        source = _resolve(path, self._roots, self._default_path)
        if not source.exists():
            return ToolResult(content=f"Path does not exist: {source}")
        dest = _resolve(destination, self._roots, self._default_path) if destination else self._default_duplicate_path(source)
        if dest.exists():
            return ToolResult(content=f"Destination already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            shutil.copy2(source, dest)
        return ToolResult(content=f"Duplicated: {source} -> {dest}")


class CompressPathTool(BaseTool):
    name = "compress_path"
    description = "Compress a sandboxed file or folder to a zip archive."
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "File or folder to compress."},
            "destination": {"type": "string", "description": "Destination .zip archive path."},
        },
        "required": ["source", "destination"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, source: str = "", destination: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, source, destination)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("compress_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, source: str, destination: str) -> ToolResult:
        src = _resolve(source, self._roots, self._default_path)
        dest = _resolve(destination, self._roots, self._default_path)
        if not src.exists():
            return ToolResult(content=f"Source does not exist: {src}")
        if dest.exists():
            return ToolResult(content=f"Destination already exists: {dest}")
        if dest.suffix.lower() != ".zip":
            return ToolResult(content="Compression destination must end with .zip.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        base_name = str(dest.with_suffix(""))
        archive_path = shutil.make_archive(base_name, "zip", root_dir=src.parent, base_dir=src.name)
        return ToolResult(content=f"Compressed: {src} -> {archive_path}")


class UncompressArchiveTool(BaseTool):
    name = "uncompress_archive"
    description = "Uncompress a sandboxed zip archive into a new sandboxed destination folder."
    parameters = {
        "type": "object",
        "properties": {
            "archive": {"type": "string", "description": "Zip archive to extract."},
            "destination": {"type": "string", "description": "Destination folder that must not already exist."},
        },
        "required": ["archive", "destination"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, archive: str = "", destination: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, archive, destination)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("uncompress_archive failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, archive: str, destination: str) -> ToolResult:
        archive_path = _resolve(archive, self._roots, self._default_path)
        dest = _resolve(destination, self._roots, self._default_path)
        if not archive_path.is_file():
            return ToolResult(content=f"Not a file: {archive_path}")
        if archive_path.suffix.lower() != ".zip":
            return ToolResult(content="Only .zip archives are supported.")
        if dest.exists():
            return ToolResult(content=f"Destination already exists: {dest}")
        dest.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive_path) as zf:
                for member in zf.infolist():
                    target = (dest / member.filename).resolve()
                    if not (target == dest or dest in target.parents):
                        shutil.rmtree(dest)
                        return ToolResult(content="Archive contains a path outside the destination. Extraction refused.")
                zf.extractall(dest)
        except zipfile.BadZipFile:
            shutil.rmtree(dest)
            return ToolResult(content=f"Not a valid zip archive: {archive_path}")
        return ToolResult(content=f"Uncompressed: {archive_path} -> {dest}")


class MoveToTrashTool(BaseTool):
    name = "move_to_trash"
    description = (
        "Move a sandboxed file or folder to the user's macOS Trash instead of deleting it permanently. "
        "Use this for remove/delete requests by default."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Sandboxed file or folder to move to Trash."},
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._trash = (Path.home() / ".Trash").resolve()

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("move_to_trash failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str) -> ToolResult:
        source = _resolve(path, self._roots, self._default_path)
        if not source.exists():
            return ToolResult(content=f"Path does not exist: {source}")
        self._trash.mkdir(parents=True, exist_ok=True)
        destination = self._unique_trash_path(source.name)
        shutil.move(str(source), str(destination))
        return ToolResult(content=f"Moved to Trash: {source} -> {destination}")

    def _unique_trash_path(self, name: str) -> Path:
        original = Path(name)
        stem = original.stem
        suffix = original.suffix
        for _ in range(10):
            candidate = self._trash / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
            if not candidate.exists():
                return candidate
        return self._trash / f"{stem}-{uuid.uuid4().hex}{suffix}"


class RestoreFromTrashTool(BaseTool):
    name = "restore_from_trash"
    description = "Restore a file or folder from macOS Trash to a sandboxed destination path."
    parameters = {
        "type": "object",
        "properties": {
            "trash_path": {"type": "string", "description": "Path under ~/.Trash returned by move_to_trash."},
            "destination": {"type": "string", "description": "Sandboxed restore destination."},
        },
        "required": ["trash_path", "destination"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._trash = (Path.home() / ".Trash").resolve()

    async def execute(self, trash_path: str = "", destination: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, trash_path, destination)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("restore_from_trash failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, trash_path: str, destination: str) -> ToolResult:
        source = Path(trash_path).expanduser().resolve()
        if not (source == self._trash or self._trash in source.parents):
            return ToolResult(content=f"Access denied: trash_path must be under {self._trash}")
        if not source.exists():
            return ToolResult(content=f"Trash item does not exist: {source}")
        dest = _resolve(destination, self._roots, self._default_path)
        if dest.exists():
            return ToolResult(content=f"Destination already exists: {dest}. Choose a different restore path.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        return ToolResult(content=f"Restored: {source} -> {dest}")


class DeletePermanentlyTool(BaseTool):
    name = "delete_permanently"
    description = (
        "Permanently delete a sandboxed file or folder. This is irreversible and always requires server-side "
        "approval. Prefer move_to_trash for normal remove/delete requests."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Sandboxed file or folder to permanently delete."},
            "approval_id": {"type": "string", "description": "Server-issued approval id for this exact deletion."},
        },
        "required": ["path"],
    }
    costly = False

    def __init__(
        self,
        allowed_roots: tuple[Path, ...] = (),
        default_path: Path | None = None,
        approval_manager: ApprovalManager | None = None,
    ):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(self, path: str = "", approval_id: str = "", **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, approval_id)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("delete_permanently failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, approval_id: str) -> ToolResult:
        target = _resolve(path, self._roots, self._default_path)
        if not target.exists():
            return ToolResult(content=f"Path does not exist: {target}")
        if any(target == root for root in self._roots):
            return ToolResult(content=f"Refusing to permanently delete a sandbox root: {target}")
        details = {"path": str(target), "irreversible": True}
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="permanent deletion",
            details=details,
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return ToolResult(content=f"Permanently deleted: {target}")


class OpenPathTool(BaseTool):
    name = "open_path"
    description = "Open an allowed file or folder with the default macOS app."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or folder to open."},
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            p = _resolve(path, self._roots, self._default_path)
            if not p.exists():
                return ToolResult(content=f"Path does not exist: {p}")
            proc = await asyncio.create_subprocess_exec(
                "open",
                str(p),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(content=f"Opened: {p}")
            if proc.returncode != 0:
                detail = stderr.decode(errors="replace").strip()
                return ToolResult(content=f"Could not open {p}: {detail or 'open failed'}")
            return ToolResult(content=f"Opened: {p}")
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("open_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")


class RevealPathTool(BaseTool):
    name = "reveal_path"
    description = "Reveal an allowed file or folder in Finder."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or folder to reveal in Finder."},
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            p = _resolve(path, self._roots, self._default_path)
            if not p.exists():
                return ToolResult(content=f"Path does not exist: {p}")
            proc = await asyncio.create_subprocess_exec(
                "open",
                "-R",
                str(p),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                detail = stderr.decode(errors="replace").strip()
                return ToolResult(content=f"Could not reveal {p}: {detail or 'open -R failed'}")
            return ToolResult(content=f"Revealed in Finder: {p}")
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("reveal_path failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")
