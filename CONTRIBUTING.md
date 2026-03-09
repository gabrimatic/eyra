# Contributing

Bug fixes, new modes and backends, better docs. Here's how to get involved.

## Dev Setup

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
```

Set `USE_MOCK_CLIENT=true` in `.env` to run without any AI backend during development.

Voice mode requires local-whisper running locally. Check with `wh status`.

## Architecture

```
eyra/
├── src/
│   ├── main.py                  # Entry point, unified session loop
│   ├── chat/
│   │   ├── capture.py           # In-memory screenshot and webcam capture
│   │   ├── complexity_scorer.py # Deterministic prompt routing
│   │   ├── message_handler.py   # Model selection, response shaping, streaming
│   │   └── session_state.py     # Shared in-memory session state
│   ├── clients/
│   │   ├── base_client.py       # BaseAIClient abstract class
│   │   └── ai_client.py         # OpenAI-compatible async client
│   ├── modes/
│   │   ├── base_mode.py         # BaseMode abstract class
│   │   ├── manual_mode.py       # Text interaction + command handling
│   │   ├── live_mode.py         # Watch mode (continuous screen analysis)
│   │   └── voice/
│   │       └── voice_mode.py    # Voice pipeline (STT + LLM + TTS)
│   └── utils/
│       ├── settings.py
│       ├── image_history.py
│       ├── sound_player.py
│       └── mock_client.py
```

The routing path for every request: `message_handler.py` → `complexity_scorer.py` → quality mode override → response shaping → client selection → streaming.

## New AI Backend

1. Create a file in `src/clients/`, e.g. `src/clients/my_client.py`
2. Subclass `BaseAIClient` from `src/clients/base_client.py`
3. Implement `generate_completion_stream(messages, model_name) -> AsyncIterator[str]`
4. Implement `generate_completion_with_image_stream(messages, image_base64, model_name) -> AsyncIterator[str]`
5. Register it in `src/chat/message_handler.py` in `get_ai_client()`

Keep streaming behavior consistent with existing clients. Responses should yield string chunks, not complete strings.

## New Interaction Style

1. Create a file in `src/modes/`, e.g. `src/modes/my_mode.py`
2. Subclass `BaseMode` from `src/modes/base_mode.py`
3. Implement `run()` — return the next style string ('text', 'watch', 'voice') or None to exit
4. Accept `session: SessionState` and use it for shared state
5. Add the style to the session loop in `src/main.py`

## Testing

There is no automated test suite at this time. Manual verification flow:

1. `USE_MOCK_CLIENT=true uv run python src/main.py` — confirm app starts in text mode
2. Text mode: send a text prompt, confirm streamed response
3. Text mode: send `test #image`, confirm screenshot is captured and sent
4. Text mode: `/watch` to start watch mode, confirm loop output, `Ctrl+C` to return to text
5. Text mode: `/voice` to enter voice mode, speak when prompted, confirm response via local-whisper
6. `/mode best` then a prompt — confirm strongest model is used
7. `/status` — confirm current state is displayed
8. `/clear` — confirm session is reset
9. `/retry` — confirm last request is re-sent

For new clients, test with both text and image inputs at each complexity level.

## PR Checklist

- Code follows the style of the surrounding file (indentation, naming, structure)
- No new dependencies added without updating `pyproject.toml` and `requirements.txt`
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
