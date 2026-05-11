"""Filesystem tools — read, write, edit, and navigate files and directories."""

import asyncio
import logging
from pathlib import Path

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_MAX_READ_BYTES = 64_000  # ~64 KB text read limit
_MAX_LIST_ENTRIES = 200
_DEFAULT_ALLOWED_ROOTS = (Path.home() / "Documents", Path("/tmp").resolve())


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
        },
        "required": ["path", "content"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", content: str = "", overwrite: bool = False, **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, content, overwrite is True)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("write_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Filesystem error: {e}")

    def _run(self, path: str, content: str, overwrite: bool) -> ToolResult:
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
