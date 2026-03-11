"""Preflight checks: backend, models, permissions, capabilities."""

import asyncio
import logging
import shutil
import subprocess

import httpx

from runtime.models import PreflightResult
from utils.settings import Settings
from utils.theme import CYAN, DIM, GREEN, NC, RED, YELLOW

logger = logging.getLogger(__name__)


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

        # Capabilities
        result.wh_available = self._check_wh()
        result.screen_capture_available = self._check_screen_capture()
        if result.wh_available:
            result.microphone_available = self._check_microphone()

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

    def _check_wh(self) -> bool:
        """Check that Local Whisper is installed and the service is running."""
        if shutil.which("wh") is None:
            _warn("Voice: wh not found (install local-whisper for voice)")
            return False
        try:
            proc = subprocess.run(["wh", "status"], capture_output=True, timeout=3)
            if proc.returncode == 0:
                _ok("Voice: Local Whisper running")
                return True
            else:
                _warn("Voice: Local Whisper not running (start with: wh start)")
                return False
        except Exception:
            _warn("Voice: could not check Local Whisper status")
            return False

    def _check_screen_capture(self) -> bool:
        available = shutil.which("screencapture") is not None
        if available:
            _ok("Screen capture: available")
        else:
            _warn("Screen capture: not available")
        return available

    def _check_microphone(self) -> bool:
        """Mic permission belongs to Local Whisper, not the terminal.
        If wh is installed and running, mic is available through it."""
        # This is called only when wh_available is True (service confirmed running).
        # The service handles its own mic permission via macOS.
        _ok("Microphone: available (via Local Whisper)")
        return True
