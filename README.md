# Eyra

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)]()

**Real-time AI screen analysis from the terminal.**

Eyra captures screenshots or webcam frames, routes them through a vision model, and responds in text or voice. Processing is local by default, with an optional cloud fallback for complex tasks.

<p align="center"><img src="screenshot.png" width="800" alt="Eyra terminal screenshot"></p>

---

## Quick Start

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
chmod +x setup.sh && ./setup.sh
cp .env.example .env   # edit as needed
python src/main.py
```

| Prompt | What happens |
|--------|--------------|
| `python src/main.py` | Mode selection menu |
| `1` | Manual mode |
| `2` | Live mode |
| `3` | Voice mode |

---

## What It Does

- Captures screenshots in memory via `mss`, no disk I/O
- Captures webcam frames via OpenCV with AVFoundation backend
- Scores task complexity using spaCy NLP and optionally CLIP
- Routes to the appropriate Qwen3.5 model via Ollama based on score
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
| Simple | qwen3.5:4b-q4_K_M (Ollama) | qwen3.5:4b-q4_K_M (Ollama) |
| Moderate | qwen3.5:9b-q4_K_M (Ollama) | qwen3.5:9b-q4_K_M (Ollama) |
| Complex | qwen3.5:27b-q4_K_M (Ollama) | qwen3.5:27b-q4_K_M (Ollama) |

---

## Configuration

<details><summary><strong>.env reference</strong></summary>

```env
OLLAMA_HOST=localhost
OLLAMA_PORT=11434
USE_MOCK_CLIENT=false
```

`USE_MOCK_CLIENT=true` runs a local stub instead of any AI backend, useful for development.

</details>

---

## Privacy

| Component | Where it runs |
|-----------|--------------|
| Ollama (qwen3.5) | localhost:11434 |
| wh listen (local-whisper) | Subprocess, fully local |
| wh whisper (local-whisper) | Subprocess, fully local |
| Screenshots / webcam | In-memory only, never written to disk |

No telemetry. No analytics. No network calls. Everything runs on your machine.

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
│   │   └── ollama_client.py     # Ollama async HTTP client
│   ├── modes/
│   │   ├── base_mode.py
│   │   ├── manual_mode.py
│   │   ├── live_mode.py
│   │   └── voice/
│   │       ├── voice_mode.py    # Voice pipeline (STT + LLM + TTS)
│   └── utils/
│       ├── settings.py
│       ├── image_history.py
│       ├── sound_player.py
│       └── mock_client.py
```

---

## Troubleshooting

<details><summary><strong>Ollama not responding</strong></summary>

Verify the service is running:

```bash
ollama list
curl http://localhost:11434/api/tags
```

Check `OLLAMA_HOST` and `OLLAMA_PORT` in `.env` match your setup.

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
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
cp .env.example .env
USE_MOCK_CLIENT=true python src/main.py
```

**Adding a new AI backend:** subclass `BaseAIClient` in `src/clients/base_client.py`, implement `generate` and `generate_with_image`, then register it in `message_handler.py`.

**Adding a new mode:** subclass `BaseMode` in `src/modes/base_mode.py`, implement `run`, then add a menu entry in `src/main.py`.

---

## License

MIT. See [LICENSE](LICENSE).

---

Created by [Soroush Yousefpour](https://gabrimatic.info)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/gabrimatic)
