#!/usr/bin/env python3
"""Run Eyra's local voice-to-computer certification matrix."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.certification import run_certification
from utils.settings import Settings

_SYNTHETIC_MIC_TEXT = (
    "show status show status show status show status show status "
    "show status show status show status show status show status"
)


def _start_synthetic_mic(settings: Settings, text: str) -> bool:
    if not settings.LIVE_LISTENING_ENABLED:
        return False
    fake_mic = shutil.which("fake-mic")
    if not fake_mic:
        raise RuntimeError("fake-mic is required for --synthetic-mic but was not found on PATH.")
    subprocess.run([fake_mic, "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    subprocess.run([fake_mic, "start", text], check=True)
    time.sleep(0.75)
    return True


def _stop_synthetic_mic() -> None:
    fake_mic = shutil.which("fake-mic")
    if fake_mic:
        subprocess.run([fake_mic, "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Eyra certification checks.")
    parser.add_argument("--include-physical", action="store_true", help="Label physical microphone/barge-in checks as requested.")
    parser.add_argument(
        "--synthetic-mic",
        action="store_true",
        help="Start fake-mic and use a configured virtual microphone, such as BlackHole, as the attended barge-in source.",
    )
    parser.add_argument(
        "--synthetic-mic-text",
        default=_SYNTHETIC_MIC_TEXT,
        help="Text to feed through fake-mic when --synthetic-mic is set.",
    )
    parser.add_argument(
        "--human-phrase",
        default="",
        help=(
            "Attended physical barge-in challenge phrase. The diagnostic passes only if ASR returns this phrase; "
            "the TTS prompt will not speak it, so speaker echo cannot satisfy the check."
        ),
    )
    args = parser.parse_args()

    settings = Settings.load_from_env()
    synthetic_started = False
    try:
        if args.synthetic_mic:
            synthetic_started = _start_synthetic_mic(settings, args.synthetic_mic_text)
        report = run_certification(
            settings,
            include_physical=args.include_physical,
            synthetic_mic=args.synthetic_mic,
            human_phrase=args.human_phrase,
        )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Synthetic microphone setup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if synthetic_started:
            _stop_synthetic_mic()
    print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
