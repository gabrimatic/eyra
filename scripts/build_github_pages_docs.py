#!/usr/bin/env python3
"""Build the Mintlify export for GitHub Pages project hosting."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


TEXT_EXTENSIONS = {".html", ".js", ".css", ".json", ".txt", ".xml", ".svg", ".webmanifest"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Mintlify docs and patch root URLs for GitHub Pages.")
    parser.add_argument("--docs-dir", default="docs", help="Directory containing docs.json")
    parser.add_argument("--out-dir", default="site", help="Output directory for GitHub Pages")
    parser.add_argument("--base-path", default="", help="GitHub Pages base path, for example /eyra")
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    base_path = _normalize_base_path(args.base_path)
    export_zip = out_dir.parent / "mintlify-export.zip"

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if export_zip.exists():
        export_zip.unlink()

    subprocess.run(
        ["mint", "export", "--disable-openapi", "--output", str(export_zip)],
        cwd=docs_dir,
        check=True,
        env={**os.environ, "MINTLIFY_TELEMETRY_DISABLED": "1"},
    )

    with zipfile.ZipFile(export_zip) as archive:
        archive.extractall(out_dir)

    if base_path:
        _patch_root_urls(out_dir, base_path)

    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    export_zip.unlink(missing_ok=True)
    return 0


def _normalize_base_path(value: str) -> str:
    stripped = value.strip().strip("/")
    return f"/{stripped}" if stripped else ""


def _patch_root_urls(out_dir: Path, base_path: str) -> None:
    replacements = [
        (re.compile(r'(?P<prefix>["\'(=])/(?P<path>_next/)'), rf"\g<prefix>{base_path}/\g<path>"),
        (
            re.compile(r'(?P<prefix>["\'(=])/(?P<path>favicon\.svg|logo-light\.svg|logo-dark\.svg|favicons/|sitemap\.xml)'),
            rf"\g<prefix>{base_path}/\g<path>",
        ),
        (re.compile(r'(?P<prefix>["\'])/(?P<path>(?:architecture|development|get-started|guides|project|reference|security)/)'), rf"\g<prefix>{base_path}/\g<path>"),
        (re.compile(r'(?P<prefix>["\'])/(?P<suffix>["\'])'), rf"\g<prefix>{base_path}/\g<suffix>"),
    ]
    for path in out_dir.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        patched = text
        for pattern, replacement in replacements:
            patched = pattern.sub(replacement, patched)
        if patched != text:
            path.write_text(patched, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
