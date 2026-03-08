# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.0.0] - 2026-03-08

### Added

- Voice mode: speech input and output via local-whisper (`wh listen` / `wh whisper`)
- Adaptive model routing via `ComplexityScorer` (spaCy NLP + CLIP), dispatching to configurable models via any OpenAI-compatible provider based on task complexity
- In-memory screenshot and webcam capture via `mss` and OpenCV, no files written to disk

### Changed

- All inference via any OpenAI-compatible provider; defaults to Ollama at localhost with three model tiers configurable in `.env`
- `API_BASE_URL` and `API_KEY` replace provider-specific env vars; point at any OpenAI-compatible endpoint
- Project restructured into `chat/`, `clients/`, `modes/`, and `utils/` packages
- Conversation context sent to the AI is capped at the 10 most recent messages; full history is preserved locally for display

---

## [0.1.0] - 2024-12-01

- Initial release: Manual and Live modes, OpenAI backend, screenshot and webcam capture
