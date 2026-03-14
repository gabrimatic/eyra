"""Preflight checks: backend, models, permissions, capabilities."""

import asyncio
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import wave

import httpx

from runtime.models import PreflightResult
from utils.settings import Settings
from utils.theme import CYAN, DIM, GREEN, NC, RED, YELLOW

logger = logging.getLogger(__name__)

WH_INSTALL_HINT = "brew tap gabrimatic/local-whisper && brew install local-whisper"


def _ok(msg: str):
    print(f"  {GREEN}✓{NC} {msg}")


def _warn(msg: str):
    print(f"  {YELLOW}⚠{NC} {msg}")


def _fail(msg: str):
    print(f"  {RED}✗{NC} {msg}")


class PreflightManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._is_ollama = False

    async def run(self) -> PreflightResult:
        result = PreflightResult()
        print(f"\n{CYAN}▶{NC} Preflight\n")

        # Backend
        result.backend_reachable = await self._check_backend()

        # Models
        if result.backend_reachable:
            await self._check_models(result)

        # Capabilities (sync/blocking — run off the event loop)
        result.wh_available = await asyncio.to_thread(self._check_wh, result)
        result.screen_capture_available = self._check_screen_capture()

        print()
        return result

    async def _check_backend(self) -> bool:
        base = self.settings.API_BASE_URL.rstrip("/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=5) as client:
            # Try OpenAI-compatible endpoint first (works with any provider)
            try:
                resp = await client.get(f"{base}/v1/models")
                if resp.status_code == 200:
                    _ok(f"Backend: {base}")
                    return True
            except Exception as e:
                logger.debug("OpenAI /v1/models check failed: %s", e)

            # Fall back to Ollama-specific endpoint
            try:
                resp = await client.get(f"{base}/api/tags")
                if resp.status_code == 200:
                    self._is_ollama = True
                    _ok(f"Backend: {base}")
                    return True
                _fail(f"Backend responded {resp.status_code}")
                return False
            except Exception as e:
                _fail(f"Backend unreachable: {base}")
                logger.debug("Backend check failed: %s", e)
                return False

    async def _check_models(self, result: PreflightResult):
        base = self.settings.API_BASE_URL.rstrip("/v1").rstrip("/")
        available: set[str] = set()

        async with httpx.AsyncClient(timeout=5) as client:
            # Try OpenAI-compatible /v1/models first
            try:
                resp = await client.get(f"{base}/v1/models")
                if resp.status_code == 200:
                    data = resp.json()
                    available = {m["id"] for m in data.get("data", [])}
            except Exception:
                pass

            # Fall back to Ollama /api/tags if no models found yet
            if not available:
                try:
                    resp = await client.get(f"{base}/api/tags")
                    if resp.status_code == 200:
                        available = {m["name"] for m in resp.json().get("models", [])}
                except Exception:
                    pass

        for model in self.settings.all_model_names:
            # Providers may store with or without :latest suffix
            found = model in available or f"{model}:latest" in available
            if found:
                result.models_ready.append(model)
                _ok(f"Model: {model}")
            elif self.settings.AUTO_PULL_MODELS and self._is_ollama:
                print(f"  {DIM}›{NC} Pulling {model}...", end="", flush=True)
                if await self._pull_model(model):
                    result.models_ready.append(model)
                    print(f"\r  {GREEN}✓{NC} Model: {model}    ")
                else:
                    result.models_missing.append(model)
                    print(f"\r  {RED}✗{NC} Model: {model} (pull failed)    ")
            else:
                result.models_missing.append(model)
                _fail(f"Model missing: {model}")

    async def _pull_model(self, model: str) -> bool:
        if shutil.which("ollama") is None:
            logger.debug("ollama command not found, cannot pull %s", model)
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama", "pull", model,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def unload_models(settings: Settings, model_names: list[str]):
        """Release models from memory (keep_alive=0). Fails silently for backends that don't support it."""
        base = settings.API_BASE_URL.rstrip("/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=5) as client:
            for model in model_names:
                try:
                    await client.post(f"{base}/api/generate", json={"model": model, "keep_alive": 0})
                    logger.info("Unloaded model: %s", model)
                except Exception as e:
                    logger.debug("Could not unload %s: %s", model, e)

    def _check_wh(self, result: PreflightResult) -> bool:
        """Check that Local Whisper is installed, running, and ASR is ready.

        Local Whisper powers both voice input (ASR) and speech output (TTS).
        Installed via Homebrew, resolved from PATH. After confirming the process
        is alive, probes ASR readiness (the model can take seconds to load).
        """
        wh_bin = self._resolve_wh()
        if wh_bin is None:
            _fail("Local Whisper: not installed (voice input + speech disabled)")
            print(f"    {DIM}Install: {WH_INSTALL_HINT}{NC}")
            return False

        result.wh_bin = wh_bin

        running = self._wh_is_running(wh_bin)
        if not running:
            if not self._start_wh_service(wh_bin):
                _warn("Local Whisper: installed but not running (voice input + speech disabled)")
                print(f"    {DIM}Start: wh start{NC}")
                result.wh_bin = None
                return False

        # Process is alive — now wait for ASR to be ready
        print(f"  {DIM}› Waiting for Local Whisper ASR...{NC}", end="", flush=True)
        if self._wait_for_asr_ready(wh_bin, max_wait=15):
            print(f"\r  {GREEN}✓{NC} Local Whisper: ready (voice input + speech)   ")
            return True

        print(f"\r  {YELLOW}⚠{NC} Local Whisper: running but ASR not ready (voice disabled)   ")
        print(f"    {DIM}ASR model may still be loading. Restart: wh restart{NC}")
        result.wh_bin = None
        return False

    @staticmethod
    def _wait_for_asr_ready(wh_bin: str, max_wait: int = 15) -> bool:
        """Probe Local Whisper until ASR responds, up to max_wait seconds.

        Uses a silent WAV file to test transcription end-to-end. A successful
        call (even with empty output) means the model is loaded and ready.
        """
        # Create a tiny silent WAV (100ms of silence at 16kHz)
        fd, probe_path = tempfile.mkstemp(suffix=".wav")
        try:
            os.close(fd)
            with wave.open(probe_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 1600)  # 100ms silence

            deadline = time.time() + max_wait
            while time.time() < deadline:
                try:
                    proc = subprocess.run(
                        [wh_bin, "transcribe", probe_path],
                        capture_output=True, timeout=10, text=True,
                    )
                    output = (proc.stdout or "") + (proc.stderr or "")
                    if "not ready" in output.lower():
                        time.sleep(1)
                        continue
                    # Any other result (including empty transcription) means ready
                    return True
                except subprocess.TimeoutExpired:
                    time.sleep(1)
                except Exception:
                    time.sleep(1)
            return False
        finally:
            try:
                pathlib.Path(probe_path).unlink()
            except Exception:
                pass

    @staticmethod
    def _resolve_wh() -> str | None:
        """Find the wh binary on PATH (installed via Homebrew)."""
        return shutil.which("wh")

    @staticmethod
    def _wh_is_running(wh_bin: str) -> bool:
        """Check whether the Local Whisper service reports itself as running."""
        try:
            proc = subprocess.run(
                [wh_bin, "status"], capture_output=True, timeout=3, text=True,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            return "running" in output.lower()
        except Exception:
            return False

    @staticmethod
    def _start_wh_service(wh_bin: str) -> bool:
        """Start Local Whisper via brew services, falling back to wh start."""
        if shutil.which("brew"):
            try:
                subprocess.run(
                    ["brew", "services", "start", "local-whisper"],
                    capture_output=True, timeout=10,
                )
                time.sleep(1)
                check = subprocess.run(
                    [wh_bin, "status"], capture_output=True, timeout=3, text=True,
                )
                output = (check.stdout or "") + (check.stderr or "")
                if "running" in output.lower():
                    return True
            except Exception:
                pass

        try:
            proc = subprocess.run(
                [wh_bin, "start"], capture_output=True, timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _check_screen_capture(self) -> bool:
        available = shutil.which("screencapture") is not None
        if available:
            _ok("Screen capture: available")
        else:
            _warn("Screen capture: not available")
        return available

