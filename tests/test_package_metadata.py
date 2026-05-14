"""Tests for distributable package metadata."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPackageMetadata:
    def test_console_script_points_to_packaged_main_module(self):
        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

        assert data["project"]["scripts"]["eyra"] == "main:run"
        assert data["project"]["scripts"]["eyra-doctor"] == "runtime.cli:doctor"
        assert data["project"]["scripts"]["eyra-setup"] == "runtime.cli:setup"
        assert data["project"]["scripts"]["eyra-certify"] == "runtime.cli:certify"
        wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert wheel["force-include"]["src/main.py"] == "main.py"

    def test_release_candidate_version_is_pep440(self):
        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

        assert data["project"]["version"] == "4.2.0rc1"
