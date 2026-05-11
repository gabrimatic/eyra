"""Tests for distributable package metadata."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPackageMetadata:
    def test_console_script_points_to_packaged_main_module(self):
        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

        assert data["project"]["scripts"]["eyra"] == "main:run"
        wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert wheel["force-include"]["src/main.py"] == "main.py"
