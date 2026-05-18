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
        assert data["project"]["scripts"]["eyra-menu"] == "runtime.cli:menu"
        wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert wheel["force-include"]["src/main.py"] == "main.py"
        assert wheel["force-include"]["apps/EyraMenuBar/Package.swift"] == "runtime/resources/EyraMenuBar/Package.swift"
        assert wheel["force-include"]["apps/EyraMenuBar/Sources"] == "runtime/resources/EyraMenuBar/Sources"
        assert wheel["force-include"]["apps/EyraMenuBar/Tests"] == "runtime/resources/EyraMenuBar/Tests"
        assert wheel["force-include"]["scripts/build_menu_bar_app.sh"] == "runtime/resources/scripts/build_menu_bar_app.sh"
        assert all(".build" not in path for path in wheel["force-include"])
        assert all(".build" not in path for path in wheel["force-include"].values())

    def test_release_candidate_version_is_pep440(self):
        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

        assert data["project"]["version"] == "4.3.1"
