# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.0.0] - 2026-03-08

### Added

- Voice mode: speech input and output via local-whisper (`wh listen` / `wh whisper`)
- Adaptive model routing via `ComplexityScorer` (spaCy NLP + CLIP), dispatching to Qwen3.5 models via Ollama based on task complexity
- In-memory screenshot and webcam capture via `mss` and OpenCV, no files written to disk

### Changed

- All inference runs locally via Ollama with Qwen3.5 Q4 models (2b, 4b, 9b)
- Project restructured into `chat/`, `clients/`, `modes/`, and `utils/` packages

---

## [0.1.0] - 2024-12-01

- Initial release: Manual and Live modes, OpenAI backend, screenshot and webcam capture
