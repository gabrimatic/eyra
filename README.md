# Eyra

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)]()

**Live AI screen assistant for the terminal.**

Eyra starts immediately as a live screen-aware assistant. It observes your screen, listens for voice input, and responds in text or speech. No mode switching, no commands required to become useful. Works with any OpenAI-compatible provider.

<p align="center"><img src="screenshot.png" width="800" alt="Eyra terminal screenshot"></p>

---

## Quick Start

Requires an AI provider with an OpenAI-compatible API. Defaults to [Ollama](https://ollama.com) at `localhost:11434`. Point `API_BASE_URL` in `.env` at any other provider.

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
chmod +x setup.sh && ./setup.sh
```

Setup creates `.env`, installs dependencies, and verifies your backend and models. Then run:

```bash
uv run python src/main.py
```

Eyra runs preflight checks, then enters a live session:

```
Eyra Live

  Observation: on  Listening: on  Speech: on
  Backend: local  Routing: automatic

  Type anything or speak. /pause /mute /goal /status /quit
```

From this point, Eyra is already observing your screen. Type or speak at any time.

---

## What It Does

- Launches directly into a live, always-on assistant session
- Continuously observes your screen with cheap fingerprinting, only captures when something changes
- Accepts typed or spoken input at any time without leaving the live session
- Routes to the appropriate model tier based on deterministic prompt analysis
- Speaks responses via local-whisper when available
- Works with any OpenAI-compatible provider (Ollama, LM Studio, vLLM, OpenRouter, etc.)
- Captures screenshots in memory, no disk I/O

---

## How It Works

Eyra runs as one live session with concurrent subsystems:

- **Screen observation** continuously fingerprints the screen. When a material change is detected (debounced), it captures a full screenshot and analyzes it with the smallest adequate model.
- **Typed input** is always available inline. Trivial messages (greetings, thanks) skip the screenshot. Anything substantive gets current screen context automatically.
- **Voice input** listens continuously via local-whisper when available. Speak naturally; Eyra interrupts its own speech to hear you.
- **Speech output** speaks responses and proactive observations via local-whisper TTS.

### Preflight

On startup, Eyra validates:

- Backend reachability (tries `/v1/models`, falls back to Ollama `/api/tags`)
- Every configured model exists (auto-pulls via Ollama if needed)
- Screen capture, microphone, and speech capabilities

The session does not start until the backend and models are confirmed ready.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/pause` | Pause screen observation |
| `/resume` | Resume observation |
| `/mute` | Mute speech output |
| `/unmute` | Unmute speech |
| `/goal <text>` | Set a watch goal ("tell me when an error appears") |
| `/inspect` | Force a screen capture and analysis now |
| `/mode fast\|balanced\|best` | Set quality mode |
| `/status` | Show current runtime state |
| `/clear` | Reset conversation history |
| `/quit` | Exit |

Unknown commands are caught locally and never sent to the model.

---

## Quality Modes

Control the speed/quality trade-off with `/mode`:

| Mode | Behavior |
|------|----------|
| `fast` | Always use the smallest model |
| `balanced` | Let the router decide (default) |
| `best` | Always use the strongest model |

---

## Complexity Routing

In `balanced` mode, every request is scored by `ComplexityScorer` before dispatch.

Scoring factors:

- Pattern matching for common prompt types
- Weighted signal scoring (reasoning cues, code/debug cues, domain terms)
- Prompt length and constraint analysis
- Follow-up context from recent messages

| Score | Text model | Image model |
|-------|-----------|-------------|
| Simple | `SIMPLE_TEXT_MODEL` | `SIMPLE_IMAGE_MODEL` |
| Moderate | `MODERATE_TEXT_MODEL` | `MODERATE_IMAGE_MODEL` |
| Complex | `COMPLEX_MODEL` | `COMPLEX_MODEL` |

All model names are set in `.env`. Any model supported by your provider works.

---

## Configuration

<details><summary><strong>.env reference</strong></summary>

```env
# Provider — any OpenAI-compatible endpoint
API_BASE_URL=http://localhost:11434/v1
API_KEY=ollama        # leave as-is for local; set your key for cloud providers

USE_MOCK_CLIENT=false

SCREENSHOT_INTERVAL=1   # seconds between captures in watch mode

# Model names — set to any model your provider supports
SIMPLE_TEXT_MODEL=...
MODERATE_TEXT_MODEL=...
SIMPLE_IMAGE_MODEL=...
MODERATE_IMAGE_MODEL=...
COMPLEX_MODEL=...

# Live runtime settings
AUTO_PULL_MODELS=true
LIVE_LISTENING_ENABLED=true
LIVE_SPEECH_ENABLED=true
LIVE_OBSERVATION_ENABLED=true
OBSERVATION_DEBOUNCE_MS=1500
OBSERVATION_COOLDOWN_MS=5000
SPEECH_COOLDOWN_MS=3000
```

`API_BASE_URL` accepts any OpenAI-compatible endpoint. Point it at Ollama (default), LM Studio, vLLM, OpenRouter, Groq, or OpenAI itself. `API_KEY` is ignored by local providers but required for cloud ones.

</details>

---

## Privacy

| Component | Where it runs |
|-----------|--------------|
| AI backend | `API_BASE_URL` (default: localhost:11434) |
| wh listen (local-whisper) | Subprocess, fully local |
| wh whisper (local-whisper) | Subprocess, fully local |
| Screenshots / webcam | In-memory only, never written to disk |

No telemetry. No analytics. By default everything runs on your machine. If you point `API_BASE_URL` at a remote provider, prompts and images will leave your machine to that provider.

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
│   │   ├── screen_observer.py     # Cheap fingerprinting + triggered capture
│   │   ├── speech_controller.py   # TTS output and STT input via local-whisper
│   │   └── status_presenter.py    # User-facing status header and updates
│   ├── chat/
│   │   ├── capture.py             # In-memory screenshot and webcam capture
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

<details><summary><strong>Voice not working</strong></summary>

Voice requires local-whisper to be installed and running. Check with:

```bash
wh status
```

If the service is not running: `wh start`. See [local-whisper](https://github.com/gabrimatic/local-whisper) for setup instructions.

</details>

<details><summary><strong>Webcam not opening</strong></summary>

Eyra uses OpenCV with the AVFoundation backend. Grant camera access to your terminal in System Settings > Privacy & Security > Camera.

</details>

---

## Development

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
uv run pytest -q
USE_MOCK_CLIENT=true uv run python src/main.py
```

---

## Credits

[Ollama](https://ollama.com) · [local-whisper](https://github.com/gabrimatic/local-whisper) (STT + TTS via Kokoro) · [mss](https://github.com/BoboTiG/python-mss) · [OpenCV](https://opencv.org)

<details>
<summary><strong>Legal notices</strong></summary>

### Trademarks

"Ollama" is a trademark of its respective owner. All trademark names are used solely to describe compatibility with their respective technologies. This project is not affiliated with, endorsed by, or sponsored by any trademark holder.

### Third-Party Licenses

All dependencies use MIT, BSD, or Apache 2.0 licenses. See each package for details.

</details>

## License

MIT. See [LICENSE](LICENSE).

---

Created by [Soroush Yousefpour](https://gabrimatic.info)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/gabrimatic)
