# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.0.0] - 2026-03-08

### Added

- Voice mode: speech input and output via local-whisper (`wh listen` / `wh whisper`)
- Adaptive model routing via `ComplexityScorer` (spaCy NLP + CLIP), dispatching to Ollama or Gemini based on task complexity
- Google Gemini backend as cloud fallback for moderate and complex tasks
- In-memory screenshot and webcam capture via `mss` and OpenCV, no files written to disk

### Changed

- Local inference via Ollama replaces the OpenAI backend as the default
- Project restructured into `chat/`, `clients/`, `modes/`, and `utils/` packages

---

## [0.1.0] - 2024-12-01

- Initial release: Manual and Live modes, OpenAI backend, screenshot and webcam capture
