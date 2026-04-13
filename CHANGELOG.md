# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [3.3.1] - 2026-04-13

### Changed

- Bump `openai` minimum from 1.0.0 to 2.31.0
- Bump `python-dotenv` minimum from 1.1.0 to 1.2.2
- Bump `sounddevice` minimum from 0.5.0 to 0.5.5
- Bump `pytest` minimum from 8.0 to 9.0.3
- Bump `ruff` minimum from 0.11.0 to 0.15.10

---

## [3.3.0] - 2026-03-15

### Added

- 30-second timeout per tool call execution (`asyncio.wait_for`) to prevent infinite hangs
- `_int()` and `_float_range()` validation helpers for numeric env vars in `settings.py`
- `VOICE_VAD_THRESHOLD` range validation (0.0 to 1.0)
- Empty/blank path rejection in filesystem tools
- Protocol-relative URL handling in `OpenUrlTool` (`//example.com` becomes `https://example.com`)
- `_box_row_padded()` helper in `status_presenter.py` for truncating long values with `…`

### Fixed

- `rstrip("/v1")` replaced with `removesuffix("/v1")` in preflight and startup for safe URL parsing
- `/goal` command now preserves original case instead of lowercasing
- Race condition: `_handle_user_input` now checks `_busy` to prevent voice and typed input collision
- Spinner properly awaited on cancel (`await spinner` after `cancel()`) to fix garbled output during tool calls
- `_busy` cleared only after TTS finishes (`await speech.wait_for_speech()`)
- Empty response no longer prints a stray newline
- KeyboardInterrupt caught in input loop (shows fresh prompt instead of crashing)
- Tool call history now persisted in conversation via `history` parameter in `stream_with_tools`
- Image messages from screenshot tools placed after all tool result messages, not interleaved
- Text-format tool call detection buffer increased from 30 to 50 chars, uses `in` instead of `startswith` to catch preamble before XML
- `_TEXT_TOOL_PATTERNS` pattern 2 regex fixed to handle nested JSON arguments
- `EditFileTool` reads with strict encoding and returns clean error for binary files instead of silently replacing characters
- `ListDirectoryTool` uses a generator with early stop instead of materializing entire directory listing
- `BrowserSession.page()` closes old browser/context before launching a new one (prevents Chromium process leak)
- `BrowserSession.close()` wrapped in try/except/finally to prevent Playwright leak on browser crash
- `WebSearchTool` uses `wait_for_selector` with fallback instead of hardcoded `wait_for_timeout`
- Browser tool error messages return user-friendly strings instead of raw stack traces
- Screenshot tool `execute()` wrapped in try/except with clean error message for capture failures
- Weather tool location parameter URL-encoded with `urllib.parse.quote()` (fixes special characters)
- `mss` screenshot capture in `capture.py` moved to `asyncio.to_thread()` to avoid blocking the event loop
- Complexity scorer cue matching now uses word-boundary regex instead of substring search
- Status presenter header box alignment and padding calculation corrected
- Sound subprocess handles cleaned up via `asyncio.create_task(proc.wait())` instead of fire-and-forget (prevents zombie processes)

### Changed

- `load_dotenv()` moved from module-level to inside `Settings.load_from_env()` (no longer pollutes test env)
- `.env` rewrite in startup now preserves user comments (except managed ones)
- `ollama pull` timeout increased to 600 seconds with progress message
- Console log handler level raised from WARNING to CRITICAL (no error stack traces in the terminal UI)
- Voice transcription prefix changed from DIM to YELLOW for visibility
- `conversation_messages` list passed directly to `process_task_stream` (not a copy) so tool call history is preserved
- `stream_with_tools` accepts new `history: list[dict] | None` parameter across all client implementations
- ANSI color constants in `theme.py` are empty strings when not in a terminal (`sys.stdout.isatty()` check)

---

## [3.2.0] - 2026-03-12

### Fixed

- Voice input and speech output now use the resolved `wh` binary path instead of relying on PATH. Fixes voice failing silently when `wh` is installed via LaunchAgent or at the well-known path but not on shell PATH.
- Socket transcription guard: checks socket existence before connecting, falls cleanly to CLI fallback.

### Changed

- `PreflightResult` and `LiveRuntimeState` now carry `wh_bin: str | None` so the resolved binary path flows from preflight through to `SpeechController` and `VoiceInput`.
- `/voice on|off` command toggles both voice input and speech output as a single unit.
- Unified "Voice" status in header and status card (was separate Voice/Speech lines).
- Preflight messaging clarified: Local Whisper powers both voice input (ASR) and speech output (TTS).

### Removed

- Fake microphone check (always returned True when wh was available).

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

- Redesigned as a voice-first on-device agent with tool use
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

- Unified agent experience: text, watch, and voice as interaction styles within one session
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
