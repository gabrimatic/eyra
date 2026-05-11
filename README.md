# Eyra

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)]()

Eyra is a local-first voice agent for the macOS terminal.

Speak or type. Eyra routes the request to an OpenAI-compatible model, calls local tools when needed, and speaks back through Local Whisper. The default path stays on your machine: Ollama at localhost, Silero VAD in process, screenshots in memory, no telemetry.

Cloud providers and network-backed tools are opt-in.

<p align="center"><img src="screenshot.png" width="800" alt="Eyra terminal screenshot"></p>

---

## Quick start

Runtime: macOS on Apple Silicon, Python 3.11+, Homebrew, and an OpenAI-compatible AI provider. Default provider: [Ollama](https://ollama.com) at `localhost:11434`.

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
chmod +x setup.sh && ./setup.sh
```

Setup creates `.env`, installs dependencies, checks your backend and models, checks Local Whisper for voice, and registers the `eyra` command.

Run from the repo:

```bash
uv run python src/main.py
```

After your shell reloads `~/.local/bin`, the short command works too:

```bash
eyra
```

Eyra runs preflight checks, then enters a live session:

```
Eyra

  Voice: input + speech    Backend: ready

  Type anything or speak. /help for commands.
```

Eyra is now listening. Type or speak without leaving the session.

---

## What it does

- Live session: one terminal process stays open for typed and spoken input.
- Voice: Local Whisper handles transcription and speech; Silero VAD decides when you finished speaking.
- Model routing: one configured model by default, with experimental complexity routing when you turn it on.
- Local tools: screenshot, time, clipboard, system info, and sandboxed filesystem access through function calling.
- Filesystem safety: existing files are not overwritten unless `overwrite=true`; binary reads and edits return a clean message.
- Network tools: weather and browser access stay disabled until `NETWORK_TOOLS_ENABLED=true`.
- Provider support: Ollama, LM Studio, vLLM, OpenRouter, Groq, OpenAI, or any compatible `/v1/chat/completions` endpoint.
- Image handling: screenshots stay in memory and are never written to disk.
- Mock mode: `USE_MOCK_CLIENT=true` starts Eyra without a backend for development and smoke tests.

---

## How it works

Eyra runs one live session with a typed channel, a voice channel, and one streaming response path.

- Voice: [Local Whisper](https://github.com/gabrimatic/local-whisper) handles ASR through Qwen3-ASR and TTS through Kokoro. Eyra records microphone audio with sounddevice, classifies 32ms frames with Silero VAD, and transcribes after a pause.
- Interruption: Eyra stops speaking when you start talking, then listens again.
- Runtime recovery: `/voice on` rechecks Local Whisper and enables whichever side is ready. ASR and TTS are tracked separately, so speech can keep working while input is still loading.
- Typed input: keyboard input is always available and feeds the same conversation as voice.
- Tool use: the model can call local function tools for screenshot, time, clipboard, system info, and filesystem work. Weather and browser tools are available only after you opt in.
- Model routing: complexity routing is experimental and off by default. When enabled, `ComplexityScorer` dispatches requests to Simple, Moderate, or Complex tiers.
- Tool fallback: if a local model rejects native tool calling, Eyra falls back to plain streaming so text chat keeps working. Choose a tool-capable model when you need local tools.

### Preflight

Startup checks:

- Backend reachability (tries `/v1/models`, falls back to Ollama `/api/tags`)
- Every configured model exists (auto-pulls via Ollama if needed)
- Ollama model capabilities, with a warning when the selected model does not advertise native tool calling
- [Local Whisper](https://github.com/gabrimatic/local-whisper) for voice input and speech output (`brew tap gabrimatic/local-whisper && brew install local-whisper`). Input and speech are tracked separately so one side can keep working if the other is unavailable.
- Screen capture (macOS built-in)

The session does not start until the backend and models are confirmed ready.
When `USE_MOCK_CLIENT=true`, backend and model checks are skipped on purpose so you can smoke-test the session without a running provider.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/voice on\|off` | Toggle voice input and speech output, with runtime recovery |
| `/mute` | Mute speech output only |
| `/unmute` | Unmute speech |
| `/goal <text>` | Set session context that guides future replies |
| `/mode fast\|balanced\|best` | Set quality mode |
| `/status` | Show current runtime state |
| `/clear` | Reset conversation history |
| `/quit` | Exit |

Unknown commands are caught locally and never sent to the model.

---

## Quality modes

Set the speed and quality trade-off with `/mode`:

| Mode | Behavior |
|------|----------|
| `fast` | Uses the smallest model when complexity routing is enabled. If routing is off, Eyra says fast mode is unavailable instead of pretending to switch. |
| `balanced` | Lets the router decide (default) |
| `best` | Uses the strongest model |

---

## Complexity routing

Complexity routing is **experimental and off by default**. When disabled (`COMPLEXITY_ROUTING_ENABLED=false`), all requests use the single `MODEL` setting with all tools available. `/mode fast` is available only when routing is enabled because the simple-tier model is validated only in routing mode.

When enabled (`COMPLEXITY_ROUTING_ENABLED=true`), every request in `balanced` mode is scored by `ComplexityScorer` before dispatch.

Scoring factors:

- Pattern matching for common prompt types
- Weighted signal scoring (reasoning cues, code/debug cues, domain terms)
- Prompt length and constraint analysis
- Follow-up context from recent messages

| Score | Model |
|-------|-------|
| Simple | `SIMPLE_MODEL` |
| Moderate | `MODERATE_MODEL` |
| Complex | `MODEL` |

Set model names in `.env`. Any model supported by your provider works.

---

## Configuration

<details><summary><strong>.env reference</strong></summary>

```env
# Provider: any OpenAI-compatible endpoint.
API_BASE_URL=http://localhost:11434/v1
API_KEY=ollama        # Keep this for local providers; set a real key for cloud providers.

USE_MOCK_CLIENT=false

# Default model for all requests when complexity routing is off.
MODEL=gemma3:4b

# Tier models used only when COMPLEXITY_ROUTING_ENABLED=true.
SIMPLE_MODEL=qwen3.5:2b
MODERATE_MODEL=gemma3:4b

# Live runtime settings.
AUTO_PULL_MODELS=true
LIVE_LISTENING_ENABLED=true
LIVE_SPEECH_ENABLED=true
SPEECH_COOLDOWN_MS=3000
VOICE_SILENCE_MS=1500          # Silence after speech before processing (ms).
VOICE_VAD_THRESHOLD=0.6        # Silero VAD sensitivity (0.0-1.0, higher = stricter)

# Optional network tools. Keep false for the local-first default.
NETWORK_TOOLS_ENABLED=false

# Optional log path. Default: ~/Library/Logs/Eyra/eyra.log on macOS.
# EYRA_LOG_FILE=~/Library/Logs/Eyra/eyra.log

# Experimental model routing. When disabled, all requests use MODEL.
COMPLEXITY_ROUTING_ENABLED=false

# Filesystem sandbox: comma-separated allowed root paths.
FILESYSTEM_ALLOWED_PATHS=~/Documents,/tmp
# Relative file paths are resolved under this directory, then checked against the sandbox.
FILESYSTEM_DEFAULT_PATH=~/Documents
```

`API_BASE_URL` accepts any OpenAI-compatible endpoint: Ollama (default), LM Studio, vLLM, OpenRouter, Groq, or OpenAI itself. Local providers ignore `API_KEY`; cloud providers require it.

</details>

---

## Privacy

Default behavior: no telemetry, no analytics, no remote browsing, and no remote weather calls.

| Component | Where it runs |
|-----------|--------------|
| AI backend | `API_BASE_URL` (default: localhost:11434) |
| Silero VAD | ONNX model, in-process, local |
| Voice recording | sounddevice (PortAudio), in-process, local |
| wh transcribe (local-whisper) | Subprocess, local |
| wh whisper (local-whisper) | Subprocess, local |
| Screenshots | In-memory; never written to disk |
| Weather/browser tools | Disabled by default; contact remote sites only when `NETWORK_TOOLS_ENABLED=true` and a tool is used. Weather requires an explicit location and does not use remote IP geolocation. |

Data leaves your machine only when you choose a remote AI provider or turn on network tools. A remote `API_BASE_URL` receives prompts and images. Network tools send the requested URL, search query, or weather location to the relevant remote service.

---

## Architecture

```
eyra/
├── pyproject.toml
├── setup.sh
├── src/
│   ├── main.py                     # Entry point, preflight, live session launch
│   ├── runtime/
│   │   ├── live_session.py         # Unified orchestrator
│   │   ├── models.py              # Runtime state and event dataclasses
│   │   ├── preflight.py           # Backend, model, and capability validation
│   │   ├── speech_controller.py   # TTS output and STT input coordination
│   │   ├── voice_input.py         # Silero VAD recording + local-whisper transcription
│   │   ├── status_presenter.py    # Terminal status header and updates
│   │   └── startup.py             # First-run setup and .env management
│   ├── tools/
│   │   ├── base.py                # BaseTool abstract + ToolResult
│   │   ├── registry.py            # Tool registry and dispatch
│   │   ├── screenshot.py          # In-memory screenshot via mss
│   │   ├── time_tool.py           # Current time tool
│   │   ├── weather.py             # Optional network weather tool
│   │   ├── clipboard.py           # Clipboard reader tool
│   │   ├── system_info.py         # System info tool
│   │   ├── browser.py             # Optional network browser tools
│   │   └── filesystem.py          # Sandboxed file read/write/edit/list
│   ├── chat/
│   │   ├── capture.py             # In-memory screenshot capture
│   │   ├── complexity_scorer.py   # Deterministic prompt routing
│   │   ├── message_handler.py     # Model selection, response shaping, streaming
│   │   └── session_state.py       # Shared session types
│   ├── clients/
│   │   ├── base_client.py         # BaseAIClient abstract
│   │   └── ai_client.py           # OpenAI-compatible async client
│   └── utils/
│       ├── settings.py
│       ├── image_history.py
│       ├── sound_player.py
│       ├── theme.py
│       └── mock_client.py
```

---

## Troubleshooting

<details><summary><strong>AI backend not responding</strong></summary>

Check that your backend is running and reachable at the URL in `API_BASE_URL`. Eyra probes `/v1/models` on startup and reports the result.

For Ollama (default):

```bash
ollama list
curl http://localhost:11434/v1/models
```

If using a different provider, verify `API_BASE_URL` and `API_KEY` in `.env` are correct.

</details>

<details><summary><strong>Runtime logs</strong></summary>

Runtime logs are written to `~/Library/Logs/Eyra/eyra.log` on macOS. Set `EYRA_LOG_FILE` if you want a different location.

</details>

<details><summary><strong>Tools are not being used</strong></summary>

The selected model must support native tool calling. In Ollama, check:

```bash
ollama show <model>
```

If the model does not list tools, text chat will still work, but local tool calls will be skipped by the backend. Choose a tool-capable model for filesystem, screenshot, time, clipboard, weather, or browser actions.

</details>

<details><summary><strong>Voice not working</strong></summary>

Voice requires [Local Whisper](https://github.com/gabrimatic/local-whisper), which powers input (ASR) and output (TTS). Install it:

```bash
brew tap gabrimatic/local-whisper && brew install local-whisper
```

Eyra's preflight automatically detects the installation (even if `wh` is not on PATH) and starts the service if needed. If preflight reports it's installed but not running, start manually with `wh start`.

You can also toggle voice at runtime with `/voice on|off`. If voice was disabled in `.env`, `/voice on` performs the Local Whisper check at runtime and enables whichever voice features are ready. If ASR is not ready yet, speech can still remain on.

</details>

---

## Development

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
uv run pytest -q
uv run ruff check src tests
uv lock --check
bash -n setup.sh
uv build --wheel
USE_MOCK_CLIENT=true LIVE_LISTENING_ENABLED=false LIVE_SPEECH_ENABLED=false uv run python src/main.py
```

---

## Credits

[Ollama](https://ollama.com) · [local-whisper](https://github.com/gabrimatic/local-whisper) (STT + TTS via Kokoro) · [Silero VAD](https://github.com/snakers4/silero-vad) (voice activity detection) · [mss](https://github.com/BoboTiG/python-mss)

<details>
<summary><strong>Legal notices</strong></summary>

### Trademarks

"Ollama" is a trademark of its respective owner. All trademark names are used solely to describe compatibility with their respective technologies. This project is not affiliated with, endorsed by, or sponsored by any trademark holder.

### Third-party licenses

All dependencies use MIT, BSD, or Apache 2.0 licenses. See each package for details.

</details>

## License

MIT. See [LICENSE](LICENSE).

---

Created by [Soroush Yousefpour](https://gabrimatic.info)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/gabrimatic)
