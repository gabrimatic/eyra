"""Preflight checks: backend, models, permissions, capabilities."""

import asyncio
import logging
import os
import pathlib
import plistlib
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
_MODEL_PULL_TIMEOUT = 600


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

        if self.settings.USE_MOCK_CLIENT:
            result.backend_reachable = True
            result.models_ready = list(self.settings.all_model_names)
            _ok("Backend: mock client")
            for model in result.models_ready:
                _ok(f"Model: {model}")
        else:
            # Backend
            result.backend_reachable = await self._check_backend()

            # Models
            if result.backend_reachable:
                await self._check_models(result)

        # Capabilities (sync/blocking — run off the event loop)
        if self.settings.LIVE_LISTENING_ENABLED or self.settings.LIVE_SPEECH_ENABLED:
            result.wh_available = await self.check_local_whisper(result)
        else:
            _ok("Local Whisper: skipped (voice disabled)")
        result.screen_capture_available = self._check_screen_capture()

        print()
        return result

    async def check_local_whisper(
        self,
        result: PreflightResult | None = None,
        listening_requested: bool | None = None,
        speech_requested: bool | None = None,
    ) -> bool:
        """Check Local Whisper on demand and thread the resolved wh path into result."""
        target = result or PreflightResult()
        target.wh_available = await asyncio.to_thread(
            self._check_wh,
            target,
            self.settings.LIVE_LISTENING_ENABLED if listening_requested is None else listening_requested,
            self.settings.LIVE_SPEECH_ENABLED if speech_requested is None else speech_requested,
        )
        return target.wh_available

    async def _check_backend(self) -> bool:
        base = self.settings.API_BASE_URL.removesuffix("/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=5) as client:
            # Try OpenAI-compatible endpoint first (works with any provider)
            try:
                resp = await client.get(f"{base}/v1/models")
                if resp.status_code == 200:
                    self._is_ollama = await self._ollama_tags_available(client, base)
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

    @staticmethod
    async def _ollama_tags_available(client: httpx.AsyncClient, base: str) -> bool:
        """Detect Ollama even when its OpenAI-compatible /v1/models endpoint works."""
        try:
            resp = await client.get(f"{base}/api/tags")
            return resp.status_code == 200
        except Exception as e:
            logger.debug("Ollama /api/tags probe failed: %s", e)
            return False

    async def _check_models(self, result: PreflightResult):
        base = self.settings.API_BASE_URL.removesuffix("/v1").rstrip("/")
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
                await self._inspect_model_tool_capability(result, model)
            elif self.settings.AUTO_PULL_MODELS and self._is_ollama:
                print(f"  {DIM}›{NC} Pulling {model}...", end="", flush=True)
                if await self._pull_model(model):
                    result.models_ready.append(model)
                    print(f"\r  {GREEN}✓{NC} Model: {model}    ")
                    await self._inspect_model_tool_capability(result, model)
                else:
                    result.models_missing.append(model)
                    print(f"\r  {RED}✗{NC} Model: {model} (pull failed)    ")
            else:
                result.models_missing.append(model)
                _fail(f"Model missing: {model}")

    async def _inspect_model_tool_capability(self, result: PreflightResult, model: str) -> None:
        """Record whether a configured model can accept native tools when known."""
        if not self._is_ollama:
            result.tool_capable_models.append(model)
            result.vision_capable_models.append(model)
            return
        base = self.settings.API_BASE_URL.removesuffix("/v1").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(f"{base}/api/show", json={"model": model})
            if resp.status_code != 200:
                return
            capabilities = set(resp.json().get("capabilities") or [])
        except Exception as e:
            logger.debug("Could not inspect Ollama model capabilities for %s: %s", model, e)
            return
        result.tool_capability_checked_models.append(model)
        result.vision_capability_checked_models.append(model)
        if capabilities and "tools" not in capabilities:
            _warn(f"Model lacks native tool calling: {model}")
        else:
            result.tool_capable_models.append(model)
        if not capabilities or "vision" in capabilities:
            result.vision_capable_models.append(model)

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
            try:
                await asyncio.wait_for(proc.wait(), timeout=_MODEL_PULL_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.debug("ollama pull timed out after %ss: %s", _MODEL_PULL_TIMEOUT, model)
                return False
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def unload_models(settings: Settings, model_names: list[str]):
        """Release models from memory (keep_alive=0). Fails silently for backends that don't support it."""
        base = settings.API_BASE_URL.removesuffix("/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=5) as client:
            for model in model_names:
                try:
                    await client.post(f"{base}/api/generate", json={"model": model, "keep_alive": 0})
                    logger.info("Unloaded model: %s", model)
                except Exception as e:
                    logger.debug("Could not unload %s: %s", model, e)

    def _check_wh(self, result: PreflightResult, listening_requested: bool, speech_requested: bool) -> bool:
        """Check that Local Whisper is installed and the requested voice features are ready.

        Local Whisper powers both voice input (ASR) and speech output (TTS).
        Installed via Homebrew, resolved from PATH. Speech only needs the service
        running; voice input additionally probes ASR readiness because that model
        can take seconds to load.
        """
        wh_bin = self._resolve_wh()
        if wh_bin is None:
            _fail("Local Whisper: not installed (voice input + speech disabled)")
            print(f"    {DIM}Install: {WH_INSTALL_HINT}{NC}")
            result.listening_available = False
            result.speech_available = False
            return False

        result.wh_bin = wh_bin
        result.listening_available = False
        result.speech_available = False

        running = self._wh_is_running(wh_bin)
        if not running:
            if not self._start_wh_service(wh_bin):
                _warn("Local Whisper: installed but not running (voice input + speech disabled)")
                print(f"    {DIM}Start: wh start{NC}")
                result.wh_bin = None
                result.listening_available = False
                result.speech_available = False
                return False

        if speech_requested:
            result.speech_available = True

        if listening_requested:
            # Process is alive — now wait for ASR to be ready
            print(f"  {DIM}› Waiting for Local Whisper ASR...{NC}", end="", flush=True)
            if self._wait_for_asr_ready(wh_bin, max_wait=15):
                if self._probe_microphone_ready(wh_bin):
                    result.listening_available = True
                    if speech_requested:
                        print(f"\r  {GREEN}✓{NC} Local Whisper: ready (voice input + speech)   ")
                    else:
                        print(f"\r  {GREEN}✓{NC} Local Whisper: ready (voice input)   ")
                else:
                    print(f"\r  {YELLOW}⚠{NC} Local Whisper: microphone input failed   ")
                    print(f"    {DIM}Check microphone permission/input device, then run /voice on again.{NC}")
            else:
                print(f"\r  {YELLOW}⚠{NC} Local Whisper: running but ASR not ready   ")
                print(f"    {DIM}ASR model may still be loading. Restart: wh restart{NC}")

        if result.speech_available and not result.listening_available:
            _ok("Local Whisper: ready (speech)")

        available = bool(result.listening_available or result.speech_available)
        if not available:
            result.wh_bin = None
        return available

    @staticmethod
    def _probe_microphone_ready(wh_bin: str) -> bool:
        """Run a bounded Local Whisper listen probe so voice input readiness is real.

        ASR readiness alone only proves file transcription works. Hands-free Eyra
        also needs the microphone path. A one-second listen may return no text on
        a healthy quiet room, but it must not return Local Whisper's microphone
        failure.
        """
        try:
            proc = subprocess.run(
                [wh_bin, "listen", "1", "--raw"],
                capture_output=True,
                timeout=5,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
        hard_failures = (
            "microphone error",
            "service is busy",
            "service not ready",
            "cannot connect",
            "connection closed",
            "no audio captured",
            "recording ended unexpectedly",
        )
        if any(marker in output for marker in hard_failures):
            return False
        # "No speech detected" is a healthy quiet-room probe: the microphone
        # opened, recorded, and reached the ASR pipeline.
        return True

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
        """Find the wh binary from PATH, LaunchAgent plists, or common Homebrew paths."""
        path_wh = shutil.which("wh")
        if path_wh:
            return path_wh

        launch_agent_dir = pathlib.Path.home() / "Library" / "LaunchAgents"
        for plist_path in launch_agent_dir.glob("*.plist"):
            try:
                data = plistlib.loads(plist_path.read_bytes())
            except Exception:
                continue
            args = data.get("ProgramArguments")
            candidates: list[str] = []
            if isinstance(args, list):
                candidates.extend(str(arg) for arg in args)
            program = data.get("Program")
            if isinstance(program, str):
                candidates.append(program)
            for candidate in candidates:
                path = pathlib.Path(candidate).expanduser()
                if path.name == "wh" and path.is_file():
                    return str(path)

        for candidate in (
            "/opt/homebrew/bin/wh",
            "/usr/local/bin/wh",
            str(pathlib.Path.home() / ".local" / "bin" / "wh"),
        ):
            if pathlib.Path(candidate).is_file():
                return candidate
        return None

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
