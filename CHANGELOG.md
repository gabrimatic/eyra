# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [3.1.0] - 2026-03-11

### Added

- Silero VAD voice input system (`src/runtime/voice_input.py`): records from the microphone via sounddevice, classifies each 32ms frame (512 samples at 16 kHz) with Silero's neural ONNX model, transcribes via local-whisper socket (CLI fallback)
- `VOICE_SILENCE_MS` setting: configurable silence duration after speech before processing (default 1500ms)
- `silero-vad`, `onnxruntime`, `numpy`, `sounddevice` dependencies

### Changed

- Voice input no longer shells out to `wh listen`; recording and VAD happen in-process for precise control
- `SpeechController` delegates to `VoiceInput` for recording and transcription
- Voice loop cancellation: `_stream_response()` cancels active recording immediately when processing begins

### Removed

- Direct dependency on `wh listen` subprocess for voice recording (transcription still uses local-whisper)

---

## [3.0.0] - 2026-03-10

### Added

- Tool system (`src/tools/`) with registry pattern: `base.py` defines the interface, `registry.py` manages dispatch
- `take_screenshot` tool available to Complex-tier requests; the model decides when visual context is needed
- `get_current_time`, `get_weather`, `read_clipboard`, `get_system_info` tools
- `COMPLEXITY_ROUTING_ENABLED` setting (experimental, off by default). When disabled, all requests use `MODEL` with all tools available
- Sound feedback for listen, process, and respond events

### Changed

- Redesigned as a voice-first, tool-using assistant
- Screenshot capture is now on-demand (model-driven tool call) rather than a constant polling loop
- Runtime reduced from three concurrent tasks to two (input loop + voice loop)
- Settings renamed: `COMPLEX_MODEL` to `MODEL`, `SIMPLE_TEXT_MODEL` to `SIMPLE_MODEL`, `MODERATE_TEXT_MODEL` to `MODERATE_MODEL`
- Complexity routing is now optional and off by default; when off, a single `MODEL` handles all requests

### Removed

- Observation loop and screen watching (`screen_observer.py`, `SCREENSHOT_INTERVAL`)
- Legacy interaction styles: Manual, Live, Watch modes
- `opencv-python` dependency (webcam/selfie capture removed)
- Selfie and webcam capture functions from `capture.py`
- Image-specific model settings (`SIMPLE_IMAGE_MODEL`, `MODERATE_IMAGE_MODEL`)
- `SessionState` class and `LastTaskMeta`; `session_state.py` now contains only enums
- Image routing in complexity scorer

---

## [2.0.0] - 2026-03-08

### Added

- Unified assistant experience: text, watch, and voice as interaction styles within one session
- Quality modes: `/mode fast|balanced|best` for direct control over model selection
- Watch mode: continuous screen analysis with a goal, change gating, and delta-style responses
- Task shortcuts: `#explain`, `#extract`, `#summarize`, `#review`, `#bug`, `#compare`
- Recovery commands: `/retry`, `/retry best`, `/clear`, `/status`
- Response shaping by interaction style (concise for voice, delta-focused for watch)
- Shared session state across interaction styles
- Deterministic prompt routing replacing spaCy/CLIP-based scorer
- Follow-up context awareness in routing

### Changed

- Modes unified into one session loop; switching styles preserves context
- Live mode replaced by watch mode with goal-driven analysis
- Removed spaCy, CLIP, torch, torchvision, torchaudio, numpy dependencies
- Router import is now instant with negligible memory footprint

### Removed

- `words.py` (898-entry keyword list replaced by ~85 curated domain terms)
- spaCy NLP pipeline, CLIP image scoring, PyTorch dependencies
- Mode selection menu at startup (replaced by inline commands)

---

## [1.0.0] - 2026-03-08

### Added

- Voice mode: speech input and output via local-whisper (`wh listen` / `wh whisper`)
- Adaptive model routing via `ComplexityScorer`, dispatching to configurable models via any OpenAI-compatible provider based on task complexity
- In-memory screenshot and webcam capture via `mss` and OpenCV, no files written to disk

### Changed

- All inference via any OpenAI-compatible provider; defaults to Ollama at localhost with three model tiers configurable in `.env`
- `API_BASE_URL` and `API_KEY` replace provider-specific env vars; point at any OpenAI-compatible endpoint
- Project restructured into `chat/`, `clients/`, `modes/`, and `utils/` packages
- Conversation context sent to the AI is capped at the 10 most recent messages; full history is preserved locally for display

---

## [0.1.0] - 2024-12-01

- Initial release: Manual and Live modes, OpenAI backend, screenshot and webcam capture
