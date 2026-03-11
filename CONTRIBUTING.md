# Contributing

Bug fixes, new tools, backend improvements, better docs. Here's how to get involved.

## Dev Setup

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
```

Set `USE_MOCK_CLIENT=true` in `.env` to run without any AI backend during development.

Voice input requires local-whisper running locally. Check with `wh status`.

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
│   │   ├── speech_controller.py # TTS/STT coordination
│   │   ├── voice_input.py      # Silero VAD recording + local-whisper transcription
│   │   └── status_presenter.py  # Session status display
│   ├── tools/
│   │   ├── base.py              # Base tool interface
│   │   ├── registry.py          # Tool registration and lookup
│   │   ├── screenshot.py        # On-demand screenshot tool
│   │   ├── time_tool.py         # Current time tool
│   │   ├── weather.py           # Weather info tool
│   │   ├── clipboard.py         # Clipboard reader tool
│   │   └── system_info.py       # System info tool
│   └── utils/
│       ├── settings.py          # .env config loader
│       ├── image_history.py     # Image context management
│       ├── sound_player.py      # Audio feedback
│       └── mock_client.py       # Mock client for development
```

The app starts a single `LiveSession` with concurrent input loops for voice and typed input. The model can call tools (like screenshot) on demand. Routing path: `message_handler.py` → `complexity_scorer.py` → quality mode override → response shaping → client selection → streaming.

## New AI Backend

1. Create a file in `src/clients/`, e.g. `src/clients/my_client.py`
2. Subclass `BaseAIClient` from `src/clients/base_client.py`
3. Implement `generate_completion_stream(messages, model_name) -> AsyncIterator[str]`
4. Implement `generate_completion_with_image_stream(messages, image_base64, model_name) -> AsyncIterator[str]`
5. Register it in `src/chat/message_handler.py` in `get_ai_client()`

Keep streaming behavior consistent with existing clients. Responses should yield string chunks, not complete strings.

## New Tool

1. Create a file in `src/tools/`, e.g. `src/tools/my_tool.py`
2. Implement the tool interface from `src/tools/base.py`
3. Register it in `src/runtime/live_session.py` inside `_build_tool_registry()`

Tools are invoked by the model on demand. Keep tool implementations stateless where possible.

## Testing

```bash
uv run pytest -q                           # Run all tests
uv run pytest tests/test_runtime.py -q     # Run a single test file
uv run pytest tests/test_runtime.py -k "test_name" -q  # Run a single test
ruff check src/                            # Lint
```

Manual verification flow:

1. `USE_MOCK_CLIENT=true uv run python src/main.py` — confirm app starts as a live session
2. Type a prompt, confirm streamed response
3. Speak a prompt (requires local-whisper), confirm voice response
4. `/status` — confirm current state is displayed
5. `/clear` — confirm session is reset

## PR Checklist

- Code follows the style of the surrounding file (indentation, naming, structure)
- No new dependencies added without updating `pyproject.toml`
- Mock client still works (`USE_MOCK_CLIENT=true`)
- No credentials, API keys, or personal data in any file
- Manual verification flow passes
- PR description explains what changed and why

## Reporting Issues

Include:

- macOS version
- Python version (`python --version`)
- AI backend version if relevant (e.g. `ollama --version`)
- Full terminal output including traceback
- Steps to reproduce
- `.env` contents (no secrets)

## Vulnerability Reporting

See [SECURITY.md](SECURITY.md). Do **not** open public issues for security vulnerabilities. Use GitHub's private vulnerability reporting.
