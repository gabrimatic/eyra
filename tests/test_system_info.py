"""Tests for deterministic local system-info formatting."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.system_info import format_system_info_for_query

SYSTEM_INFO = """macOS: 26.4.1 (25E253)
Disk: 25Gi available of 460Gi total (95% used)
Memory: System-wide memory free percentage: 20%
Uptime: 10:00 up 19 days
"""


def test_format_system_info_returns_only_requested_macos_line():
    assert format_system_info_for_query(SYSTEM_INFO, "macOS version only") == "macOS: 26.4.1 (25E253)"


def test_format_system_info_can_return_multiple_requested_lines():
    assert format_system_info_for_query(SYSTEM_INFO, "disk and memory") == (
        "Disk: 25Gi available of 460Gi total (95% used)\nMemory: System-wide memory free percentage: 20%"
    )


def test_format_system_info_reports_unavailable_battery_on_desktop():
    assert format_system_info_for_query(SYSTEM_INFO, "battery level") == "Battery information is unavailable on this Mac."
