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


class TestModelPull:
    def test_pull_model_times_out_and_kills_process(self):
        manager = PreflightManager(Settings())
        proc = _HangingProcess()

        with patch("runtime.preflight.shutil.which", return_value="/usr/bin/ollama"):
            with patch("runtime.preflight.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
                with patch("runtime.preflight._MODEL_PULL_TIMEOUT", 0.01):
                    assert _run(manager._pull_model("missing:model")) is False

        assert proc.killed is True
