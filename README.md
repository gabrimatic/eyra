# Eyra

[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial-lightgrey.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)]()

Eyra is a local-first voice agent for the macOS terminal.

Speak or type. Eyra routes the request to an OpenAI-compatible model, calls local tools when needed, and speaks back through Local Whisper. Long work runs as owned background tasks, so the main coordinator stays available for quick questions, status, and cancellation. The default path stays on your machine: Ollama at localhost, Silero VAD in process, screenshots in memory, local PDFs/files, no telemetry.

Cloud providers, network-backed tools, full OS command tools, MCP bridges, universal connectors, external agent delegation, Realtime voice, and the Web UI are opt-in. Current `master` contains unreleased post-4.1.0 routing and voice hardening intended for a future 4.2.0 release candidate.

<p align="center"><img src="screenshot.png" width="800" alt="Eyra terminal screenshot"></p>

---

## Quick start

Runtime: macOS on Apple Silicon, Python 3.11+, Homebrew, and an OpenAI-compatible AI provider. Default provider: [Ollama](https://ollama.com) at `localhost:11434`.

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
chmod +x setup.sh && ./setup.sh
```

Setup creates `.env`, installs dependencies, checks your backend and models, checks Local Whisper for voice, and registers the `eyra` command.

Run from the repo:

```bash
uv run python src/main.py
```

After your shell reloads `~/.local/bin`, the short command works too:

```bash
eyra
```

Useful install and support commands:

```bash
eyra setup
eyra doctor
eyra doctor --json
eyra certify
eyra web
eyra version
eyra paths
eyra update
eyra uninstall --dry-run
```

`eyra setup` preserves an existing `.env`. Source checkouts use the repo `.env`; installed tool environments use `~/.config/eyra/.env`, and the repo `.env` still wins when you run from an Eyra source checkout. Eyra does not read unrelated project `.env` files from arbitrary current directories. `eyra update` only explains the correct update path for the detected install source; it does not pull, overwrite, or delete user data. `eyra uninstall` removes Eyra-created command shims and preserves `.env`, jobs, triggers, logs, and the operation ledger unless you explicitly choose data removal.

For CI or temp-home smoke tests, use `./setup.sh --non-interactive`. Non-interactive setup does not start Local Whisper or other services; it reports what is missing and leaves service startup to the user.

Eyra runs preflight checks, then enters a live session:

```
Eyra

  Voice: input + speech    Backend: ready

  Type anything or speak. /help for commands.
```

Eyra is now listening. Type or speak without leaving the session.

Optional phone/browser access:

```bash
WEB_UI_ENABLED=true uv run python -m web.server
```

Eyra runs the same backend, model, voice, screen, and capability preflight before serving the Web UI. Then open the tokenized URL printed by Eyra. Set `WEB_UI_HOST=0.0.0.0` only when you intentionally want access from other devices on your network.
By default, every non-health Web UI endpoint requires a session token, including localhost. Keep that URL private.

For one shared terminal and browser runtime, start the terminal app with Web enabled:

```bash
WEB_UI_ENABLED=true eyra
```

That shared Web frontend uses the terminal-owned approvals, jobs, task events, trigger state, tools, browser session, and operation history. Running `eyra-web` or `uv run python -m web.server` directly still starts a standalone Web runtime and reports that mode in `/api/health`.

### Future install paths

The source setup path above is the supported private-beta/developer path today.

Future GitHub Release installer:

```bash
curl -fsSL https://raw.githubusercontent.com/gabrimatic/eyra/master/install.sh | bash
```

If this repository is private, that command needs authenticated GitHub access, for example `GITHUB_TOKEN` in the environment. It is not a public install path until the repository or release asset is public.

Future custom Homebrew tap path:

```bash
brew tap gabrimatic/eyra
brew install eyra
```

or:

```bash
brew install gabrimatic/eyra/eyra
```

Eyra uses the PolyForm Noncommercial license, so the intended Homebrew path is a custom tap, not `homebrew/core`, unless the license and release policy change. The checked-in formula is a private-beta scaffold that installs from `master` until a signed/tagged release asset and checksum exist. Treat it as tap preparation, not a public stable formula.

Future Python tool paths, once package distribution is enabled:

```bash
uv tool install git+https://github.com/gabrimatic/eyra@v4.2.0rc1
pipx install git+https://github.com/gabrimatic/eyra@v4.2.0rc1
```

Those installs should expose the same support commands as source installs. Run `eyra doctor --json` after installing to confirm local backend, models, Local Whisper, microphone, screen capture, sandbox paths, Web UI config, and optional tool flags.

---

## What it does

- Live session: one terminal process stays open for typed and spoken input.
- Responsive coordinator: typed and voice input remain available while workers handle long tasks.
- Background tasks: long PDF, file, screen, website, and multi-tool work has an id, lifecycle, progress, final result, failure, or cancellation. Jobs, logs, and operation ledger entries are persisted locally in SQLite.
- Local triggers: one-time “when this file appears, move it”, “remind me in 10 minutes to …”, and recurring “every 30 minutes remind me to …” triggers run as bounded background tasks, with definitions and status persisted locally.
- Voice: Local Whisper handles transcription and speech; Silero VAD decides when you finished speaking. `/voice-diagnose` checks the selected microphone, all-zero audio, Local Whisper ASR, generated WAV transcription, and the local socket path.
- Model routing: one configured model by default, with experimental complexity routing when you turn it on.
- Local tools: screenshot, time, clipboard, system info, frontmost app, sandboxed Finder selection, PDF text extraction, and sandboxed filesystem access through function calling.
- Optional OS tools: command execution, URL fetch, process listing, system snapshots, accessibility-tree snapshots, local OCR screen text extraction, file metadata/search, LaunchAgent status/management, app opening, notifications, approved Shortcut execution, and clipboard writes. These stay off until `OS_TOOLS_ENABLED=true` or `NETWORK_TOOLS_ENABLED=true` for URL/network work.
- Optional UI actions: approved coordinate click, scroll, drag, focused text entry, and hotkey tools are available only when `OS_TOOLS_ENABLED=true`.
- Optional app/window control: list visible apps, list app windows, activate apps, quit apps with approval, and apply approved window actions such as close, minimize, zoom, fullscreen, move, and resize when `OS_TOOLS_ENABLED=true`.
- Optional MCP tools: list stdio MCP servers from `MCP_CONFIG_PATH` and call tools only after action-specific approval.
- Optional connectors: attach CLI, MCP, local HTTP, browser-agent, coding-agent, or explicitly opted-in remote workers from a structured manifest. Eyra validates the manifest, tracks acceptance state, enforces sandbox and privacy policy, requires approval for risky work, runs jobs with timeout/cancellation/output caps, redacts output, and records logs/artifacts.
- Optional agent delegation: inspect Codex/OpenClaw availability and sessions, read bounded redacted session content, use Codex/OpenClaw-compatible tool names, and hand complex work to terminal agents when `AGENT_TOOLS_ENABLED=true`. BYO agents use static argv from `EXTERNAL_AGENT_CONFIG_PATH`, sandboxed cwd, bounded timeouts, capped redacted output, and exact approval.
- Coding jobs: when agent tools are enabled, “Start a coding job with Codex to …” creates an owned background task, waits for server-side approval, then runs the bounded terminal-agent bridge.
- Dictation: “Start dictation”, “End dictation”, and “Cancel dictation” capture text locally without model routing. Dictation can save directly to a sandboxed file and supports simple “Literal …” spelling for filenames, codes, and exact text.
- Corrections: after a failed direct file move/copy/remove, “No, I meant …” can safely retry the same local action with the corrected target name.
- Operator loop evidence: direct file moves record observe → plan → act → verify → recover details in the local operation ledger, including post-action checks and recovery guidance when verification fails.
- Optional Web UI: a compact browser interface for text chat, event-driven task status, task cancellation, Local Whisper voice turns, and local voice feedback from a phone or another system. When launched from `eyra`, it shares the terminal runtime; `eyra-web` remains standalone.
- Optional Realtime voice: online browser voice can use OpenAI Realtime when `REALTIME_VOICE_ENABLED=true` and an OpenAI key is configured.
- Filesystem safety: common folders are available by default, access stays sandboxed, existing files are not overwritten unless the user explicitly confirms or approves the exact overwrite, binary reads and edits return a clean message, moves/copies/renames/duplicates check destination conflicts, append/prepend/compare are bounded text-file operations, zip compression/extraction stays inside the sandbox, and remove/delete requests move items to macOS Trash by default.
- Capability and privacy reporting: ask “What can you control?”, “Are you local right now?”, or “What would leave my machine?” to get a local runtime snapshot of model, voice, screen, filesystem, Web, Realtime, network, OS, MCP, and agent capability state.
- Network tools: weather and browser access stay disabled until `NETWORK_TOOLS_ENABLED=true`.
- Provider support: Ollama, LM Studio, vLLM, OpenRouter, Groq, OpenAI, or any compatible `/v1/chat/completions` endpoint.
- Image handling: screenshots stay in memory and are never written to disk.
- Mock mode: `USE_MOCK_CLIENT=true` starts Eyra without a backend for development and smoke tests.

---

## How it works

Eyra runs one live session with a typed channel, a voice channel, a coordinator, and a background task manager.

- Voice: [Local Whisper](https://github.com/gabrimatic/local-whisper) handles ASR through Qwen3-ASR and TTS through Kokoro. Eyra records microphone audio with sounddevice, classifies 32ms frames with Silero VAD, and transcribes after a pause. Set `VOICE_INPUT_DEVICE` to an input device index or name when the system default is wrong.
- Interruption: Eyra can stop speech output immediately through the shared interrupt path; run `/voice-test` for the physical microphone interruption check on your Mac.
- Hands-free status: say “Stop” to interrupt speech output, or “Show status” to read the local runtime status without typing `/status`.
- Coordinator: quick local intents such as task status, cancellation, disabled-network refusals, time checks, and common file actions are handled immediately.
- Background workers: long or tool-heavy requests are accepted as tasks and run through a worker pipeline. Each task keeps metadata: id, title, original request, state, progress, result, error, tool/network/filesystem/vision flags, and cancellation state.
- Runtime recovery: `/voice on` rechecks Local Whisper and enables whichever side is ready. ASR and TTS are tracked separately, so speech can keep working while microphone input is unavailable or still loading.
- Typed input: keyboard input is always available and feeds the same conversation as voice.
- Tool use: the model can call local function tools for screenshot, time, clipboard, system info, macOS context, PDF extraction, and filesystem work. OS command tools, UI actions, MCP bridges, connector jobs, agent session inspection, agent delegation, weather, URL fetch, and browser tools are available only after you opt in.
- Connectors: Eyra is not trying to rebuild every external agent or automation system. Connectors let those systems run as workers under Eyra policy. Eyra owns voice UX, route policy, approvals, privacy boundaries, jobs, logs, artifacts, operation ledger entries, cancellation, certification, and user-facing status.
- Web UI: `WEB_UI_ENABLED=true eyra` starts a browser frontend attached to the terminal-owned runtime. `eyra-web` runs the same preflight checks as a standalone Web runtime. Both use the shared intent rules and tool registry, persist jobs and triggers to the same local stores when configured paths match, and serve a small local UI with connector status, connector acceptance tests, task, job-log, artifact, trigger pause/resume/cancel, approval, auth, and event-driven task APIs.
- Realtime voice: browser Realtime voice is an online option. Eyra mints server-side ephemeral client secrets and never puts the standard OpenAI API key in browser code. Realtime tools are off by default and use a small allowlist when explicitly enabled.
- Model routing: complexity routing is experimental and off by default. When enabled, `ComplexityScorer` dispatches requests to Simple, Moderate, or Complex tiers.
- File actions: common move, copy, rename, duplicate, create, overwrite, trash, restore, zip/unzip, and direct read requests use deterministic sandboxed tool calls before involving the model. Existing files are protected until you explicitly say to overwrite, and irreversible deletion requires exact approval.
- Operation ledger: direct local file changes are recorded with target, action type, risk level, result, and undo metadata where possible. Ask “What changed?” or run `/operations` to inspect recent changes.
- Operator verification: direct moves verify that the source was removed and the destination exists, and failed moves keep recovery guidance for correction or retry.
- Undo: “Undo that” reverses recent reversible file operations, including direct moves, Trash moves, and created copies.
- Capability snapshot: `/capabilities` and matching natural phrases report current control surfaces and the privacy boundary without asking the model.
- Context snapshot: `/context` and “What is happening?” report the current goal, working directory, recent jobs, and recent changes from local state.
- Voice approvals: “Approve that”, “Reject that”, “yes”, and “no” resolve a single pending approval locally. If more than one approval is pending, Eyra reads the ids and asks you to choose.
- Voice options: when a direct file request matches multiple local targets, Eyra reads numbered options. Say “Read the options” to repeat them or “Choose number two” to continue hands-free.
- Reference grounding: “Move the latest downloaded file to Documents” resolves the newest file in Downloads locally, records the operation, and keeps undo metadata.
- Named folders: deterministic file commands understand Desktop, Documents, Downloads, Pictures, Movies, Music, and `/tmp`; sandbox roots still decide which of those paths are allowed.
- Pause/resume: “Pause that” pauses the latest queued task before it starts, and “Resume that” resumes the latest paused task.
- Triggers: say “When report.pdf appears in my Downloads, move it to Documents.”, “Remind me in 10 minutes to stretch.”, or “Every 30 minutes remind me to stretch.” Inspect triggers with `/triggers` and manage them with `/trigger pause|resume|cancel <id>`.
- Coding jobs: say “Start a coding job with Codex to update the README.” to create an approved, cancellable terminal-agent task. Ask “What is the coding agent doing?” for status.
- Connector jobs: run `/connectors`, `/connector openclawnew`, `/connector test openclawnew`, or `/connector run openclawnew inspect this folder`. Natural phrases such as “What connectors do I have?”, “Can you use OpenClawNew?”, “Ask OpenClawNew to inspect this folder”, and “Cancel the OpenClawNew job” are handled by the coordinator.
- Dictation: say “Start dictation” to capture text locally, or “Start dictation to a file named note.txt in my Documents.” to save the final text when you say “End dictation.” Use “Cancel dictation” to discard it.
- Corrections: if a direct file target was wrong and the action failed, say “No, I meant correct-file.txt” to retry that same local action with the corrected name.
- Triggers: say “When report.pdf appears in my Downloads, move it to Documents”, “Remind me in 10 minutes to stretch”, or “Every 30 minutes remind me to stretch.” Use `/triggers` and `/trigger pause|resume|cancel <id>` to manage local triggers.
- PDF handling: PDF workers extract text locally first, then summarize the extracted text. If no embedded text is available, Eyra reports that the PDF appears scanned or image-only.
- Screen handling: screen requests are controller-owned. Eyra captures the screenshot locally, keeps it in memory, then sends it to `VISION_MODEL` (or `MODEL` when `VISION_MODEL` is empty). The vision model does not need native tool calling.
- Browser actions: when network tools are enabled, Eyra can open pages, click elements, take page screenshots, fill form fields without submitting them, download files after approval to a sandboxed destination, and upload sandboxed local files only after approval.
- Tool fallback: deterministic controller actions such as time checks, task commands, common file moves/copies/creates/reads, PDF extraction, and screen capture can run without native model tool calling. Open-ended model-driven tool loops still require a tool-capable model and fail clearly when the selected model cannot call tools.
- Planning: common voice-to-computer requests are normalized into typed task specs with target references, required context, capabilities, actions, risk, verification, and rollback metadata before local execution when a deterministic plan applies.
- Privacy boundary: capability snapshots include action-specific decisions for model calls, network tools, and Realtime voice, including whether data leaves the machine, what data class leaves, where it goes, and whether the path is allowed by current settings.
- Approvals: risky OS, LaunchAgent, clipboard, command, MCP-call, agent-delegation, and model-driven overwrite actions use server-side, action-specific approvals. A model-provided `confirmed=true` value is ignored.
- Connector approvals: connector manifests cannot self-approve. File-writing, UI-control, shell-capable, remote, and delegated-agent connector jobs create exact server-side approvals before execution.

### Preflight

Startup checks:

- Backend reachability (tries `/v1/models`, falls back to Ollama `/api/tags`)
- Every configured model exists (auto-pulls via Ollama if needed)
- Ollama model capabilities, with a warning when the selected model does not advertise native tool calling
- [Local Whisper](https://github.com/gabrimatic/local-whisper) for voice input and speech output (`brew tap gabrimatic/local-whisper && brew install local-whisper`). Input and speech are tracked separately so one side can keep working if the other is unavailable.
- Screen capture (macOS built-in)

The session does not start until the backend and models are confirmed ready.
When `USE_MOCK_CLIENT=true`, backend and model checks are skipped on purpose so you can smoke-test the session without a running provider.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/voice on\|off` | Toggle voice input and speech output, with runtime recovery |
| `/handsfree on\|off` | Make no-hands operation explicit in status and capabilities |
| `/voice-diagnose` | Run local microphone, VAD, and Local Whisper diagnostics |
| `/voice-test` | Start the manual voice interruption diagnostic |
| `/mute` | Mute speech output only |
| `/unmute` | Unmute speech |
| `/goal <text>` | Set session context that guides future replies |
| `/mode fast\|balanced\|best` | Set quality mode |
| `/status` | Show current runtime state |
| `/capabilities` | Show current control surfaces and privacy boundary |
| `/context` | Show current local context, recent jobs, and recent changes |
| `/tasks` | Show active tasks and recent completed, failed, or cancelled tasks |
| `/tasks clear-completed` | Clear completed, failed, and cancelled task/job rows |
| `/task <id>` | Show detailed task state, progress, result, and error |
| `/task logs <id>` | Show durable local job logs |
| `/task artifacts <id>` | Show durable local job artifacts |
| `/task retry <id>` | Retry a failed, cancelled, or blocked deterministic local job |
| `/operations` | Show recent local operation ledger entries |
| `/triggers` | Show local trigger definitions and status |
| `/trigger pause\|resume\|cancel <id>` | Pause, resume, or cancel a local trigger |
| `/connectors` | Show configured connectors and acceptance state |
| `/connector <id>` | Show one connector capability and privacy snapshot |
| `/connector test <id>` | Run local acceptance checks for one connector |
| `/connector enable <id>` | Enable one connector for this session |
| `/connector disable <id>` | Disable one connector for this session |
| `/connector run <id> <task>` | Run a connector job through Eyra policy |
| `/cancel <id>` | Cancel a queued or running task |
| `/cancel all` | Cancel all queued or running tasks |
| `/pause <id>` | Pause a queued task before it starts |
| `/resume <id>` | Resume a paused queued task |
| `/approvals` | Show pending risky-action approvals |
| `/approve <id>` | Approve one exact pending action |
| `/reject <id>` | Reject one pending action |
| `/clear` | Reset conversation history |
| `/quit` | Exit |

Unknown commands are caught locally and never sent to the model.

Hands-free control phrases are handled locally: “read the options,” “choose number two,” “approve that,” “reject that,” “undo that,” “stop,” “cancel that,” “pause that,” “resume that,” “show status,” “what are you doing,” “what changed,” “start dictation,” and “end dictation.”

To attach the Web UI to the terminal-owned runtime:

```bash
WEB_UI_ENABLED=true eyra
```

To run a standalone Web runtime:

```bash
WEB_UI_ENABLED=true uv run python -m web.server
```

After `setup.sh`, the shortcut is:

```bash
WEB_UI_ENABLED=true eyra-web
```

---

## Quality modes

Set the speed and quality trade-off with `/mode`:

| Mode | Behavior |
|------|----------|
| `fast` | Uses the smallest model when complexity routing is enabled. If routing is off, Eyra says fast mode is unavailable instead of pretending to switch. |
| `balanced` | Lets the router decide (default) |
| `best` | Uses the strongest model |

---

## Complexity routing

Complexity routing is **optional and off by default**. When disabled (`COMPLEXITY_ROUTING_ENABLED=false`), local policy routing still plans every request, and model execution falls back to the single `MODEL` setting. `/mode fast` is available only when complexity routing is enabled because the simple-tier model is validated only in tiered mode.

When enabled (`COMPLEXITY_ROUTING_ENABLED=true`), every request in `balanced` mode is scored by `ComplexityScorer` before dispatch.

Complexity and the old `costly` flag are not safety boundaries. They help choose model effort and expensive tool exposure, but privacy, sandboxing, approvals, optional capability settings, and the local policy router decide what can actually run.

Scoring factors:

- Pattern matching for common prompt types
- Weighted signal scoring (reasoning cues, code/debug cues, domain terms)
- Prompt length and constraint analysis
- Follow-up context from recent messages

| Score | Model |
|-------|-------|
| Simple | `SIMPLE_MODEL` |
| Moderate | `MODERATE_MODEL` |
| Complex | `MODEL` |

Set model names in `.env`. Any model supported by your provider works.

## Local policy routing

Eyra plans each user request locally before model execution. The route separates execution class, effort estimate, required capabilities, model capability, tool allowlist, risk tier, privacy boundary, fallback message, and debug trace.

Policy routing is deterministic and local-first. It does not call a router model, use embeddings, add telemetry, add analytics, or silently fall back to network tools or remote providers. If `API_BASE_URL` points to a remote provider because you configured it, `/route last` and route traces say that local prompt/tool context may leave the machine.

---

## Configuration

<details><summary><strong>.env reference</strong></summary>

```env
# Provider: any OpenAI-compatible endpoint.
API_BASE_URL=http://localhost:11434/v1
API_KEY=ollama        # Keep this for local providers; set a real key for cloud providers.

USE_MOCK_CLIENT=false

# Default model for all requests when complexity routing is off.
MODEL=gemma4:e4b
# Vision model for screen/image tasks. Empty means use MODEL.
VISION_MODEL=

# Tier models used only when COMPLEXITY_ROUTING_ENABLED=true.
SIMPLE_MODEL=qwen3.5:2b
MODERATE_MODEL=gemma4:e4b

# Live runtime settings.
AUTO_PULL_MODELS=true
LIVE_LISTENING_ENABLED=true
LIVE_SPEECH_ENABLED=true
SPEECH_COOLDOWN_MS=3000
VOICE_INPUT_DEVICE=               # Optional sounddevice input device index or name.
VOICE_SAMPLE_RATE=16000           # Microphone sample rate used for VAD and WAV probes.
VOICE_DEBUG_RECORD_SECONDS=3      # Bounded local capture length for /voice-diagnose.
VOICE_DIAGNOSTIC_SAVE_AUDIO=false # Save diagnostic audio under ~/Library/Application Support/Eyra/diagnostics.
VOICE_SILENCE_MS=1500          # Silence after speech before processing (ms).
VOICE_VAD_THRESHOLD=0.15       # Silero VAD sensitivity (0.0-1.0, higher = stricter)

# Background tasks.
BACKGROUND_TASKS_ENABLED=true
MAX_BACKGROUND_TASKS=2
WORKER_MODEL=                  # Empty means use MODEL.
TASK_TIMEOUT_SECONDS=300
MAX_WORKER_TOOL_STEPS=8
TOOL_TIMEOUT_SECONDS=30
MODEL_CONCURRENCY=1
TASK_STATUS_UPDATES=true
JOB_STORE_PATH=~/.local/share/eyra/jobs.sqlite3
TRIGGER_STORE_PATH=~/.local/share/eyra/triggers.sqlite3
TRIGGER_CHECK_INTERVAL_SECONDS=0.5
TRIGGER_TIMEOUT_SECONDS=300

# Optional network tools. Keep false for the local-first default.
NETWORK_TOOLS_ENABLED=false

# Optional OS, agent, MCP, and network tools. Keep false for the local-first default.
OS_TOOLS_ENABLED=false
# Optional local OCR command for screen text extraction. Must read PNG bytes from stdin.
SCREEN_OCR_COMMAND=
AGENT_TOOLS_ENABLED=false
EXTERNAL_AGENT_TOOLS_ENABLED=false
EXTERNAL_AGENT_CONFIG_PATH=~/.config/eyra/agents.json
MCP_TOOLS_ENABLED=false
MCP_CONFIG_PATH=~/.config/eyra/mcp.json

CONNECTORS_ENABLED=false
CONNECTORS_CONFIG_PATH=~/.config/eyra/connectors.json
CONNECTORS_ALLOWED_ROOTS=        # Empty means use FILESYSTEM_ALLOWED_PATHS.
CONNECTORS_TIMEOUT_SECONDS=600
CONNECTORS_OUTPUT_CAP_BYTES=32768
CONNECTORS_ALLOW_REMOTE=false
CONNECTORS_ALLOW_PYTHON_MODULE=false

# Optional Web UI.
WEB_UI_ENABLED=false
WEB_UI_HOST=127.0.0.1
WEB_UI_PORT=8765
WEB_UI_TOKEN=
WEB_UI_REQUIRE_TOKEN=auto
WEB_UI_MAX_REQUEST_BYTES=1000000

# Optional online Realtime voice.
REALTIME_VOICE_ENABLED=false
REALTIME_MODEL=gpt-realtime
REALTIME_VOICE=marin
OPENAI_API_KEY=
REALTIME_TOOLS_ENABLED=false
REALTIME_ALLOWED_TOOLS=

# Optional log path. Default: ~/Library/Logs/Eyra/eyra.log on macOS.
# EYRA_LOG_FILE=~/Library/Logs/Eyra/eyra.log

# Optional model-tier routing. Local policy routing is always on.
COMPLEXITY_ROUTING_ENABLED=false
ROUTING_DEBUG=false
HANDS_FREE_MODE=false

# Filesystem sandbox: comma-separated allowed root paths.
FILESYSTEM_ALLOWED_PATHS=~/Documents,~/Desktop,~/Downloads,/tmp
# Relative file paths are resolved under this directory, then checked against the sandbox.
FILESYSTEM_DEFAULT_PATH=~/Documents
```

Configured external agents use JSON like:

```json
{
  "agents": [
    {
      "name": "openhands",
      "type": "cli",
      "command": ["openhands", "run"],
      "cwdPolicy": "filesystem_default_path",
      "network": false,
      "mutatesFiles": true,
      "requiresApproval": true,
      "timeoutSeconds": 600
    }
  ]
}
```

Use static argv only. Eyra does not accept dynamic model-selected commands.

Universal connectors use JSON like:

```json
{
  "connectors": [
    {
      "id": "openclawnew",
      "displayName": "OpenClawNew",
      "type": "cli",
      "enabled": true,
      "command": ["openclawnew", "run", "--json"],
      "cwdPolicy": "filesystem_default_path",
      "inputMode": "stdin_json",
      "outputMode": "stdout_json",
      "local": true,
      "canUseNetwork": false,
      "canReadFiles": true,
      "canMutateFiles": true,
      "canControlUI": false,
      "canRunShell": false,
      "requiresApproval": true,
      "riskTier": "delegated_agent",
      "timeoutSeconds": 600,
      "outputCapBytes": 32768,
      "allowedTools": [],
      "deniedTools": ["delete_permanently", "run_command"],
      "privacy": {
        "dataSent": ["task", "selected_files", "cwd"],
        "destination": "local_process",
        "leavesMachine": false
      },
      "acceptance": {
        "healthCommand": ["openclawnew", "--version"],
        "testTask": "Print a short status and exit without modifying files.",
        "expectedOutputContains": "status",
        "requiresHumanApproval": true
      }
    }
  ]
}
```

Supported connector types are `cli`, `mcp`, `http_local`, `http_remote`, `python_module`, `browser_agent`, and `coding_agent`. `python_module` requires `CONNECTORS_ALLOW_PYTHON_MODULE=true`. Remote connectors require `CONNECTORS_ALLOW_REMOTE=true`; other network-capable connectors require either `NETWORK_TOOLS_ENABLED=true` or the explicit remote connector opt-in. CLI-like connectors must use static argv. Eyra refuses shell interpolation, model-filled command strings, cwd outside the connector sandbox, missing privacy declarations, missing risk tier, invalid timeout/output caps, and privacy/capability mismatches.

Connector CLI checks:

```bash
eyra connectors validate
eyra connectors test openclawnew
eyra connectors list --json
eyra-connectors validate
```

Every connector starts as configured, available, disabled, or validation-failed, then must pass local acceptance before it is usable. Acceptance checks cover manifest schema, id, static transport, executable or endpoint availability, sandboxed cwd, timeout, output cap, privacy declaration, risk tier, approval policy, health check, test task, output redaction, cancellation where supported, and forbidden capability mismatches.

`API_BASE_URL` accepts any OpenAI-compatible endpoint: Ollama (default), LM Studio, vLLM, OpenRouter, Groq, or OpenAI itself. Local providers ignore `API_KEY`; cloud providers require it.

`VISION_MODEL` lets you keep a tool-capable text model and a separate vision model. Example: `MODEL=qwen3:4b` for normal local tool work and `VISION_MODEL=gemma3:4b` for screen questions. If `API_BASE_URL` points to a remote provider, screenshots sent to `VISION_MODEL` leave the machine because you configured that provider.

`SCREEN_OCR_COMMAND` is optional and local-only. When set, `extract_screen_text` captures a screenshot in memory, sends PNG bytes to that command on stdin, and returns the command's text output. Leave it empty unless you have installed a local OCR command that supports stdin.

`WEB_UI_REQUIRE_TOKEN=auto` means all non-health Web UI endpoints require a token. `WEB_UI_REQUIRE_TOKEN=false` is allowed only on localhost. `0.0.0.0` or any non-localhost bind always requires a token. `WEB_UI_TOKEN` can provide your own high-entropy token; if empty, Eyra generates a session token at startup.

`WEB_UI_MAX_REQUEST_BYTES` applies to chat requests, task APIs, and browser audio uploads. Raise it only when you intentionally want longer browser voice turns or larger local requests.

`REALTIME_VOICE_ENABLED=true` is an online mode. It requires `OPENAI_API_KEY` and uses server-minted ephemeral client secrets. `REALTIME_TOOLS_ENABLED=false` keeps local tools away from the remote Realtime model by default; if enabled, `REALTIME_ALLOWED_TOOLS` can expose specific low-risk tools by name. Risky local tools are not exposed to Realtime even if listed there.

</details>

---

## Privacy

Default behavior: no telemetry, no analytics, no remote browsing, and no remote weather calls.

| Component | Where it runs |
|-----------|--------------|
| AI backend | `API_BASE_URL` (default: localhost:11434) |
| Silero VAD | ONNX model, in-process, local |
| Voice recording | sounddevice (PortAudio), in-process, local |
| wh transcribe (local-whisper) | Subprocess, local |
| wh whisper (local-whisper) | Subprocess, local |
| Screenshots | In-memory; never written to disk; sent only to the configured vision model for screen tasks |
| Screen OCR | Disabled by default; when configured, captures the screen in memory and pipes PNG bytes to a local OCR command |
| macOS context | Frontmost app and sandbox-filtered Finder selection, local only |
| Accessibility tree | Disabled by default; local System Events snapshot only when `OS_TOOLS_ENABLED=true` and macOS permissions allow it |
| PDF extraction | Local files under the filesystem sandbox; text extraction only, no online OCR |
| Background task state | Local SQLite job store plus in-memory active task state |
| Trigger definitions | Local SQLite trigger store |
| OS command tools | Disabled by default; local only when enabled |
| MCP stdio tools | Disabled by default; local server processes from `MCP_CONFIG_PATH` |
| Connectors | Disabled by default; structured workers from `CONNECTORS_CONFIG_PATH`; remote connectors require explicit opt-in |
| Agent session tools | Disabled by default; read bounded, redacted local Codex/OpenClaw session files when enabled |
| Agent delegation | Disabled by default; local terminal agent commands when enabled |
| Realtime voice | Disabled by default; contacts OpenAI only when enabled and used; standard API key stays server-side |
| Web UI | Disabled by default; localhost-only by default; token required for non-localhost binds |
| Weather/browser tools | Disabled by default; contact remote sites only when `NETWORK_TOOLS_ENABLED=true` and a tool is used. Weather requires an explicit location and does not use remote IP geolocation. |
| Browser downloads | Disabled by default with browser tools; when enabled, each download needs server-side approval and saves only under the filesystem sandbox. |
| Browser uploads | Disabled by default with browser tools; when enabled, each upload needs server-side approval and can attach only sandboxed local files. |

Data leaves your machine only when you choose a remote AI provider, enable Realtime voice, turn on network tools, or explicitly allow remote connectors. A remote `API_BASE_URL` receives prompts, tool results, PDF text summaries, and screenshots that are sent to the configured model because that provider was explicitly configured. Realtime voice sends browser audio/text to OpenAI only when explicitly enabled and used. Network tools send the requested URL, search query, or weather location to the relevant remote service. Remote connectors can send only the data classes declared in their manifest and allowed by Eyra's route policy. `/capabilities` includes concrete privacy-boundary decisions for common model, network, Realtime, and connector paths.

---

## Architecture

```
eyra/
├── pyproject.toml
├── setup.sh
├── docs/                         # Mintlify documentation site
├── internal-docs/                # Source notes that are not part of the public docs site
├── scripts/build_github_pages_docs.py
├── src/
│   ├── main.py                     # Entry point, preflight, live session launch
│   ├── runtime/
│   │   ├── live_session.py         # Unified orchestrator
│   │   ├── actions.py              # Typed local action specs and risk metadata
│   │   ├── capabilities.py         # Local capability snapshot construction
│   │   ├── certification.py        # Voice-to-computer certification matrix
│   │   ├── coding_jobs.py          # Approval-gated terminal-agent job bridge
│   │   ├── connectors/             # Structured connector manifest, registry, runner, acceptance, CLI
│   │   ├── context.py              # Local context snapshots
│   │   ├── dictation.py            # Local dictation state and literal text handling
│   │   ├── intents.py              # Shared screen, file, network, PDF, and task intent rules
│   │   ├── jobs.py                 # Durable SQLite jobs, logs, artifacts, and ledger
│   │   ├── operator_loop.py        # Observe, plan, act, verify, recover execution loop
│   │   ├── planner.py              # Deterministic local task planning
│   │   ├── privacy.py              # Privacy-boundary decisions
│   │   ├── routing/                # Local policy router, model registry, tool policy, traces
│   │   ├── shared.py               # Terminal-owned objects shared with Web UI
│   │   ├── tasks.py                # Background task lifecycle manager
│   │   ├── triggers.py             # Durable file and reminder triggers
│   │   ├── vision.py               # Controller-owned screenshot + vision model flow
│   │   ├── tooling.py              # Shared tool registry builder
│   │   ├── models.py              # Runtime state and event dataclasses
│   │   ├── preflight.py           # Backend, model, and capability validation
│   │   ├── speech_controller.py   # TTS output and STT input coordination
│   │   ├── voice_diagnostics.py   # Local mic, VAD, Local Whisper, and barge-in checks
│   │   ├── voice_input.py         # Silero VAD recording + local-whisper transcription
│   │   ├── status_presenter.py    # Terminal status header and updates
│   │   └── startup.py             # First-run setup and .env management
│   ├── tools/
│   │   ├── approval.py             # Server-side approvals for risky local actions
│   │   ├── base.py                # BaseTool abstract + ToolResult
│   │   ├── registry.py            # Tool registry and dispatch
│   │   ├── screenshot.py          # In-memory screenshot via mss
│   │   ├── time_tool.py           # Current time tool
│   │   ├── weather.py             # Optional network weather tool
│   │   ├── clipboard.py           # Clipboard reader tool
│   │   ├── system_info.py         # System info tool
│   │   ├── browser.py             # Optional network browser tools
│   │   ├── macos_context.py       # Frontmost app and sandboxed Finder selection
│   │   ├── mcp_stdio.py           # Optional stdio MCP bridge
│   │   ├── operator.py            # Optional OS and agent tools
│   │   ├── filesystem.py          # Sandboxed file read/write/edit/list/move/copy/open/reveal
│   │   └── pdf.py                 # Local PDF text extraction
│   ├── web/
│   │   └── server.py              # Built-in browser and phone UI
│   ├── chat/
│   │   ├── capture.py             # In-memory screenshot capture
│   │   ├── complexity_scorer.py   # Deterministic prompt routing
│   │   ├── message_handler.py     # Model selection, response shaping, streaming
│   │   └── session_state.py       # Shared session types
│   ├── clients/
│   │   ├── base_client.py         # BaseAIClient abstract
│   │   └── ai_client.py           # OpenAI-compatible async client
│   └── utils/
│       ├── settings.py
│       ├── image_history.py
│       ├── sound_player.py
│       ├── theme.py
│       └── mock_client.py
```

---

## Certification

Eyra includes a local certification matrix for the release-critical voice-to-computer contract.

Run the default local-first matrix:

```bash
uv run python scripts/certify_voice_to_computer.py
```

Run attended physical microphone checks:

```bash
uv run python scripts/certify_voice_to_computer.py --include-physical
```

For a human-certified physical microphone check, pass a challenge phrase and say that phrase when Eyra starts speaking. The TTS prompt does not say the phrase, so speaker echo cannot satisfy the check:

```bash
uv run python scripts/certify_voice_to_computer.py --include-physical --human-phrase "human microphone release test"
```

Run deterministic virtual microphone checks, for example with BlackHole or another configured loopback input:

```bash
uv run python scripts/certify_voice_to_computer.py --include-physical --synthetic-mic
```

Status meanings:

- `passed`: the scenario completed and verified the expected behavior.
- `failed`: a launch-critical or requested check did not meet the product contract.
- `skipped`: the surface was disabled or the physical condition was not requested for this run.

The matrix proves the configured local runtime path it exercises: model preflight, Local Whisper TTS, microphone diagnostics, synthetic or physical barge-in when requested, durable jobs, operation ledger, triggers, task control, Web APIs, connector manifest and acceptance checks, disabled-by-default privacy behavior, and enabled browser/OS representatives when those settings are turned on.

It does not prove every physical microphone setup, every macOS app UI, or live OpenAI Realtime unless those paths are actually enabled and tested in that environment. Synthetic microphone certification proves the configured virtual input path; run `/voice-diagnose` and a challenge-phrase physical check on each target Mac before claiming human microphone certification.

---

## Troubleshooting

<details><summary><strong>AI backend not responding</strong></summary>

Check that your backend is running and reachable at the URL in `API_BASE_URL`. Eyra probes `/v1/models` on startup and reports the result.

For Ollama (default):

```bash
ollama list
curl http://localhost:11434/v1/models
```

If using a different provider, verify `API_BASE_URL` and `API_KEY` in `.env` are correct.

</details>

<details><summary><strong>Runtime logs</strong></summary>

Runtime logs are written to `~/Library/Logs/Eyra/eyra.log` on macOS. Set `EYRA_LOG_FILE` if you want a different location.

</details>

<details><summary><strong>Tools are not being used</strong></summary>

The selected model must support native tool calling. In Ollama, check:

```bash
ollama show <model>
```

If the model does not list tools, text chat will still work. Tool-required tasks report the limitation clearly instead of claiming the local action completed.

</details>

<details><summary><strong>Screen analysis says a model capability is missing</strong></summary>

Screen analysis needs macOS screenshot permission and a vision-capable model. Native model tool calling is not required for screenshot capture.

Use `VISION_MODEL` when your normal model is not vision-capable:

```env
MODEL=qwen3:4b
VISION_MODEL=gemma3:4b
```

If no configured model can process images, Eyra says so clearly and does not pretend to inspect the screen.

</details>

<details><summary><strong>Task is still running</strong></summary>

Run `/tasks` to see active and recent tasks. Run `/task <id>` for the request, progress, result, or error. Run `/cancel <id>` or `/cancel all` to stop queued or running work.

</details>

<details><summary><strong>File access is blocked</strong></summary>

Eyra only works inside `FILESYSTEM_ALLOWED_PATHS`. The default sandbox is `~/Documents,~/Desktop,~/Downloads,/tmp`. Add another root only when you intentionally want Eyra to use it, then restart the session.

</details>

<details><summary><strong>PDF has no text</strong></summary>

Eyra extracts embedded PDF text locally. If the PDF is scanned or image-only, it reports that no extractable text was found. Eyra does not silently use online OCR or upload the PDF.

</details>

<details><summary><strong>Web UI not reachable from a phone</strong></summary>

The Web UI binds to `127.0.0.1` by default. That is safest, but only the same machine can reach it. For another device on your network, set:

```bash
WEB_UI_ENABLED=true WEB_UI_HOST=0.0.0.0 uv run python -m web.server
```

Use a trusted network and open the tokenized URL printed by Eyra. All non-health API calls require the token. Eyra also refuses cross-origin API requests that do not come from the Web UI host.

Realtime voice also requires browser microphone support and an explicit `OPENAI_API_KEY` when `REALTIME_VOICE_ENABLED=true`. Eyra does not reuse `API_KEY` for Realtime because that may belong to another provider.

</details>

<details><summary><strong>Manual voice interruption test</strong></summary>

Run:

```text
/voice-test
```

Eyra speaks a long diagnostic sentence. Start talking into the microphone while it is speaking. Passing behavior: TTS stops, your new input is recorded and processed, the session stays alive, and background tasks keep their state unless you cancel them.

Eyra uses local VAD for barge-in. It does not perform full acoustic echo cancellation; if your speakers feed back into the microphone, lower the volume or use headphones for the physical test. During normal responses, Eyra pauses listening while it is thinking, then runs a TTS barge-in listener while speaking. An echo guard drops transcripts that look like Eyra's own spoken output.

For deterministic local certification on macOS, feed generated speech through a virtual microphone such as BlackHole 2ch, then run:

```bash
fake-mic start "hello eyra this is deterministic fake microphone input for certification"
VOICE_INPUT_DEVICE='BlackHole 2ch' uv run python scripts/certify_voice_to_computer.py --include-physical --synthetic-mic
fake-mic stop
```

This deterministic path is useful for release checks, but it is not the same as a person speaking into every physical microphone. If physical voice input fails or returns all-zero audio, run `/voice-diagnose`, verify macOS microphone permission for the terminal app, and set `VOICE_INPUT_DEVICE` to the correct sounddevice index or name.

</details>

<details><summary><strong>Voice diagnostics</strong></summary>

Run:

```text
/voice-diagnose
```

The diagnostic stays local. It lists input devices, resolves `VOICE_INPUT_DEVICE`, checks 16 kHz sample-rate support, records a bounded microphone probe, reports silent/all-zero audio clearly, checks stream overflow, probes Silero VAD, checks the Local Whisper socket, and runs `wh transcribe` on a generated local WAV.

If the diagnostic says `microphone input is silent/all-zero`, first check macOS microphone permission for the terminal app, then try a specific device in `.env`:

```env
VOICE_INPUT_DEVICE=2
```

or:

```env
VOICE_INPUT_DEVICE=USB Headset
```

Diagnostic audio is not saved unless `VOICE_DIAGNOSTIC_SAVE_AUDIO=true`.

During normal responses, Eyra pauses the microphone while it is thinking, then listens for speech onset during TTS. `/voice-test` and physical certification still remain separate checks; use headphones for those checks if the microphone hears the speakers.

</details>

<details><summary><strong>Voice not working</strong></summary>

Voice requires [Local Whisper](https://github.com/gabrimatic/local-whisper), which powers input (ASR) and output (TTS). Install it:

```bash
brew tap gabrimatic/local-whisper && brew install local-whisper
```

Eyra's preflight automatically detects the installation (even if `wh` is not on PATH) and starts the service if needed. If preflight reports it's installed but not running, start manually with `wh start`.

You can also toggle voice at runtime with `/voice on|off`. If voice was disabled in `.env`, `/voice on` performs the Local Whisper check at runtime and enables whichever voice features are ready. If ASR is not ready yet, speech can still remain on.

</details>

---

## Development

```bash
git clone https://github.com/gabrimatic/eyra.git
cd eyra
./setup.sh
uv run python -m pytest -q
uv run ruff check src tests
uv lock --check
bash -n setup.sh
uv build --wheel
USE_MOCK_CLIENT=true LIVE_LISTENING_ENABLED=false LIVE_SPEECH_ENABLED=false uv run python src/main.py
WEB_UI_ENABLED=true USE_MOCK_CLIENT=true uv run python -m web.server
uv run python scripts/certify_voice_to_computer.py
uv run python scripts/certify_voice_to_computer.py --include-physical
uv run python scripts/certify_voice_to_computer.py --include-physical --human-phrase "human microphone release test"
uv run python scripts/certify_voice_to_computer.py --include-physical --synthetic-mic
```

---

## Credits

[Ollama](https://ollama.com) · [local-whisper](https://github.com/gabrimatic/local-whisper) (STT + TTS via Kokoro) · [Silero VAD](https://github.com/snakers4/silero-vad) (voice activity detection) · [mss](https://github.com/BoboTiG/python-mss)

<details>
<summary><strong>Legal notices</strong></summary>

### Trademarks

"Ollama" is a trademark of its respective owner. All trademark names are used solely to describe compatibility with their respective technologies. This project is not affiliated with, endorsed by, or sponsored by any trademark holder.

### Third-party licenses

Dependencies keep their own licenses. See each package for details.

</details>

## License

Source-available under the PolyForm Noncommercial License 1.0.0. Noncommercial use, modification, and redistribution are allowed when the license and required notices stay with the software. Commercial use requires written permission from Soroush Yousefpour. See [LICENSE](LICENSE).

---

Created by [Soroush Yousefpour](https://gabrimatic.info)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/gabrimatic)
