"""Tests for local PDF extraction and summarization support."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.pdf import ReadPdfTool


def _run(coro):
    return asyncio.run(coro)


def _minimal_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream.encode())} >> stream\n{stream}\nendstream endobj\n",
    ]
    content = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(content.encode()))
        content += obj
    xref_offset = len(content.encode())
    content += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        content += f"{offset:010d} 00000 n \n"
    content += f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_offset}\n%%EOF\n"
    path.write_bytes(content.encode())


class TestReadPdfTool:
    def test_extracts_text_from_local_pdf(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                pdf = root / "sample.pdf"
                _minimal_pdf(pdf, "Quarterly decision: ship local mode.")

                result = await ReadPdfTool(allowed_roots=(root,)).execute(path=str(pdf))

                assert "Quarterly decision" in result.content
                assert "Pages: 1" in result.content

        _run(run())

    def test_empty_pdf_reports_scanned_or_image_only(self):
        async def run():
            with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
                root = Path(d)
                pdf = root / "empty.pdf"
                _minimal_pdf(pdf, "")

                result = await ReadPdfTool(allowed_roots=(root,)).execute(path=str(pdf))

                assert "image-only" in result.content.lower() or "no extractable text" in result.content.lower()

        _run(run())

    def test_blocks_outside_sandbox(self):
        async def run():
            result = await ReadPdfTool(allowed_roots=(Path("/tmp"),)).execute(path="/etc/passwd")
            assert "Access denied" in result.content

        _run(run())
