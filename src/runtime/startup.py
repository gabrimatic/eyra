"""Provider + model selector shown when the backend is not configured or reachable."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from utils.theme import BOLD, DIM, GREEN, NC, RED

_SKIP = (
    "embed", "tts", "whisper", "dall-e", "moderation",
    "davinci", "babbage", "audio", "realtime", "transcri",
    "search", "similarity", "code-",
)

_ENV = Path(__file__).parent.parent.parent / ".env"
_LMS_BUNDLED = "/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(msg: str):
    print(f"  {GREEN}✓{NC} {msg}")


def _info(msg: str):
    print(f"  {DIM}›{NC} {msg}")


def _fail(msg: str):
    print(f"  {RED}✗{NC} {msg}")


def _is_chat_model(model_id: str) -> bool:
    low = model_id.lower()
    return not any(s in low for s in _SKIP)


def _is_reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as c:
            return c.get(url).status_code == 200
    except Exception:
        return False


def _wait_for(url: str, max_seconds: int = 15, msg: str = "Waiting") -> bool:
    print(f"  {DIM}›{NC} {msg} ", end="", flush=True)
    for _ in range(max_seconds):
        try:
            with httpx.Client(timeout=2) as c:
                if c.get(url).status_code == 200:
                    print(f"{GREEN}ok{NC}")
                    return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(1)
    print(f" {RED}timeout{NC}")
    return False


def _fetch_chat_models(base_url: str, api_key: str = "") -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key and api_key not in ("", "none") else {}
    try:
        with httpx.Client(timeout=10) as c:
            resp = c.get(f"{base_url}/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                return sorted(m["id"] for m in data if _is_chat_model(m["id"]))
    except Exception:
        pass
    return []


def _pick(options: list[str], allow_manual: bool = False) -> str:
    for i, opt in enumerate(options, 1):
        print(f"  {BOLD}{i:2}{NC}  {opt}")
    extra = len(options) + 1
    if allow_manual:
        print(f"  {BOLD}{extra:2}{NC}  {DIM}Enter manually{NC}")
    print()
    max_n = len(options) + (1 if allow_manual else 0)
    while True:
        try:
            raw = input(f"  Choice [1-{max_n}]: ").strip() or "1"
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1]
            if allow_manual and n == extra:
                val = input("  Enter value: ").strip()
                if val:
                    return val
        except (ValueError, EOFError):
            pass
        except KeyboardInterrupt:
            print()
            sys.exit(0)


def _provider_label(url: str) -> str:
    if "11434" in url:
        return "Ollama"
    if "1234" in url:
        return "LM Studio"
    if "openrouter" in url:
        return "OpenRouter"
    if "groq.com" in url:
        return "Groq"
    if "openai.com" in url:
        return "OpenAI"
    return "Custom"


def _write_env(base_url: str, api_key: str, model: str):
    """Write provider config to .env, preserving unrelated lines. Updates os.environ immediately."""
    provider_keys = {
        "API_BASE_URL", "API_KEY", "USE_MOCK_CLIENT", "MODEL",
        "LIVE_LISTENING_ENABLED", "LIVE_SPEECH_ENABLED", "SPEECH_COOLDOWN_MS",
    }
    extra: list[str] = []
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            key = line.split("=", 1)[0] if "=" in line else ""
            if line.startswith("#"):
                # Preserve user comments that aren't our own managed comments
                if line.strip() not in ("# Voice and speech", "# Custom"):
                    extra.append(line)
            elif key and key not in provider_keys and line.strip():
                extra.append(line)

    content_lines = [
        f"API_BASE_URL={base_url}",
        f"API_KEY={api_key}",
        "",
        "USE_MOCK_CLIENT=false",
        "",
        f"MODEL={model}",
        "",
        "# Voice and speech",
        "LIVE_LISTENING_ENABLED=true",
        "LIVE_SPEECH_ENABLED=true",
        "SPEECH_COOLDOWN_MS=3000",
    ]
    if extra:
        content_lines += ["", "# Custom"] + extra

    _ENV.write_text("\n".join(content_lines) + "\n")
    _ENV.chmod(0o600)

    # Reflect changes in the live environment immediately (load_dotenv already ran)
    os.environ["API_BASE_URL"] = base_url
    os.environ["API_KEY"] = api_key
    os.environ["MODEL"] = model


# ── Provider setup ────────────────────────────────────────────────────────────

def _setup_ollama() -> tuple[str, str, str]:
    base = "http://localhost:11434/v1"
    api_key = "ollama"

    if not shutil.which("ollama"):
        raise RuntimeError("Ollama not found. Install from https://ollama.com")

    if not _is_reachable("http://localhost:11434/api/tags"):
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_for("http://localhost:11434/api/tags", 10, "Starting Ollama"):
            raise RuntimeError("Ollama failed to start. Run: ollama serve")
    _ok("Ollama running")

    result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    models = [line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()]

    if not models:
        model = input("  No local models. Pull which model? (e.g. qwen3.5:4b): ").strip()
        if not model:
            raise RuntimeError("Model name required.")
        print(f"  {DIM}› Pulling {model}... (this may take a while){NC}")
        subprocess.run(["ollama", "pull", model], check=True, timeout=600)
        _ok(f"Model: {model}")
        return base, api_key, model

    if len(models) == 1:
        _ok(f"Model: {models[0]}")
        return base, api_key, models[0]

    print(f"\n  {BOLD}Local models:{NC}\n")
    pull_opt = "Pull a new model"
    choice = _pick(models + [pull_opt])
    if choice == pull_opt:
        model = input("  Model name (e.g. qwen3.5:4b): ").strip()
        if not model:
            raise RuntimeError("Model name required.")
        print(f"  {DIM}› Pulling {model}... (this may take a while){NC}")
        subprocess.run(["ollama", "pull", model], check=True, timeout=600)
    else:
        model = choice
    _ok(f"Model: {model}")
    return base, api_key, model


def _find_lms() -> str | None:
    if shutil.which("lms"):
        return "lms"
    if Path(_LMS_BUNDLED).is_file():
        return _LMS_BUNDLED
    return None


def _setup_lmstudio() -> tuple[str, str, str]:
    base = "http://localhost:1234/v1"
    api_key = "lm-studio"

    lms = _find_lms()
    if not lms:
        raise RuntimeError("LM Studio not found. Install from https://lmstudio.ai")

    if not _is_reachable(f"{base}/models"):
        subprocess.Popen(
            [lms, "server", "start", "--port", "1234"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_for(f"{base}/models", 15, "Starting LM Studio server"):
            raise RuntimeError("LM Studio server failed to start. Run: lms server start")
        time.sleep(2)  # let it fully initialize before listing models
    _ok("LM Studio running")

    models = _fetch_chat_models(base)
    if not models:
        raise RuntimeError("No models found. Download a model in LM Studio first.")

    if len(models) == 1:
        _ok(f"Model: {models[0]}")
        return base, api_key, models[0]

    print(f"\n  {BOLD}Available models:{NC}\n")
    model = _pick(models, allow_manual=True)
    _ok(f"Model: {model}")
    return base, api_key, model


def _setup_cloud(base_url: str, key_hint: str) -> tuple[str, str, str]:
    print(f"\n  {DIM}›{NC} Get your API key at: {key_hint}")
    api_key = input("  API key: ").strip()
    if not api_key:
        raise RuntimeError("API key required.")

    _info("Fetching models...")
    models = _fetch_chat_models(base_url, api_key)

    if not models:
        model = input("  Model name: ").strip()
        if not model:
            raise RuntimeError("Model name required.")
        return base_url, api_key, model

    print(f"\n  {BOLD}Available models:{NC}\n")
    model = _pick(models[:20], allow_manual=True)
    return base_url, api_key, model


# ── Entry point ───────────────────────────────────────────────────────────────

def maybe_run_startup_selector() -> bool:
    """Show provider + model picker if the backend is not configured or reachable.

    Returns True if a new configuration was written, False if backend was already reachable.
    """
    from utils.theme import CYAN

    # Read current config from .env (may not exist yet)
    env_url = env_model = ""
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            if line.startswith("API_BASE_URL="):
                env_url = line[len("API_BASE_URL="):].strip()
            elif line.startswith("MODEL="):
                env_model = line[len("MODEL="):].strip()

    # If backend is reachable, nothing to do
    if env_url:
        check = f"{env_url.rstrip('/').removesuffix('/v1')}/v1/models"
        if _is_reachable(check) or _is_reachable(f"{env_url.rstrip('/').removesuffix('/v1')}/api/tags"):
            return False

    # Detect installed/running providers
    ollama_tag = ""
    lms_tag = ""

    if shutil.which("ollama"):
        if _is_reachable("http://localhost:11434/api/tags"):
            ollama_tag = f"  {GREEN}running{NC}"
        else:
            ollama_tag = f"  {DIM}installed{NC}"

    lms = _find_lms()
    if lms:
        if _is_reachable("http://localhost:1234/v1/models"):
            lms_tag = f"  {GREEN}running{NC}"
        else:
            lms_tag = f"  {DIM}installed{NC}"

    print(f"\n{CYAN}▶{NC} Choose your AI provider\n")
    print(f"  {BOLD} 1{NC}  Ollama          {DIM}Local, free{NC}{ollama_tag}")
    print(f"  {BOLD} 2{NC}  LM Studio       {DIM}Local, free{NC}{lms_tag}")
    print(f"  {BOLD} 3{NC}  OpenRouter      {DIM}Cloud, free tier, 100+ models{NC}")
    print(f"  {BOLD} 4{NC}  Groq            {DIM}Cloud, free tier, fast inference{NC}")
    print(f"  {BOLD} 5{NC}  OpenAI          {DIM}Cloud, paid, reference quality{NC}")
    print(f"  {BOLD} 6{NC}  Custom          {DIM}Any OpenAI-compatible endpoint{NC}")

    if env_url and env_model:
        print(f"\n  {DIM}Current: {_provider_label(env_url)} · {env_model}{NC}")
    print()

    try:
        choice = input("  Provider [1-6]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

    print()

    try:
        if choice == "1":
            base, key, model = _setup_ollama()
        elif choice == "2":
            base, key, model = _setup_lmstudio()
        elif choice == "3":
            base, key, model = _setup_cloud(
                "https://openrouter.ai/api/v1", "https://openrouter.ai/keys"
            )
        elif choice == "4":
            base, key, model = _setup_cloud(
                "https://api.groq.com/openai/v1", "https://console.groq.com/keys"
            )
        elif choice == "5":
            base, key, model = _setup_cloud(
                "https://api.openai.com/v1", "https://platform.openai.com/api-keys"
            )
        elif choice == "6":
            print()
            base = input("  API base URL (e.g. http://localhost:8000/v1): ").strip()
            if not base:
                raise RuntimeError("URL required.")
            key = input("  API key (leave empty if not required): ").strip() or "none"
            print()
            models = _fetch_chat_models(base, key)
            if models:
                print(f"  {BOLD}Available models:{NC}\n")
                model = _pick(models, allow_manual=True)
            else:
                model = input("  Model name: ").strip()
                if not model:
                    raise RuntimeError("Model name required.")
        else:
            _fail("Invalid choice. Run eyra again to configure.")
            sys.exit(1)
    except RuntimeError as e:
        _fail(str(e))
        sys.exit(1)

    _write_env(base, key, model)
    print(f"  {DIM}Configuration saved.{NC}\n")
    return True
