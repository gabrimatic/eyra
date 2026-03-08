# Contributing

Bug fixes, new modes and backends, better docs. Here's how to get involved.

## Dev Setup

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
uv sync
python -m spacy download en_core_web_sm
cp .env.example .env
```

Set `USE_MOCK_CLIENT=true` in `.env` to run without any AI backend during development.

Voice mode requires local-whisper running locally. Check with `wh status`.

## Architecture

```
eyra/
├── src/
│   ├── main.py                  # Entry point, mode selection
│   ├── chat/
│   │   ├── capture.py           # In-memory screenshot and webcam capture
│   │   ├── complexity_scorer.py # NLP + CLIP task complexity routing
│   │   ├── message_handler.py   # Message history and AI client routing
│   │   └── words.py             # Complexity indicator vocabulary
│   ├── clients/
│   │   ├── base_client.py       # BaseAIClient abstract class
│   │   └── ollama_client.py     # Ollama async HTTP client
│   ├── modes/
│   │   ├── base_mode.py         # BaseMode abstract class
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

The routing path for every request: `message_handler.py` → `complexity_scorer.py` → client selection → response streaming.

## New AI Backend

1. Create a file in `src/clients/`, e.g. `src/clients/my_client.py`
2. Subclass `BaseAIClient` from `src/clients/base_client.py`
3. Implement `generate(messages) -> AsyncIterator[str]`
4. Implement `generate_with_image(messages, image_b64) -> AsyncIterator[str]`
5. Register it in `src/chat/message_handler.py` under the appropriate complexity tier

Keep streaming behavior consistent with existing clients. Responses should yield string chunks, not complete strings.

## New Mode

1. Create a file in `src/modes/`, e.g. `src/modes/my_mode.py`
2. Subclass `BaseMode` from `src/modes/base_mode.py`
3. Implement `run()`
4. Add a menu entry in `src/main.py`

## Testing

There is no automated test suite at this time. Manual verification flow:

1. `USE_MOCK_CLIENT=true python src/main.py` — confirm all three modes start without errors
2. Manual mode: send a text prompt, confirm streamed response
3. Manual mode: send `test #image`, confirm screenshot is captured and sent
4. Live mode: run for 5 seconds, confirm loop output, interrupt with `Ctrl+C`
5. Voice mode: run mode 3, speak when prompted, confirm response is spoken back via local-whisper

For new clients, test with both text and image inputs at each complexity level.

## PR Checklist

- Code follows the style of the surrounding file (indentation, naming, structure)
- No new dependencies added without updating `requirements.txt`
- Mock client still works (`USE_MOCK_CLIENT=true`)
- No credentials, API keys, or personal data in any file
- Manual verification flow passes
- PR description explains what changed and why

## Reporting Issues

Include:

- macOS version
- Python version (`python --version`)
- Ollama version (`ollama --version`) if relevant
- Full terminal output including traceback
- Steps to reproduce
- `.env` contents (no secrets)

## Vulnerability Reporting

See [SECURITY.md](SECURITY.md). Do **not** open public issues for security vulnerabilities. Use GitHub's private vulnerability reporting.
