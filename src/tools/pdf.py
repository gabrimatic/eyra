"""Local-only PDF text extraction tool."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.filesystem import _DEFAULT_ALLOWED_ROOTS, _human_size, _resolve

logger = logging.getLogger(__name__)

_MAX_PDF_TEXT_CHARS = 80_000


def _decode_pdf_string(raw: str) -> str:
    return (
        raw.replace(r"\(", "(")
        .replace(r"\)", ")")
        .replace(r"\\", "\\")
        .replace(r"\n", "\n")
        .replace(r"\r", "\n")
        .replace(r"\t", "\t")
    )


def _fallback_extract_text(pdf_bytes: bytes) -> tuple[list[str], int]:
    """Small fallback extractor for simple text PDFs when pypdf is unavailable."""
    text = pdf_bytes.decode("latin-1", errors="ignore")
    page_count = max(1, len(re.findall(r"/Type\s*/Page\b", text)))
    parts = []
    for match in re.finditer(r"\((.*?)\)\s*Tj", text, flags=re.DOTALL):
        value = _decode_pdf_string(match.group(1)).strip()
        if value:
            parts.append(value)
    return parts, page_count


class ReadPdfTool(BaseTool):
    name = "read_pdf"
    description = (
        "Extract text from a local PDF inside the filesystem sandbox. "
        "Use this for reading or summarizing PDFs. It is local-only and does not perform OCR."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute, ~, or sandbox-relative PDF path."},
            "max_chars": {
                "type": "integer",
                "description": "Maximum extracted characters to return. Default 80000.",
            },
        },
        "required": ["path"],
    }
    costly = False

    def __init__(self, allowed_roots: tuple[Path, ...] = (), default_path: Path | None = None):
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None

    async def execute(self, path: str = "", max_chars: int = _MAX_PDF_TEXT_CHARS, **_) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run, path, max_chars)
        except PermissionError as e:
            return ToolResult(content=str(e))
        except Exception as e:
            logger.error("read_pdf failed: %s", e, exc_info=True)
            return ToolResult(content=f"PDF error: {e}")

    def _run(self, path: str, max_chars: int) -> ToolResult:
        p = _resolve(path, self._roots, self._default_path)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {p}")
        if p.suffix.lower() != ".pdf":
            return ToolResult(content=f"Not a PDF file: {p}")

        max_chars = max(1000, min(int(max_chars or _MAX_PDF_TEXT_CHARS), _MAX_PDF_TEXT_CHARS))
        size = p.stat().st_size
        pages: list[str] = []
        page_count = 0

        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            page_count = len(reader.pages)
            for index, page in enumerate(reader.pages, start=1):
                extracted = page.extract_text() or ""
                extracted = extracted.strip()
                if extracted:
                    pages.append(f"[Page {index}]\n{extracted}")
        except ImportError:
            parts, page_count = _fallback_extract_text(p.read_bytes())
            if parts:
                pages.append("[Page 1]\n" + "\n".join(parts))
        except Exception as e:
            return ToolResult(content=f"Could not read PDF {p.name}: {e}")

        body = "\n\n".join(pages).strip()
        header = f"PDF: {p} ({_human_size(size)})\nPages: {page_count}"
        if not body:
            return ToolResult(
                content=(
                    f"{header}\n\nNo extractable text found. "
                    "This PDF may be scanned or image-only. Eyra does not use online OCR silently."
                )
            )
        truncated = len(body) > max_chars
        if truncated:
            body = body[:max_chars].rstrip()
            header += f"\nShowing first {max_chars} characters"
        return ToolResult(content=f"{header}\n\n{body}")
