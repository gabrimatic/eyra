"""Tests for preflight backend detection and model recovery."""

import asyncio
import os
import subprocess
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.preflight import PreflightManager
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


class _Response:
    def __init__(self, status_code: int, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class _OllamaCompatibleClient:
    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url: str):
        if url.endswith("/v1/models"):
            return _Response(200, {"data": [{"id": "gemma4:e4b"}]})
        if url.endswith("/api/tags"):
            return _Response(200, {"models": [{"name": "gemma4:e4b"}]})
        return _Response(404)

    async def post(self, url: str, json: dict):
        if url.endswith("/api/show"):
            return _Response(200, {"capabilities": ["completion", "vision"]})
        return _Response(404)


class _HangingProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False

    async def wait(self):
        if not self.killed:
            await asyncio.sleep(60)
        self.returncode = -9
        return self.returncode

    def kill(self):
        self.killed = True


class TestPreflightBackend:
    def test_ollama_detected_when_v1_models_works(self):
        manager = PreflightManager(Settings())

        with patch("runtime.preflight.httpx.AsyncClient", _OllamaCompatibleClient):
            assert _run(manager._check_backend()) is True

        assert manager._is_ollama is True

    def test_ollama_model_without_tools_warns_but_stays_ready(self, capsys):
        settings = Settings(LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        result = PreflightManager(settings)
        with patch("runtime.preflight.httpx.AsyncClient", _OllamaCompatibleClient):
            preflight = _run(result.run())

        assert preflight.models_ready == ["gemma4:e4b"]
        assert preflight.models_missing == []
        assert "lacks native tool calling" in capsys.readouterr().out

    def test_mock_client_bypasses_backend_and_models(self):
        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        manager = PreflightManager(settings)

        with patch.object(manager, "_check_backend", new_callable=AsyncMock) as mock_backend:
            result = _run(manager.run())

        assert result.backend_reachable is True
        assert result.models_ready == [settings.MODEL]
        assert result.models_missing == []
        mock_backend.assert_not_called()

    def test_resolve_wh_uses_launch_agent_program_arguments(self, tmp_path):
        wh = tmp_path / "wh"
        wh.write_text("#!/bin/sh\n")
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / "com.local-whisper.plist"
        plist.write_bytes(
            b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>ProgramArguments</key>
  <array>
    <string>"""
            + str(wh).encode()
            + b"""</string>
    <string>serve</string>
  </array>
</dict>
</plist>
"""
        )

        with patch("runtime.preflight.shutil.which", return_value=None):
            with patch("runtime.preflight.pathlib.Path.home", return_value=tmp_path):
                assert PreflightManager._resolve_wh() == str(wh)

    def test_speech_only_preflight_skips_asr_probe(self):
        settings = Settings(LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=True)
        manager = PreflightManager(settings)
        from runtime.models import PreflightResult

        result = PreflightResult()
        with patch("runtime.preflight.PreflightManager._resolve_wh", return_value="/opt/wh"):
            with patch("runtime.preflight.PreflightManager._wh_is_running", return_value=True):
                with patch("runtime.preflight.PreflightManager._start_wh_service", return_value=True):
                    with patch("runtime.preflight.PreflightManager._wait_for_asr_ready") as asr_probe:
                        with patch("runtime.preflight.PreflightManager._probe_microphone_ready") as mic_probe:
                            _run(manager.check_local_whisper(result))

        assert result.wh_available is True
        assert result.speech_available is True
        assert result.listening_available is False
        assert result.wh_bin == "/opt/wh"
        asr_probe.assert_not_called()
        mic_probe.assert_not_called()

    def test_listening_only_preflight_disables_voice_when_asr_is_not_ready(self):
        settings = Settings(LIVE_LISTENING_ENABLED=True, LIVE_SPEECH_ENABLED=False)
        manager = PreflightManager(settings)
        result = _run(_check_wh_with(
            manager,
            resolve="/opt/wh",
            running=True,
            asr_ready=False,
        ))

        assert result.wh_available is False
        assert result.speech_available is False
        assert result.listening_available is False
        assert result.wh_bin is None

    def test_combined_voice_keeps_speech_when_asr_is_not_ready(self):
        settings = Settings(LIVE_LISTENING_ENABLED=True, LIVE_SPEECH_ENABLED=True)
        manager = PreflightManager(settings)
        result = _run(_check_wh_with(
            manager,
            resolve="/opt/wh",
            running=True,
            asr_ready=False,
        ))

        assert result.wh_available is True
        assert result.speech_available is True
        assert result.listening_available is False
        assert result.wh_bin == "/opt/wh"

    def test_combined_voice_keeps_speech_when_microphone_probe_fails(self):
        settings = Settings(LIVE_LISTENING_ENABLED=True, LIVE_SPEECH_ENABLED=True)
        manager = PreflightManager(settings)
        result = _run(_check_wh_with(
            manager,
            resolve="/opt/wh",
            running=True,
            asr_ready=True,
            mic_ready=False,
        ))

        assert result.wh_available is True
        assert result.speech_available is True
        assert result.listening_available is False
        assert result.wh_bin == "/opt/wh"

    def test_listening_only_preflight_disables_voice_when_microphone_probe_fails(self):
        settings = Settings(LIVE_LISTENING_ENABLED=True, LIVE_SPEECH_ENABLED=False)
        manager = PreflightManager(settings)
        result = _run(_check_wh_with(
            manager,
            resolve="/opt/wh",
            running=True,
            asr_ready=True,
            mic_ready=False,
        ))

        assert result.wh_available is False
        assert result.speech_available is False
        assert result.listening_available is False
        assert result.wh_bin is None


class TestModelPull:
    def test_pull_model_times_out_and_kills_process(self):
        manager = PreflightManager(Settings())
        proc = _HangingProcess()

        with patch("runtime.preflight.shutil.which", return_value="/usr/bin/ollama"):
            with patch("runtime.preflight.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
                with patch("runtime.preflight._MODEL_PULL_TIMEOUT", 0.01):
                    assert _run(manager._pull_model("missing:model")) is False

        assert proc.killed is True


class TestLocalWhisperMicrophoneProbe:
    def test_quiet_room_no_speech_is_not_a_microphone_failure(self):
        manager = PreflightManager(Settings())

        completed = subprocess.CompletedProcess(
            args=["wh", "listen", "1", "--raw"],
            returncode=1,
            stdout="",
            stderr="Recording... (Ctrl+C to stop)\nNo speech detected\n",
        )
        with patch("runtime.preflight.subprocess.run", return_value=completed):
            assert manager._probe_microphone_ready("/opt/wh") is True

    def test_microphone_error_is_a_microphone_failure(self):
        manager = PreflightManager(Settings())

        completed = subprocess.CompletedProcess(
            args=["wh", "listen", "1", "--raw"],
            returncode=1,
            stdout="",
            stderr="Recording... (Ctrl+C to stop)\nMicrophone error\n",
        )
        with patch("runtime.preflight.subprocess.run", return_value=completed):
            assert manager._probe_microphone_ready("/opt/wh") is False

    def test_no_audio_captured_is_a_microphone_failure(self):
        manager = PreflightManager(Settings())

        completed = subprocess.CompletedProcess(
            args=["wh", "listen", "1", "--raw"],
            returncode=1,
            stdout="",
            stderr="Recording... (Ctrl+C to stop)\nNo audio captured\n",
        )
        with patch("runtime.preflight.subprocess.run", return_value=completed):
            assert manager._probe_microphone_ready("/opt/wh") is False


async def _check_wh_with(
    manager: PreflightManager,
    resolve: str | None,
    running: bool,
    asr_ready: bool,
    mic_ready: bool = True,
):
    from runtime.models import PreflightResult

    result = PreflightResult()
    with patch("runtime.preflight.PreflightManager._resolve_wh", return_value=resolve):
        with patch("runtime.preflight.PreflightManager._wh_is_running", return_value=running):
            with patch("runtime.preflight.PreflightManager._start_wh_service", return_value=True):
                with patch("runtime.preflight.PreflightManager._wait_for_asr_ready", return_value=asr_ready):
                    with patch("runtime.preflight.PreflightManager._probe_microphone_ready", return_value=mic_ready):
                        await manager.check_local_whisper(result)
    return result
