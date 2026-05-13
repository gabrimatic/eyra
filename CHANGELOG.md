# Changelog

This file tracks meaningful project changes.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## Unreleased

### Added

- Durable local job store backed by SQLite, including persisted job rows, job logs, and operation ledger entries.
- `JOB_STORE_PATH` setting for the local durable job database.
- `/voice-diagnose` for bounded local microphone diagnostics, all-zero audio reporting, selected-device checks, VAD probing, Local Whisper socket checks, and generated WAV transcription checks.
- `VOICE_INPUT_DEVICE`, `VOICE_SAMPLE_RATE`, `VOICE_DEBUG_RECORD_SECONDS`, and `VOICE_DIAGNOSTIC_SAVE_AUDIO` settings for microphone selection and diagnostics.
- Local `scripts/certify_voice_to_computer.py` certification matrix for offline product-contract checks across voice diagnostics, durable jobs, task control, operation ledger, Web approvals, triggers, and disabled-by-default optional surfaces.
- `--synthetic-mic` certification mode for virtual microphone barge-in tests through BlackHole or another system loopback input.
- Opt-in certification rows for enabled browser, OS, MCP, and coding-agent approval paths without contacting remote services by default.
- `/operations` and “What changed?” support for inspecting recent local operation ledger entries.
- Sandboxed `move_to_trash` and `restore_from_trash` filesystem tools.
- Runtime capability and privacy-boundary snapshots for `/capabilities`, “What can you control?”, Web health, and the `discover_capabilities` tool.
- On-demand local context snapshots for `/context` and “What is happening?” with current goal, working directory, recent jobs, and recent changes.
- Hands-free approval and rejection phrases for a single pending approval, with disambiguation when multiple approvals are waiting.
- Hands-free “Stop” and “Show status” phrases for interrupting speech output and reading the runtime status card without typing slash commands.
- `/pause`, `/resume`, “Pause that”, and “Resume that” support for queued tasks.
- `/task logs <id>` and `/task artifacts <id>` support for inspecting durable local job logs and artifacts from the terminal.
- `/task retry <id>` support for retrying failed, cancelled, or blocked deterministic local jobs from their original request.
- `/tasks clear-completed` support for clearing completed, failed, and cancelled in-memory task rows plus matching durable job rows.
- Web APIs for durable job logs, job artifacts, and clearing completed task/job rows.
- “Undo that” support for reversible direct file moves, Trash operations, and created copies.
- OS-gated UI action tools for approved coordinate clicks, focused text entry, and hotkeys.
- Browser form-field filling without submit when network/browser tools are enabled.
- One-time local file-appears triggers, backed by SQLite and visible through `/triggers`.
- SQLite job and trigger stores now set schema versions, common query indexes, WAL mode, busy timeout, and owner-only database file permissions where supported.
- Shared intent detection for terminal and Web UI requests, so screen, filesystem, network, PDF, and background-task decisions stay aligned across both surfaces.
- Event-driven Web UI task updates through a local event stream, replacing the browser-side task polling loop.
- Web runtime creation and API listing for the same persisted one-time file triggers as the terminal runtime.
- Voice/Web-created coding jobs that wait for server-side approval before running the bounded Codex/OpenClaw terminal-agent bridge.
- Pause, resume, and cancel support for local triggers in the terminal and Web API.
- Local dictation mode for terminal and Web, including save-to-file, cancel, and simple literal spelling support.
- Bounded voice correction for failed direct file actions through “No, I meant …”.
- Observe → plan → act → verify → recover ledger evidence for direct local file moves.
- Approved browser downloads to sandboxed local destinations when network/browser tools are enabled.
- Approved browser file uploads from sandboxed local paths when network/browser tools are enabled.
- OS-gated accessibility tree snapshots for grounding frontmost-app UI actions when macOS permissions allow it.
- OS-gated local OCR screen text extraction through `SCREEN_OCR_COMMAND`, with screenshots piped in memory to a local stdin-based OCR command.
- Typed local action schemas and deterministic task specs for common file, UI, screen, and coding requests.
- OS-gated app/window control tools for listing open apps, listing windows, activating apps, and quitting apps with approval.
- Approval-gated `window_action` tool for closing, minimizing, zooming, fullscreening, moving, and resizing macOS windows through System Events when OS tools are enabled.
- Approval-gated `run_shortcut` tool for running local macOS Shortcuts by name, with optional stdin text, when OS tools are enabled.
- OS-gated UI scroll and drag tools using approval-gated local coordinate automation.
- Direct “Open Downloads/Documents/Desktop/tmp” handling through the sandboxed local open-path flow, with operation ledger records.
- Voice-readable file disambiguation for ambiguous direct file requests: Eyra can read numbered matches and apply “Choose number two” style selections.
- Deterministic “latest downloaded file” grounding for direct file moves, using the newest file in Downloads with operation ledger and undo metadata.
- Deterministic named-folder handling for Pictures, Movies, and Music when those folders are included in the filesystem sandbox.
- Terminal-owned Web UI runtime sharing when `WEB_UI_ENABLED=true eyra` is used, so Web approvals, jobs, task events, triggers, tools, browser session, and operation state point at the same runtime objects as the terminal session.
- Action-specific privacy boundary decisions for local model calls, remote model providers, disabled network tools, and online Realtime voice paths.
- One-time local reminder triggers through terminal and Web requests such as “Remind me in 10 minutes to stretch,” persisted in the local trigger store and cancellable through existing trigger controls.
- Recurring local reminder triggers through terminal and Web requests such as “Every 30 minutes remind me to stretch,” persisted in the local trigger store with fire counts and cancellable through existing trigger controls.
- Sandboxed structured file tools for appending to files, prepending to files, and comparing two text files with a bounded unified diff.
- Sandboxed file rename, duplicate, zip compression, and zip extraction tools, including destination-conflict checks and zip path traversal refusal.
- High-risk `delete_permanently` filesystem tool for irreversible deletion, protected by exact server-side approval and sandbox-root refusal. Normal remove/delete still uses Trash by default.

### Changed

- Package version is now `4.1.0` because `v4.0.0` has already been published on GitHub and the current release-qualification work expands the runtime and certification surface after that release.
- Terminal and Web background tasks now mirror compatibility task rows into the durable local job store.
- Direct deterministic file moves, copies, writes, and trash actions now record operation metadata with undo instructions where possible.
- `eyra-web` now runs startup/provider setup and full preflight before serving, then passes real model, tool, vision, and screen capability results into the Web UI runtime.
- Web UI local PDF tasks now use the same controller-owned local extraction path before asking the model to summarize.
- `eyra-web` remains a standalone Web runtime, while `WEB_UI_ENABLED=true eyra` starts a shared Web frontend attached to the terminal-owned runtime.
- Capability snapshots now include concrete privacy-boundary examples that name the action, data classes, destination, opt-in requirement, and whether the action is currently allowed.
- `/voice on` and startup preflight now probe Local Whisper microphone readiness separately from speech output, keep speech available if input fails, and report speech-only state honestly.
- Shared Web health now uses runtime preflight evidence instead of settings-only capability guesses.
- Sandboxed moves now use copy plus unlink instead of filesystem rename so protected-folder or iCloud-backed paths do not hang on macOS.
- Project licensing changed from MIT to PolyForm Noncommercial 1.0.0, with required notices preserving the Eyra name and copyright attribution.

### Fixed

- Web UI open-ended local tool requests now fail clearly when the selected model cannot call tools, instead of starting a task that cannot complete correctly.
- Web UI screen requests now fail clearly when the configured vision model is not vision-capable.
- `WEB_UI_MAX_REQUEST_BYTES` now applies to browser audio uploads as well as JSON API requests.
- `eyra-web` now reports host/port bind failures cleanly instead of showing a traceback.
- Screen intent detection now catches plain phrases such as “what am I looking at?”
- Bare-domain requests such as `example.com` now hit the network-disabled refusal path when network tools are off.
- `open_path` now bounds the macOS `open` handoff so terminal sessions do not hang waiting for Finder.
- File-appears triggers in terminal and Web now wait for real path existence instead of failing early with “Not a file.”
- Web capability and privacy questions now use deterministic local runtime answers instead of model chat.
- Voice diagnostics now skip unattended no-speech VAD and ASR checks instead of failing a healthy microphone capture with no human speech.
- Voice diagnostics now probe alternate input devices after all-zero microphone capture and report the exact `VOICE_INPUT_DEVICE` value when another device delivers audio.
- Voice diagnostics now stop cleanly when no input devices are reported and give recovery guidance for microphone connection, macOS permission, and remote audio forwarding.
- `VOICE_INPUT_DEVICE=0` now selects input device index `0` in the live voice path instead of looking for a device named `0`.
- Trash moves now use unique destination names so concurrent or repeated same-name deletes cannot overwrite another recoverable Trash item.

## [4.0.0] - 2026-05-11

### Added

- Packaged installs expose the `eyra` console command and include the runtime entry module in the wheel.
- Shared tool-registry construction for terminal and web sessions.
- `pypdf` dependency for local PDF text extraction.
- Optional OS operator tools for bounded command execution, process listing, file metadata, file search, LaunchAgent status, app opening, notifications, and clipboard writes.
- Voice-codex-style operator aliases for voice context, system snapshots, URL fetches, LaunchAgent management, Codex/OpenClaw session lookup, and Codex/OpenClaw task delegation.
- Optional stdio MCP bridge for listing and calling configured MCP server tools.
- Optional terminal-agent status, session listing, bounded redacted session reading, and delegation bridges for Codex and OpenClaw.
- Built-in `eyra-web` browser UI for phone or browser access, with text chat, Local Whisper browser voice turns, and optional OpenAI Realtime WebRTC voice.
- Web UI task APIs for creating long work, listing tasks, viewing task detail, and cancelling tasks without blocking the browser request.
- Web UI auth controls: `WEB_UI_TOKEN`, `WEB_UI_REQUIRE_TOKEN=auto`, request size limits, and token-required non-health endpoints by default.
- Local Web UI voice replies through `wh whisper` after Local Whisper browser voice turns.
- Realtime voice session endpoint that mints server-side ephemeral client secrets and can expose allowlisted low-risk tools to Realtime sessions.
- Realtime tool safety controls: `REALTIME_TOOLS_ENABLED=false` by default and `REALTIME_ALLOWED_TOOLS` for explicit low-risk allowlists.
- Background task manager with task ids, lifecycle states, progress, final results, failures, timeouts, and cancellation.
- Task commands: `/tasks`, `/task <id>`, `/cancel <id>`, and `/cancel all`.
- Risky-action approvals with `/approvals`, `/approve <id>`, and `/reject <id>`.
- `/voice-test` manual diagnostic for physical speech interruption checks.
- Non-blocking coordinator behavior so typed and voice input remain available while background tasks run.
- Local PDF text extraction tool for sandboxed PDFs, including scanned/image-only reporting without online OCR.
- Sandboxed filesystem move, copy, open, and reveal tools.
- Deterministic local handlers for common move, copy, create, overwrite, and direct-read file requests.
- Local macOS context tools for frontmost app and sandbox-filtered Finder selection.
- Task and tool safety settings for background concurrency, worker model override, task timeout, tool timeout, model concurrency, and task status updates.
- `VISION_MODEL` for screen/image tasks, allowing a separate vision-capable model from the main tool-capable model.

### Changed

- Realtime voice now requires `OPENAI_API_KEY` explicitly and no longer falls back to provider `API_KEY`.
- Realtime web tool calls now require both Realtime mode and unguessable Web UI/tool-call tokens.
- Realtime now uses `gpt-realtime` as its default model name.
- Realtime tools are no longer exposed by default; risky local tools are not available to Realtime unless explicitly allowlisted.
- Realtime tool allowlists are restricted to Eyra's built-in low-risk Realtime tool set; local clipboard, filesystem, screen, OS, MCP, browser, and agent tools are not exposed to Realtime.
- Web UI now requires token auth automatically for all non-health endpoints, including localhost, and refuses cross-origin API requests from other origins.
- Web UI responses now include local-app security headers: content security policy, no-referrer policy, no-sniff, no-store, and frame denial.
- Voice input now stays active during TTS so a real VAD speech onset can interrupt active speech output and continue recording the user's utterance.
- MCP tool calls now require server-side action-specific approval before execution.
- Risky OS, LaunchAgent, clipboard, shell-command, and agent-delegation tools now require server-side action-specific approval. Model-supplied `confirmed=true` no longer executes those actions.
- Model-driven file overwrites now require server-side action-specific approval. Controller-owned overwrite confirmations still work only after the user explicitly says to overwrite the pending file action.
- Delegated Codex/OpenClaw subprocesses now run under the normal tool timeout and are killed on timeout or cancellation.
- The terminal runtime now uses the shared tool-registry builder instead of constructing tools inside `LiveSession`.
- First-run `.env` writing now preserves and documents Web UI, Realtime, OS tools, MCP, and agent delegation settings.
- `setup.sh` now registers both `eyra` and `eyra-web`.
- Browser clicks now avoid false failures when navigation completes after the click but before text extraction.
- `/voice on` now rechecks Local Whisper at runtime and starts an owned voice loop when voice was disabled or unavailable at startup.
- Voice readiness now tracks input and speech separately, so TTS can keep working while ASR is unavailable or still loading.
- `/voice off` now cancels the owned voice task instead of relying on global task-name lookup.
- `/mode fast` now explains when complexity routing is disabled instead of silently switching to an unused mode.
- `write_file` now protects existing files unless `overwrite=true` is provided explicitly.
- The default filesystem sandbox is now `~/Documents,~/Desktop,~/Downloads,/tmp` instead of the whole home directory.
- Runtime logs now use a writable log path by default, with `EYRA_LOG_FILE` as an override.
- `/goal` now reaches the response pipeline as session context for future replies.
- `/status` now includes task state.
- Weather lookups require an explicit location and no longer use remote IP geolocation.
- Tool-call logs now record tool names and argument keys without persisting argument values.
- Tool-required background tasks now report clearly when the selected model cannot use local tools.
- PDF summary workers now extract local PDF text before asking the model to summarize, with a bounded local fallback if the model returns no text.
- Screen requests now use controller-owned screenshot capture and a configurable vision model, so the vision model does not need native tool calling.
- Document requests such as page-by-page PDF summaries no longer trigger screen capture intent detection.
- `.env.example` now includes `AUTO_PULL_MODELS`.
- CI and contributor docs now include wheel-build verification.
- `read_file` now refuses binary files with a clean message instead of streaming raw bytes into the terminal.
- First-run provider setup now skips the interactive provider picker in non-interactive shells and lets preflight report the backend problem cleanly.

## [3.3.2] - 2026-05-11

### Added

- `NETWORK_TOOLS_ENABLED` setting. Weather and browser tools are now opt-in so the default runtime remains local-only.
- CI coverage for lockfile freshness, linting, tests, setup script syntax, Playwright browser installation, and GitHub Action updates.

### Fixed

- Ollama model auto-pull now works when Ollama's OpenAI-compatible `/v1/models` endpoint is reachable.
- Ollama model pulls during preflight now time out after 600 seconds instead of waiting forever.
- `setup.sh` now creates/verifies provider configuration during setup instead of leaving first-run setup to runtime.
- Project version metadata now matches the 3.x changelog line.
- `pillow` and `pygments` lockfile entries now resolve to patched versions reported by Dependabot.
- `USE_MOCK_CLIENT=true` now bypasses provider setup and backend/model preflight so development smoke tests work without a running backend.
- Local Whisper detection now checks PATH, LaunchAgent plists, and common Homebrew locations.
- Voice input is initialized lazily so typed-only sessions can start even when voice is disabled or unavailable.
- Relative filesystem tool paths now resolve under `FILESYSTEM_DEFAULT_PATH` before sandbox enforcement.
- Invalid JSON tool arguments now return a clear tool error instead of silently running with empty arguments.
- Models that reject native tool-calling now fall back to plain streaming instead of failing the whole response.
- Ollama preflight now warns when the selected model does not advertise native tool-calling capability.

### Removed

- Stale `requirements.txt`; `pyproject.toml` and `uv.lock` are the canonical dependency files.

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
- `_box_row_padded()` helper in `status_presenter.py` for truncating long values with `...`

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
- Browser tool error messages return clean text instead of raw stack traces
- Screenshot tool `execute()` wrapped in try/except with clean error message for capture failures
- Weather tool location parameter URL-encoded with `urllib.parse.quote()` (fixes special characters)
- `mss` screenshot capture in `capture.py` moved to `asyncio.to_thread()` to avoid blocking the event loop
- Complexity scorer cue matching now uses word-boundary regex instead of substring search
- Status presenter header box alignment and padding calculation corrected
- Sound subprocess handles cleaned up via `asyncio.create_task(proc.wait())` instead of fire-and-forget (prevents zombie processes)

### Changed

- `load_dotenv()` moved from module-level to inside `Settings.load_from_env()` (no longer pollutes test env)
- `.env` rewrite in startup now preserves existing comments (except managed ones)
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
