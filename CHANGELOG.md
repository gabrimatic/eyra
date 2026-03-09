# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
