#!/usr/bin/env python3
"""Run Eyra's local voice-to-computer certification matrix."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.certification import run_certification
from utils.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Eyra certification checks.")
    parser.add_argument("--include-physical", action="store_true", help="Label physical microphone/barge-in checks as requested.")
    args = parser.parse_args()

    report = run_certification(Settings.load_from_env(), include_physical=args.include_physical)
    print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
