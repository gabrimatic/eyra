# Security Policy

## Privacy by Design

Privacy is a core constraint, not a feature toggle.

- **All processing is local.** Screenshots and voice input are handled entirely on your Mac.
- **No network calls** except to the configured AI backend. By default this is localhost.
- **No telemetry, no analytics, no cloud.** No tracking data ever leaves your machine.
- **Screenshots** exist only in memory and are never written to disk.

## Permissions

| Permission | Why | Scope |
|------------|-----|-------|
| **Screen capture** | Screenshot tool (on-demand, model-invoked) | Single frame when requested |
| **Microphone** | Voice input recording | In-process via sounddevice (Silero VAD); transcription via local-whisper |
| **Network** | AI backend API | Loopback by default; follows `API_BASE_URL` |

All permissions are requested on demand. Nothing runs in the background between interactions.

## Trust Boundaries

| Boundary | Trust Level | Notes |
|----------|-------------|-------|
| AI backend at `API_BASE_URL` | User-controlled | Loopback by default; remote if configured |
| wh (local-whisper) | Trusted | Subprocess, runs on localhost, no network |
| Filesystem sandbox | Enforced | Paths restricted to `FILESYSTEM_ALLOWED_PATHS` (default `~/,/tmp`). Rejects empty paths and binary file edits. |
| Browser (Playwright) | Sandboxed | Headless Chromium, http/https only, 30s tool timeout |
| `.env` file | User-controlled | Must not be committed |
| User prompts | Untrusted input | Passed to AI backends, no shell execution |

User input is passed to AI backends as message content only. No shell commands are constructed from user input.

## Vulnerability Reporting

Report vulnerabilities responsibly:

1. **Do not open a public issue.** Public disclosure before a fix is available puts users at risk.
2. Use [GitHub's private vulnerability reporting](https://github.com/gabrimatic/eyra/security/advisories/new) to submit.
3. Include:
   - Steps to reproduce
   - Demonstrated impact
   - Suggested fix (if any)

Reports without reproduction steps or demonstrated impact are deprioritized.

Expect acknowledgment within 48 hours.

## Out of Scope

These are not considered vulnerabilities:

- Issues in third-party dependencies (AI providers, local-whisper)
- Issues requiring physical access to the machine
- Denial-of-service via resource exhaustion on private machines

## Supported Versions

| Version | Supported |
|---------|-----------|
| 3.x     | Yes       |
