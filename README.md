# Eyra

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)]()

**Real-time AI screen analysis from the terminal.**

Eyra captures screenshots or webcam frames, routes them through a vision model, and responds in text or voice. Works with any OpenAI-compatible provider — local or cloud.

<p align="center"><img src="screenshot.png" width="800" alt="Eyra terminal screenshot"></p>

---

## Quick Start

Requires an AI provider with an OpenAI-compatible API. Defaults to [Ollama](https://ollama.com) at `localhost:11434`. Point `API_BASE_URL` in `.env` at any other provider.

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
chmod +x setup.sh && ./setup.sh
```

Setup creates `.env` from `.env.example`. Open it, set your models, then run.

> **First run:** the CLIP model (~340 MB) is downloaded to `~/.cache/clip/` on startup. This is a one-time download.

```bash
uv run python src/main.py
```

| Input | What happens |
|-------|--------------|
| `1` | Manual mode |
| `2` | Live mode |
| `3` | Voice mode |

---

## What It Does

- Captures screenshots in memory via `mss`, no disk I/O
- Captures webcam frames via OpenCV with AVFoundation backend
- Scores task complexity using spaCy NLP and optionally CLIP
- Routes to the appropriate model via any OpenAI-compatible provider based on score
- Streams AI responses sentence by sentence
- Speaks AI responses via local-whisper (`wh whisper`), using Kokoro TTS

---

## Modes

| Mode | Trigger | What it does |
|------|---------|--------------|
| Manual | Type a prompt | Interactive chat. Append `#image` for a screenshot or `#selfie` for webcam. |
| Live | Select at startup | Captures a screenshot every second, sends to AI, streams response. Runs until interrupted. |
| Voice | `wh listen` | Records audio, transcribes via local-whisper, sends to LLM, speaks response via `wh whisper`. |

### Manual Mode

Type any prompt at the `>` input. Attach visual context with keywords:

- `#image` — captures the current screen
- `#selfie` — captures a webcam frame

Both are encoded as base64 JPEG in memory and sent with the message.

### Live Mode

Runs a continuous loop. Each iteration:

1. Screenshot captured via `mss`
2. Complexity scored
3. Routed to appropriate model
4. Response streamed to terminal

Interrupt with `Ctrl+C`.

### Voice Mode

<details><summary><strong>Setup</strong></summary>

Voice mode requires [local-whisper](https://github.com/gabrimatic/local-whisper) installed and running.

Once set up, `wh` handles recording, transcription, and speech. No additional configuration needed.

</details>

Full pipeline per utterance:

1. `wh listen` captures audio and returns the transcription
2. Transcript sent to LLM
3. Response spoken via `wh whisper`

---

## Complexity Routing

Every request is scored by `ComplexityScorer` before dispatch.

Scoring factors:

- Vocabulary richness (spaCy)
- Syntactic depth (dependency parse)
- Named entity density
- CLIP image embedding distance (image tasks)

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

SCREENSHOT_INTERVAL=1   # seconds between captures in Live mode

# Model names — set to any model your provider supports
SIMPLE_TEXT_MODEL=...
MODERATE_TEXT_MODEL=...
SIMPLE_IMAGE_MODEL=...
MODERATE_IMAGE_MODEL=...
COMPLEX_MODEL=...
```

`.env.example` ships with working defaults. Copy, fill in your models, done.

`API_BASE_URL` accepts any OpenAI-compatible endpoint. Point it at Ollama (default), LM Studio, vLLM, OpenRouter, Groq, or OpenAI itself. `API_KEY` is ignored by local providers but required for cloud ones. `USE_MOCK_CLIENT=true` runs a local stub with no backend at all, useful for development.

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
├── requirements.txt
├── src/
│   ├── main.py
│   ├── chat/
│   │   ├── capture.py           # In-memory screenshot and webcam capture
│   │   ├── complexity_scorer.py # NLP + CLIP task complexity routing
│   │   ├── message_handler.py   # Message history and AI client routing
│   │   └── words.py             # Complexity indicator vocabulary
│   ├── clients/
│   │   ├── base_client.py       # BaseAIClient abstract
│   │   └── ai_client.py         # OpenAI-compatible async client
│   ├── modes/
│   │   ├── base_mode.py
│   │   ├── manual_mode.py
│   │   ├── live_mode.py
│   │   └── voice/
│   │       └── voice_mode.py    # Voice pipeline (STT + LLM + TTS)
│   └── utils/
│       ├── settings.py
│       ├── image_history.py
│       ├── sound_player.py
│       └── mock_client.py
```

---

## Troubleshooting

<details><summary><strong>AI backend not responding</strong></summary>

Check that your backend is running and reachable at the URL in `API_BASE_URL`. For Ollama (default):

```bash
ollama list
curl http://localhost:11434/api/tags
```

If using a different provider, verify `API_BASE_URL` and `API_KEY` in `.env` are correct.

</details>

<details><summary><strong>Voice mode not working</strong></summary>

Voice mode requires local-whisper to be installed and running. Check with:

```bash
wh status
```

If the service is not running: `wh start`. See [local-whisper](https://github.com/gabrimatic/local-whisper) for setup instructions.

</details>

<details><summary><strong>Webcam not opening</strong></summary>

Eyra uses OpenCV with the AVFoundation backend. Grant camera access to your terminal in System Settings → Privacy & Security → Camera.

</details>

---

## Development

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
USE_MOCK_CLIENT=true uv run python src/main.py
```

**Adding a new AI backend:** subclass `BaseAIClient` from `src/clients/base_client.py`, implement `generate_completion_stream` and `generate_completion_with_image_stream`, then register it in `message_handler.py`. See `CONTRIBUTING.md` for the full steps.

**Adding a new mode:** subclass `BaseMode` in `src/modes/base_mode.py`, implement `run`, then add a menu entry in `src/main.py`.

---

## Credits

[Ollama](https://ollama.com) · [local-whisper](https://github.com/gabrimatic/local-whisper) (STT + TTS via Kokoro) · [spaCy](https://spacy.io) · [CLIP](https://github.com/openai/CLIP) by OpenAI · [mss](https://github.com/BoboTiG/python-mss) · [OpenCV](https://opencv.org)

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
