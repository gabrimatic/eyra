"""Tests for non-blocking system sound playback."""

import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.sound_player import play_sound


def _run(coro):
    return asyncio.run(coro)


def test_darwin_sound_launches_detached_process():
    with patch("utils.sound_player.platform.system", return_value="Darwin"):
        with patch("utils.sound_player.os.path.exists", return_value=True):
            with patch("utils.sound_player.subprocess.Popen") as popen:
                _run(play_sound("listen"))

    popen.assert_called_once()
    assert popen.call_args.args[0][0] == "afplay"


def test_darwin_missing_sound_does_not_launch_process():
    with patch("utils.sound_player.platform.system", return_value="Darwin"):
        with patch("utils.sound_player.os.path.exists", return_value=False):
            with patch("utils.sound_player.subprocess.Popen") as popen:
                _run(play_sound("listen"))

    popen.assert_not_called()
