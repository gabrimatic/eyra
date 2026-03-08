# Security Policy

## Privacy by Design

Privacy is a core constraint, not a feature toggle.

- **All processing is local.** Screenshots, webcam frames, and voice input are handled entirely on your Mac.
- **No network calls** except to localhost (Ollama on port 11434).
- **No telemetry, no analytics, no cloud.** Zero data leaves your machine.
- **Screenshots and webcam frames** exist only in memory and are never written to disk.
- **No outbound connections.** The app makes no internet requests.

## Permissions

| Permission | Why | Scope |
|------------|-----|-------|
| **Screen capture** | Screenshot mode and Live mode | On demand, not continuous |
| **Camera** | `#selfie` keyword in Manual mode | On demand, single frame |
| **Microphone** | Voice mode recording | Delegated to local-whisper (`wh`) |
| **Network (localhost)** | Ollama API | Loopback only, no external exposure |

All permissions are requested on demand. Nothing runs in the background between interactions.

## Trust Boundaries

| Boundary | Trust Level | Notes |
|----------|-------------|-------|
| Ollama at localhost:11434 | Trusted | Loopback only, no external exposure |
| wh (local-whisper) | Trusted | Subprocess, runs on localhost, no network |
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

- Issues in third-party dependencies (Ollama, local-whisper, spaCy, CLIP)
- Issues requiring physical access to the machine
- Denial-of-service via resource exhaustion on private machines

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |
