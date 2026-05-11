# Contributing

Eyra contributions should keep the local-first contract intact: voice, tools, setup, and docs must fail clearly and recover when they can.

Bug fixes, new tools, backend improvements, and sharper docs all help.

## Development setup

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
```

Set `USE_MOCK_CLIENT=true` in `.env` to run without a backend during development. Mock mode skips provider and model preflight on purpose.

Voice input and speech output require [Local Whisper](https://github.com/gabrimatic/local-whisper). Install: `brew tap gabrimatic/local-whisper && brew install local-whisper`. Check with `wh status`. Input and speech are tracked separately, so tests should cover speech-only and input-only states when touching voice preflight.

Network tools are disabled by default. Set `NETWORK_TOOLS_ENABLED=true` in `.env` only when testing weather or browser tools. Weather requests require an explicit location so tests and runtime use never rely on remote IP geolocation.

## Architecture

```
eyra/
├── src/
│   ├── main.py                  # Entry point, preflight checks, session launch
│   ├── chat/
│   │   ├── capture.py           # In-memory screenshot capture
│   │   ├── complexity_scorer.py # Deterministic prompt routing
│   │   ├── message_handler.py   # Model selection, response shaping, streaming
│   │   └── session_state.py     # Quality mode and interaction style enums
│   ├── clients/
│   │   ├── base_client.py       # BaseAIClient abstract class
│   │   └── ai_client.py         # OpenAI-compatible async client
│   ├── runtime/
│   │   ├── live_session.py      # Central orchestrator (voice + typed input)
│   │   ├── models.py            # Runtime data models
│   │   ├── preflight.py         # Backend and model validation
│   │   ├── startup.py           # First-run setup and .env management
│   │   ├── speech_controller.py # TTS/STT coordination
│   │   ├── voice_input.py       # Silero VAD recording + local-whisper transcription
│   │   └── status_presenter.py  # Session status display
│   ├── tools/
│   │   ├── base.py              # Base tool interface
│   │   ├── registry.py          # Tool registration and lookup
│   │   ├── screenshot.py        # On-demand screenshot tool
│   │   ├── time_tool.py         # Current time tool
│   │   ├── weather.py           # Optional network weather tool
│   │   ├── clipboard.py         # Clipboard reader tool
│   │   ├── system_info.py       # System info tool
│   │   ├── browser.py           # Optional network browser tools
│   │   └── filesystem.py        # Sandboxed file read/write/edit/list
│   └── utils/
│       ├── settings.py          # .env config loader
│       ├── image_history.py     # Image context management
│       ├── sound_player.py      # Audio feedback
│       ├── theme.py             # Terminal colors and formatting
│       └── mock_client.py       # Mock client for development
```

Eyra starts one `LiveSession` with concurrent input loops for voice and typed input. The model can call tools, including screenshot, on demand.

Routing path:

```text
message_handler.py -> complexity_scorer.py -> quality mode override -> response shaping -> client selection -> streaming
```

## New AI backend

Eyra works with any OpenAI-compatible endpoint. Set `API_BASE_URL` and `API_KEY` in `.env`; standard providers do not need code changes.

Known compatible providers: Ollama, LM Studio, vLLM, OpenRouter, Groq, and OpenAI.

For a provider that doesn't follow the `/v1/chat/completions` spec:

1. Create a file in `src/clients/`, e.g. `src/clients/my_client.py`
2. Subclass `BaseAIClient` from `src/clients/base_client.py`
3. Implement `generate_completion_stream(messages, model_name) -> AsyncIterator[str]`
4. Implement `stream_with_tools(messages, tools, model_name) -> AsyncIterator[str]`
5. Wire it into `src/chat/message_handler.py`

Keep streaming behavior consistent with existing clients. Yield string chunks, not complete strings.

## New tool

1. Create a file in `src/tools/`, e.g. `src/tools/my_tool.py`
2. Implement the tool interface from `src/tools/base.py`
3. Register it in `src/runtime/live_session.py` inside `_build_tool_registry()`

The model invokes tools on demand. Keep tool implementations stateless where possible. Gate every network-backed tool behind `NETWORK_TOOLS_ENABLED`.
Relative filesystem paths resolve under `FILESYSTEM_DEFAULT_PATH` and are still checked against `FILESYSTEM_ALLOWED_PATHS`.
`write_file` creates new files by default and requires `overwrite=true` before replacing an existing file.
The default filesystem sandbox is `~/Documents,/tmp`; broaden it only when a workflow needs more access.

## Testing

```bash
uv run pytest -q                           # Run all tests
uv run pytest tests/test_runtime.py -q     # Run a single test file
uv run pytest tests/test_runtime.py -k "test_name" -q  # Run a single test
uv run ruff check src tests                # Lint
uv lock --check                            # Verify uv.lock matches pyproject.toml
bash -n setup.sh                           # Check setup script syntax
uv build --wheel                           # Verify the distributable package
```

Manual verification:

1. `USE_MOCK_CLIENT=true LIVE_LISTENING_ENABLED=false LIVE_SPEECH_ENABLED=false uv run python src/main.py` - confirm Eyra starts as a live session
2. Type a prompt, confirm streamed response
3. Speak a prompt (requires Local Whisper), confirm voice response
4. `/status` - confirm current state is displayed
5. `/clear` - confirm session is reset

## PR checklist

- Code follows the style of the surrounding file (indentation, naming, structure)
- No new dependencies added without updating `pyproject.toml`
- Mock client still works (`USE_MOCK_CLIENT=true`)
- Voice toggling works when Local Whisper becomes available after startup
- Speech-only and input-only voice states do not disable each other by accident
- Existing files are not overwritten by `write_file` unless overwrite is explicit
- No credentials, API keys, or personal data in any file
- Manual verification passes
- PR description explains what changed and why

## Reporting issues

Include:

- macOS version
- Python version (`python --version`)
- AI backend version if relevant (e.g. `ollama --version`)
- Relevant terminal output or logs (`~/Library/Logs/Eyra/eyra.log` by default)
- Steps to reproduce
- Relevant sanitized `.env` keys (never paste `API_KEY` or other secrets)

## Vulnerability reporting

See [SECURITY.md](SECURITY.md). Do **not** open a public issue for a security vulnerability. Use GitHub's private vulnerability reporting.
