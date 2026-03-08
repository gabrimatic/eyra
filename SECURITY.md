# Security

## Privacy by Design

- All default processing runs locally on the user's machine
- No data is sent over the network unless complexity routing selects Google Gemini
- No telemetry, analytics, or usage reporting of any kind
- Screenshots and webcam frames exist only in memory and are never written to disk

---

## Permissions

| Permission | Why | Scope |
|-----------|-----|-------|
| Screen capture | Screenshot mode and Live mode | On demand, not continuous |
| Camera | `#selfie` keyword in Manual mode | On demand, single frame |
| Microphone | Voice mode recording | Delegated to local-whisper (`wh`) for voice mode |
| Network (localhost) | Ollama API | Always, loopback only |
| Network (external) | Google Gemini API | Only when complexity score routes to cloud |

---

## Trust Boundaries

| Boundary | Trust Level | Notes |
|----------|------------|-------|
| Ollama at localhost:11434 | Trusted | Loopback only, no external exposure |
| wh (local-whisper) | Trusted | Subprocess, runs on localhost, no network |
| Google Gemini | Partially trusted | Cloud service, only invoked when needed |
| `.env` file | User-controlled | Contains API key, must not be committed |
| User prompts | Untrusted input | Passed to AI backends, no shell execution |

---

## Vulnerability Reporting

Do not open a public issue for security vulnerabilities.

1. Go to [https://github.com/gabrimatic/eyra/security/advisories/new](https://github.com/gabrimatic/eyra/security/advisories/new)
2. Describe the vulnerability, affected versions, and reproduction steps
3. Include potential impact and, if known, a suggested fix
4. Allow up to 7 days for an initial response before any public disclosure

---

## Out of Scope

- Issues in third-party dependencies (Ollama, local-whisper, spaCy, CLIP)
- Google Gemini service-side behavior or data handling
- Issues requiring physical access to the machine
- Denial-of-service via resource exhaustion on private machines

---

## Supported Versions

| Version | Supported |
|---------|----------|
| 2.x | Yes |
| 1.x | No |
