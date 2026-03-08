# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.0.0] - 2026-03-08

### Added

- `ComplexityScorer` using spaCy NLP: vocabulary richness, syntactic depth, named entity density
- CLIP-based image complexity scoring for visual tasks
- Adaptive routing: phi3 and llava for simple tasks, gemini-1.5-flash for moderate and complex
- Google Gemini backend (`src/clients/google_client.py`) as cloud fallback
- Three distinct modes: Manual, Live, Voice, each in their own module under `src/modes/`
- Voice pipeline: hold-Space recording, local Whisper STT (`tiny.en.pt` bundled), Coqui TTS output
- Sentence-buffered TTS: generates and plays sentence by sentence for lower latency
- pyttsx3 fallback when Coqui TTS fails to initialize (`VOICE_TTS_FALLBACK`)
- In-memory screenshot capture via `mss`, no files written to disk
- Webcam capture via OpenCV with AVFoundation backend and warm-up frames
- `BaseAIClient` abstract class for consistent client interface
- `BaseMode` abstract class for consistent mode interface
- `MockClient` for development without a running AI backend (`USE_MOCK_CLIENT`)
- `.env` configuration for all runtime settings
- `setup.sh` for one-command environment setup

### Changed

- Replaced model selection by name with complexity-based routing
- Image capture moved fully in-memory (previously wrote temporary files)
- Voice model path moved to `src/modes/voice/models/` and made configurable via `VOICE_MODEL_PATH`
- Project restructured under `src/` with dedicated `chat/`, `clients/`, `modes/`, and `utils/` packages
- README rewritten to reflect current architecture and configuration
