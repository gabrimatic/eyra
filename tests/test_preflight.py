"""Tests for preflight backend detection and model recovery."""

import asyncio
import os
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
            return _Response(200, {"data": [{"id": "gemma3:4b"}]})
        if url.endswith("/api/tags"):
            return _Response(200, {"models": [{"name": "gemma3:4b"}]})
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

        assert preflight.models_ready == ["gemma3:4b"]
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


class TestModelPull:
    def test_pull_model_times_out_and_kills_process(self):
        manager = PreflightManager(Settings())
        proc = _HangingProcess()

        with patch("runtime.preflight.shutil.which", return_value="/usr/bin/ollama"):
            with patch("runtime.preflight.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
                with patch("runtime.preflight._MODEL_PULL_TIMEOUT", 0.01):
                    assert _run(manager._pull_model("missing:model")) is False

        assert proc.killed is True
