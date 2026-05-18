"""Provider + model selector shown when the backend is not configured or reachable."""

import getpass
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
_RECOMMENDED_LOCAL_MODEL = os.getenv("EYRA_RECOMMENDED_MODEL", "gemma4:e4b")


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


def _ask_yes_no(prompt: str, *, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"  {prompt} [{suffix}]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        _fail("Please type yes or no.")


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
        "API_BASE_URL", "API_KEY", "USE_MOCK_CLIENT", "MODEL", "VISION_MODEL", "SIMPLE_MODEL", "MODERATE_MODEL",
        "AUTO_PULL_MODELS", "LIVE_LISTENING_ENABLED", "LIVE_SPEECH_ENABLED", "SPEECH_COOLDOWN_MS",
        "VOICE_INPUT_DEVICE", "VOICE_SAMPLE_RATE", "VOICE_DEBUG_RECORD_SECONDS", "VOICE_DIAGNOSTIC_SAVE_AUDIO",
        "VOICE_SILENCE_MS", "VOICE_VAD_THRESHOLD", "VOICE_MAX_DURATION_SECONDS", "HANDS_FREE_MODE",
        "NETWORK_TOOLS_ENABLED", "OS_TOOLS_ENABLED", "SCREEN_OCR_COMMAND",
        "AGENT_TOOLS_ENABLED", "EXTERNAL_AGENT_TOOLS_ENABLED", "EXTERNAL_AGENT_CONFIG_PATH",
        "CONNECTORS_ENABLED", "CONNECTORS_CONFIG_PATH", "CONNECTORS_ALLOWED_ROOTS", "CONNECTORS_TIMEOUT_SECONDS",
        "CONNECTORS_OUTPUT_CAP_BYTES", "CONNECTORS_ALLOW_REMOTE", "CONNECTORS_ALLOW_PYTHON_MODULE",
        "MCP_TOOLS_ENABLED", "MCP_CONFIG_PATH", "MEMORY_ENABLED", "MEMORY_PROVIDER", "MEMORY_AUTO_SAVE_ENABLED",
        "MEMORY_PATH", "MEMORY_MCP_COMMAND", "MEMORY_MCP_ARGS", "MEMORY_CONTEXT_MAX_CHARS",
        "MEMORY_FACT_MAX_CHARS", "MEMORY_SECTION_MAX_FACTS", "MEMORY_WRITE_REQUIRE_CONFIRMATION",
        "MEMORY_DEBUG", "AGENTS_FILE", "AGENTS_MAX_CHARS", "PERSONALITY_FILE", "PERSONALITY_MAX_CHARS",
        "WEB_UI_ENABLED", "WEB_UI_HOST",
        "WEB_UI_PORT", "WEB_UI_TOKEN", "WEB_UI_REQUIRE_TOKEN", "WEB_UI_MAX_REQUEST_BYTES",
        "REALTIME_VOICE_ENABLED", "REALTIME_MODEL", "REALTIME_VOICE", "OPENAI_API_KEY",
        "REALTIME_TOOLS_ENABLED", "REALTIME_ALLOWED_TOOLS",
        "BACKGROUND_TASKS_ENABLED", "MAX_BACKGROUND_TASKS", "WORKER_MODEL", "TASK_TIMEOUT_SECONDS",
        "MAX_WORKER_TOOL_STEPS", "TOOL_TIMEOUT_SECONDS", "MODEL_CONCURRENCY", "TASK_STATUS_UPDATES",
        "JOB_STORE_PATH", "TRIGGER_STORE_PATH", "TRIGGER_CHECK_INTERVAL_SECONDS", "TRIGGER_TIMEOUT_SECONDS",
        "FILESYSTEM_ALLOWED_PATHS", "FILESYSTEM_DEFAULT_PATH", "COMPLEXITY_ROUTING_ENABLED", "ROUTING_DEBUG",
    }
    extra: list[str] = []
    existing: dict[str, str] = {}
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            key = line.split("=", 1)[0] if "=" in line else ""
            if key:
                existing[key] = line.split("=", 1)[1]
            if line.startswith("#"):
                # Preserve user comments that aren't our own managed comments
                if line.strip() not in (
                    "# Voice and speech",
                    "# Optional Web UI",
                    "# Compact local memory",
                    "# Optional Realtime voice",
                    "# Background tasks",
                    "# Optional OS, agent, MCP, and network tools",
                    "# Optional connectors",
                    "# Optional MCP bridge",
                    "# Filesystem sandbox",
                    "# Optional network tools",
                    "# Routing",
                    "# Custom",
                ):
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
        f"VISION_MODEL={existing.get('VISION_MODEL', '')}",
        "",
        "# Tier models, only used when COMPLEXITY_ROUTING_ENABLED=true",
        f"SIMPLE_MODEL={existing.get('SIMPLE_MODEL', 'qwen3.5:2b')}",
        f"MODERATE_MODEL={existing.get('MODERATE_MODEL', model)}",
        "",
        f"AUTO_PULL_MODELS={existing.get('AUTO_PULL_MODELS', 'true')}",
        "",
        "# Voice and speech",
        f"LIVE_LISTENING_ENABLED={existing.get('LIVE_LISTENING_ENABLED', 'true')}",
        f"LIVE_SPEECH_ENABLED={existing.get('LIVE_SPEECH_ENABLED', 'true')}",
        f"SPEECH_COOLDOWN_MS={existing.get('SPEECH_COOLDOWN_MS', '3000')}",
        f"VOICE_INPUT_DEVICE={existing.get('VOICE_INPUT_DEVICE', '')}",
        f"VOICE_SAMPLE_RATE={existing.get('VOICE_SAMPLE_RATE', '16000')}",
        f"VOICE_DEBUG_RECORD_SECONDS={existing.get('VOICE_DEBUG_RECORD_SECONDS', '3')}",
        f"VOICE_DIAGNOSTIC_SAVE_AUDIO={existing.get('VOICE_DIAGNOSTIC_SAVE_AUDIO', 'false')}",
        f"VOICE_SILENCE_MS={existing.get('VOICE_SILENCE_MS', '1500')}",
        f"VOICE_VAD_THRESHOLD={existing.get('VOICE_VAD_THRESHOLD', '0.15')}",
        f"VOICE_MAX_DURATION_SECONDS={existing.get('VOICE_MAX_DURATION_SECONDS', '300')}",
        f"HANDS_FREE_MODE={existing.get('HANDS_FREE_MODE', 'false')}",
        "",
        "# Background tasks",
        f"BACKGROUND_TASKS_ENABLED={existing.get('BACKGROUND_TASKS_ENABLED', 'true')}",
        f"MAX_BACKGROUND_TASKS={existing.get('MAX_BACKGROUND_TASKS', '2')}",
        f"WORKER_MODEL={existing.get('WORKER_MODEL', '')}",
        f"TASK_TIMEOUT_SECONDS={existing.get('TASK_TIMEOUT_SECONDS', '300')}",
        f"MAX_WORKER_TOOL_STEPS={existing.get('MAX_WORKER_TOOL_STEPS', '8')}",
        f"TOOL_TIMEOUT_SECONDS={existing.get('TOOL_TIMEOUT_SECONDS', '30')}",
        f"MODEL_CONCURRENCY={existing.get('MODEL_CONCURRENCY', '1')}",
        f"TASK_STATUS_UPDATES={existing.get('TASK_STATUS_UPDATES', 'true')}",
        f"JOB_STORE_PATH={existing.get('JOB_STORE_PATH', '~/.local/share/eyra/jobs.sqlite3')}",
        f"TRIGGER_STORE_PATH={existing.get('TRIGGER_STORE_PATH', '~/.local/share/eyra/triggers.sqlite3')}",
        f"TRIGGER_CHECK_INTERVAL_SECONDS={existing.get('TRIGGER_CHECK_INTERVAL_SECONDS', '0.5')}",
        f"TRIGGER_TIMEOUT_SECONDS={existing.get('TRIGGER_TIMEOUT_SECONDS', '300')}",
        "",
        "# Optional Web UI",
        f"WEB_UI_ENABLED={existing.get('WEB_UI_ENABLED', 'false')}",
        f"WEB_UI_HOST={existing.get('WEB_UI_HOST', '127.0.0.1')}",
        f"WEB_UI_PORT={existing.get('WEB_UI_PORT', '8765')}",
        f"WEB_UI_TOKEN={existing.get('WEB_UI_TOKEN', '')}",
        f"WEB_UI_REQUIRE_TOKEN={existing.get('WEB_UI_REQUIRE_TOKEN', 'auto')}",
        f"WEB_UI_MAX_REQUEST_BYTES={existing.get('WEB_UI_MAX_REQUEST_BYTES', '1000000')}",
        "",
        "# Optional Realtime voice",
        f"REALTIME_VOICE_ENABLED={existing.get('REALTIME_VOICE_ENABLED', 'false')}",
        f"REALTIME_MODEL={existing.get('REALTIME_MODEL', 'gpt-realtime')}",
        f"REALTIME_VOICE={existing.get('REALTIME_VOICE', 'marin')}",
        f"OPENAI_API_KEY={existing.get('OPENAI_API_KEY', '')}",
        f"REALTIME_TOOLS_ENABLED={existing.get('REALTIME_TOOLS_ENABLED', 'false')}",
        f"REALTIME_ALLOWED_TOOLS={existing.get('REALTIME_ALLOWED_TOOLS', '')}",
        "",
        "# Optional OS, agent, MCP, and network tools",
        f"NETWORK_TOOLS_ENABLED={existing.get('NETWORK_TOOLS_ENABLED', 'false')}",
        f"OS_TOOLS_ENABLED={existing.get('OS_TOOLS_ENABLED', 'false')}",
        f"SCREEN_OCR_COMMAND={existing.get('SCREEN_OCR_COMMAND', '')}",
        f"AGENT_TOOLS_ENABLED={existing.get('AGENT_TOOLS_ENABLED', 'false')}",
        f"EXTERNAL_AGENT_TOOLS_ENABLED={existing.get('EXTERNAL_AGENT_TOOLS_ENABLED', existing.get('AGENT_TOOLS_ENABLED', 'false'))}",
        f"EXTERNAL_AGENT_CONFIG_PATH={existing.get('EXTERNAL_AGENT_CONFIG_PATH', '~/.config/eyra/agents.json')}",
        "",
        "# Optional connectors",
        f"CONNECTORS_ENABLED={existing.get('CONNECTORS_ENABLED', 'false')}",
        f"CONNECTORS_CONFIG_PATH={existing.get('CONNECTORS_CONFIG_PATH', '~/.config/eyra/connectors.json')}",
        f"CONNECTORS_ALLOWED_ROOTS={existing.get('CONNECTORS_ALLOWED_ROOTS', '')}",
        f"CONNECTORS_TIMEOUT_SECONDS={existing.get('CONNECTORS_TIMEOUT_SECONDS', '600')}",
        f"CONNECTORS_OUTPUT_CAP_BYTES={existing.get('CONNECTORS_OUTPUT_CAP_BYTES', '32768')}",
        f"CONNECTORS_ALLOW_REMOTE={existing.get('CONNECTORS_ALLOW_REMOTE', 'false')}",
        f"CONNECTORS_ALLOW_PYTHON_MODULE={existing.get('CONNECTORS_ALLOW_PYTHON_MODULE', 'false')}",
        "",
        "# Optional MCP bridge",
        f"MCP_TOOLS_ENABLED={existing.get('MCP_TOOLS_ENABLED', 'false')}",
        f"MCP_CONFIG_PATH={existing.get('MCP_CONFIG_PATH', '~/.config/eyra/mcp.json')}",
        "",
        "# Compact local memory",
        f"MEMORY_ENABLED={existing.get('MEMORY_ENABLED', 'true')}",
        f"MEMORY_PROVIDER={existing.get('MEMORY_PROVIDER', 'mcp-prose-memory')}",
        f"MEMORY_AUTO_SAVE_ENABLED={existing.get('MEMORY_AUTO_SAVE_ENABLED', 'true')}",
        f"MEMORY_PATH={existing.get('MEMORY_PATH', '~/.mcp-prose-memory/memory.json')}",
        f"MEMORY_MCP_COMMAND={existing.get('MEMORY_MCP_COMMAND', 'mcp-prose-memory')}",
        f"MEMORY_MCP_ARGS={existing.get('MEMORY_MCP_ARGS', '')}",
        f"MEMORY_CONTEXT_MAX_CHARS={existing.get('MEMORY_CONTEXT_MAX_CHARS', '1500')}",
        f"MEMORY_FACT_MAX_CHARS={existing.get('MEMORY_FACT_MAX_CHARS', '220')}",
        f"MEMORY_SECTION_MAX_FACTS={existing.get('MEMORY_SECTION_MAX_FACTS', '30')}",
        f"MEMORY_WRITE_REQUIRE_CONFIRMATION={existing.get('MEMORY_WRITE_REQUIRE_CONFIRMATION', 'false')}",
        f"MEMORY_DEBUG={existing.get('MEMORY_DEBUG', 'false')}",
        f"AGENTS_FILE={existing.get('AGENTS_FILE', '~/.config/eyra/AGENTS.md')}",
        f"AGENTS_MAX_CHARS={existing.get('AGENTS_MAX_CHARS', '1200')}",
        f"PERSONALITY_FILE={existing.get('PERSONALITY_FILE', '~/.config/eyra/personality.md')}",
        f"PERSONALITY_MAX_CHARS={existing.get('PERSONALITY_MAX_CHARS', '800')}",
        "",
        "# Filesystem sandbox",
        f"FILESYSTEM_ALLOWED_PATHS={existing.get('FILESYSTEM_ALLOWED_PATHS', '~/Documents,~/Desktop,~/Downloads,/tmp')}",
        f"FILESYSTEM_DEFAULT_PATH={existing.get('FILESYSTEM_DEFAULT_PATH', '~/Documents')}",
        "",
        "# Routing",
        f"COMPLEXITY_ROUTING_ENABLED={existing.get('COMPLEXITY_ROUTING_ENABLED', 'false')}",
        f"ROUTING_DEBUG={existing.get('ROUTING_DEBUG', 'false')}",
    ]
    if extra:
        content_lines += ["", "# Custom"] + extra

    _ENV.write_text("\n".join(content_lines) + "\n")
    _ENV.chmod(0o600)

    # Reflect changes in the live environment immediately (load_dotenv already ran)
    os.environ["API_BASE_URL"] = base_url
    os.environ["API_KEY"] = api_key
    os.environ["MODEL"] = model
    os.environ["USE_MOCK_CLIENT"] = "false"


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
        print()
        _info(f"No local model is installed yet. Eyra can download {_RECOMMENDED_LOCAL_MODEL} for you.")
        if _ask_yes_no(f"Download {_RECOMMENDED_LOCAL_MODEL} now?", default=True):
            model = _RECOMMENDED_LOCAL_MODEL
        else:
            model = input("  Model name to download: ").strip()
            if not model:
                raise RuntimeError("Model name required.")
        print(f"  {DIM}› Pulling {model}... (this may take a while){NC}")
        subprocess.run(["ollama", "pull", model], check=True, timeout=600)
        _ok(f"Model: {model}")
        return base, api_key, model

    if len(models) == 1:
        _ok(f"Model: {models[0]}")
        return base, api_key, models[0]

    print(f"\n  {BOLD}Choose the local model Eyra should use:{NC}\n")
    options = models.copy()
    recommended_opt = ""
    if _RECOMMENDED_LOCAL_MODEL not in models:
        recommended_opt = f"Download recommended model ({_RECOMMENDED_LOCAL_MODEL})"
        options.insert(0, recommended_opt)
    pull_opt = "Download a different model"
    choice = _pick(options + [pull_opt])
    if choice == recommended_opt:
        model = _RECOMMENDED_LOCAL_MODEL
        print(f"  {DIM}› Pulling {model}... (this may take a while){NC}")
        subprocess.run(["ollama", "pull", model], check=True, timeout=600)
    elif choice == pull_opt:
        model = input("  Model name: ").strip()
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
    api_key = getpass.getpass("  API key (hidden while you type): ").strip()
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

    # Read current config from .env (may not exist yet), then let live env vars override it.
    env_url = env_model = ""
    use_mock = False
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            if line.startswith("API_BASE_URL="):
                env_url = line[len("API_BASE_URL="):].strip()
            elif line.startswith("MODEL="):
                env_model = line[len("MODEL="):].strip()
            elif line.startswith("USE_MOCK_CLIENT="):
                use_mock = line[len("USE_MOCK_CLIENT="):].strip().lower() in {"true", "1", "yes", "on"}

    if os.getenv("API_BASE_URL"):
        env_url = os.environ["API_BASE_URL"].strip()
    if os.getenv("MODEL"):
        env_model = os.environ["MODEL"].strip()
    if os.getenv("USE_MOCK_CLIENT"):
        use_mock = os.environ["USE_MOCK_CLIENT"].strip().lower() in {"true", "1", "yes", "on"}

    if use_mock:
        _info("Mock client enabled; skipping provider setup.")
        return False

    # If backend is reachable, nothing to do
    if env_url:
        check = f"{env_url.rstrip('/').removesuffix('/v1')}/v1/models"
        if _is_reachable(check) or _is_reachable(f"{env_url.rstrip('/').removesuffix('/v1')}/api/tags"):
            return False

    if not sys.stdin.isatty():
        if env_url:
            _info("Provider is not reachable. Start the backend or run ./setup.sh in a terminal.")
        else:
            _info("No provider configured. Run ./setup.sh in a terminal or set API_BASE_URL and MODEL.")
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

    print(f"\n{CYAN}▶{NC} Choose where Eyra should think\n")
    print(f"  {DIM}Local options keep prompts on this Mac. Cloud options send model requests to that provider.{NC}\n")
    print(f"  {BOLD} 1{NC}  Ollama          {DIM}Local, private by default, recommended{NC}{ollama_tag}")
    print(f"  {BOLD} 2{NC}  LM Studio       {DIM}Local, private by default{NC}{lms_tag}")
    print(f"  {BOLD} 3{NC}  OpenRouter      {DIM}Cloud, uses your API key{NC}")
    print(f"  {BOLD} 4{NC}  Groq            {DIM}Cloud, uses your API key{NC}")
    print(f"  {BOLD} 5{NC}  OpenAI          {DIM}Cloud, uses your API key{NC}")
    print(f"  {BOLD} 6{NC}  Custom          {DIM}Any OpenAI-compatible endpoint{NC}")

    if env_url and env_model:
        print(f"\n  {DIM}Current: {_provider_label(env_url)} · {env_model}{NC}")
    print()

    try:
        choice = input("  Provider [1-6, Enter for Ollama]: ").strip() or "1"
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
            key = getpass.getpass("  API key, if required (hidden while you type; press Enter for none): ").strip() or "none"
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
