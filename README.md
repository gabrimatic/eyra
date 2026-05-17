# Eyra

[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial-lightgrey.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)]()

Eyra is a local-first voice assistant for your Mac. You can speak or type, ask it to help with local files, screen context, reminders, tasks, and safe computer-control workflows, and keep the default path on your machine.

The current release line is `4.2.1`. Full docs: [gabrimatic.github.io/eyra](https://gabrimatic.github.io/eyra/).

## For Normal Users

Install Eyra:

```bash
curl -fsSL https://gabrimatic.github.io/eyra/install.sh | bash
```

Then open the menu bar controls:

```bash
eyra menu
```

The guided installer builds and installs `Eyra.app` under `~/.local/share/eyra/` when the bundle can be prepared. `eyra menu` opens that app. If the app bundle is unavailable, use `eyra open` for the local Web control UI.

If you prefer the terminal assistant directly:

```bash
eyra
```

Useful first commands:

```bash
eyra setup
eyra status
eyra doctor
eyra examples
eyra menu
```

Inside Eyra, try:

```text
What can you control?
Are you local right now?
What would leave my machine?
Help me check if setup is ready.
Move the latest downloaded file to Documents.
Remind me in 10 minutes to stand up.
Start dictation.
```

## What Stays Local?

By default:

- Model requests use Ollama on `localhost`.
- Voice uses Local Whisper on this Mac.
- Screenshots are kept in memory.
- File access is sandboxed to allowed folders.
- No telemetry is sent.
- Network tools are off.
- Mac control tools are off.
- MCP, connectors, external agents, Realtime voice, and Web UI are off until enabled.

Ask Eyra anytime:

```text
What would leave my machine?
```

Or run:

```bash
eyra status
eyra settings
```

## When Can Data Leave My Mac?

Only when you choose a remote provider or enable an optional online/control surface.

Examples:

- A cloud `API_BASE_URL` sends model requests to that provider.
- `NETWORK_TOOLS_ENABLED=true` allows web requests.
- `REALTIME_VOICE_ENABLED=true` uses an online Realtime voice path.
- Connectors, MCP, OS tools, and external agents stay disabled until explicitly enabled and still use Eyra policy, sandboxing, and approvals.

Secrets are not printed by `eyra status`, `eyra settings`, the menu bar, or Doctor JSON.

## Menu Bar App

Run:

```bash
eyra menu
```

`eyra menu` opens the installed `Eyra.app` bundle when it is available. From a source checkout it can also run the SwiftPM developer path when Swift/Xcode command-line tools are installed.

Check the current menu state without launching it:

```bash
eyra menu --json --check
```

If the app bundle is unavailable, use:

```bash
eyra open
```

The menu bar shows:

- Local model readiness
- Voice status
- Whether the Web control service is running
- Whether the default path keeps data on this Mac
- Simple toggles for voice, speech, network tools, Mac control tools, connectors, and Realtime voice

It does not bypass approvals or enable risky tools by itself.

## Simple Settings

Most people should use:

```bash
eyra settings
eyra settings get MODEL
eyra settings set LIVE_SPEECH_ENABLED false
```

Simple settings include the main model, voice input, speech output, microphone device, allowed folders, Web UI, network tools, Mac control tools, connectors, and Realtime voice.

Advanced settings are still documented for power users, but they are not required for first use.

## For Developers

Use the source checkout path when you want to change Eyra itself:

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
chmod +x setup.sh && ./setup.sh
```

Run from source:

```bash
uv run python src/main.py
```

Run checks:

```bash
uv run pytest -q
uv run ruff check src tests scripts/certify_voice_to_computer.py
uv lock --check
bash -n setup.sh install.sh
```

Build the menu bar app from source:

```bash
swift build --package-path apps/EyraMenuBar
swift run --package-path apps/EyraMenuBar EyraMenuBar
```

## Advanced Surfaces

Eyra still includes the full power-user surface:

- Background tasks, jobs, logs, artifacts, and cancellation
- Local triggers and reminders
- Dictation and hands-free approvals
- Sandboxed file operations with undo metadata
- PDF and screen understanding
- Optional browser/network tools
- Optional Mac control tools
- Optional Web UI
- Optional Realtime voice
- Optional connectors
- Optional MCP and external agent bridges

These stay disabled unless you enable the matching setting.

## Install Paths

Recommended normal-user install:

```bash
curl -fsSL https://gabrimatic.github.io/eyra/install.sh | bash
```

The repository is currently private, so release asset installs can require authenticated GitHub access through `GITHUB_TOKEN`.

The checked-in Homebrew formula is still custom-tap preparation. It installs from `main` and should not be treated as a stable public formula until it points at a tagged release asset and pinned sha256.

Python tool installs remain available for advanced users:

```bash
uv tool install git+https://github.com/gabrimatic/eyra@v4.2.1
pipx install git+https://github.com/gabrimatic/eyra@v4.2.1
```

## Support

Start here:

```bash
eyra status
eyra doctor
eyra logs
eyra paths
```

If something is missing, Eyra should tell you the next step. Missing local AI, microphone permission, or Local Whisper is a setup item, not a failed install.

## License

Eyra is licensed under the PolyForm Noncommercial License 1.0.0.
