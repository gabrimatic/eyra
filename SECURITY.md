# Security Policy

## Privacy by Design

Privacy is a core constraint, not a feature toggle.

- All default processing runs locally on the user's machine
- No data is sent over the network unless complexity routing selects Google Gemini
- No telemetry, analytics, or usage reporting of any kind
- Screenshots and webcam frames exist only in memory and are never written to disk

## Permissions

| Permission | Why | Scope |
|-----------|-----|-------|
| Screen capture | Screenshot mode and Live mode | On demand, not continuous |
| Camera | `#selfie` keyword in Manual mode | On demand, single frame |
| Microphone | Voice mode recording | Delegated to local-whisper (`wh`) for voice mode |
| Network (localhost) | Ollama API | Always, loopback only |
| Network (external) | Google Gemini API | Only when complexity score routes to cloud |

All permissions are requested on demand. Nothing runs in the background between interactions.

## Trust Boundaries

| Boundary | Trust Level | Notes |
|----------|------------|-------|
| Ollama at localhost:11434 | Trusted | Loopback only, no external exposure |
| wh (local-whisper) | Trusted | Subprocess, runs on localhost, no network |
| Google Gemini | Partially trusted | Cloud service, only invoked when needed |
| `.env` file | User-controlled | Contains API key, must not be committed |
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
- Google Gemini service-side behavior or data handling
- Issues requiring physical access to the machine
- Denial-of-service via resource exhaustion on private machines

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |
