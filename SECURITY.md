# Security policy

## Privacy by design

Privacy is a core constraint, not a feature toggle.

- **Default processing is local.** Screenshots and voice input stay on your Mac; remote AI providers are opt-in through `API_BASE_URL`.
- **No silent network calls.** The configured AI backend is localhost by default. Weather and browser tools stay disabled unless `NETWORK_TOOLS_ENABLED=true`; weather lookups require an explicit location.
- **No telemetry or analytics.** Eyra does not send tracking data.
- **Screenshots stay in memory.** Eyra never writes screenshot captures to disk.

## Permissions

| Permission | Why | Scope |
|------------|-----|-------|
| **Screen capture** | Screenshot tool, on demand, model-invoked | Single frame when requested |
| **Microphone** | Voice input recording | In-process via sounddevice (Silero VAD); transcription via local-whisper |
| **Network** | AI backend API | Loopback by default; follows `API_BASE_URL` |
| **Network tools** | Weather and browser lookup | Disabled by default; enabled only with `NETWORK_TOOLS_ENABLED=true` |

Permissions are requested on demand. Nothing runs in the background between interactions.

## Trust boundaries

| Boundary | Trust Level | Notes |
|----------|-------------|-------|
| AI backend at `API_BASE_URL` | Controlled by you | Loopback by default; remote if configured |
| wh (local-whisper) | Trusted | Subprocess, runs on localhost, no network |
| Filesystem sandbox | Enforced | Paths restricted to `FILESYSTEM_ALLOWED_PATHS` (default `~/Documents,/tmp`). Rejects empty paths, binary reads, and binary file edits. `write_file` requires explicit overwrite for existing files. |
| Filesystem default path | Enforced | Relative paths resolve under `FILESYSTEM_DEFAULT_PATH`, then pass through the same sandbox check. |
| Weather/browser tools | Opt-in | Contact remote sites only when `NETWORK_TOOLS_ENABLED=true` and a tool is used. Weather requires an explicit location and does not use remote IP geolocation. Browser uses headless Chromium, http/https only, 30s tool timeout. |
| `.env` file | Controlled by you | Must not be committed |
| Local logs | Local artifact | Stored under `~/Library/Logs/Eyra/eyra.log` by default on macOS. Tool-call logs record tool names and argument keys only, not argument values. |
| Prompts | Untrusted input | Passed to AI backends as message content; no shell execution |

Prompts are passed to AI backends as message content only. Eyra does not construct shell commands from prompt text.

## Vulnerability reporting

Report vulnerabilities responsibly:

1. **Do not open a public issue.** Public disclosure before a fix is available puts people at risk.
2. Use [GitHub's private vulnerability reporting](https://github.com/gabrimatic/eyra/security/advisories/new) to submit.
3. Include:
   - Steps to reproduce
   - Demonstrated impact
   - Suggested fix (if any)

Reports without reproduction steps or demonstrated impact are deprioritized.

Expect acknowledgment within 48 hours.

## Out of scope

These are not considered vulnerabilities:

- Issues in third-party dependencies (AI providers, local-whisper)
- Issues requiring physical access to the machine
- Denial-of-service via resource exhaustion on private machines

## Supported versions

| Version | Supported |
|---------|-----------|
| 3.x     | Yes       |
