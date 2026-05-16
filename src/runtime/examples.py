"""Plain-language examples for getting useful work out of Eyra."""

from __future__ import annotations


def render_examples() -> str:
    """Return respectful, concrete examples for app and live-session use."""
    return "\n".join(
        [
            "Eyra examples",
            "",
            "Setup and readiness:",
            "  eyra setup",
            "  eyra doctor",
            "  eyra certify",
            "",
            "Good first things to ask inside Eyra:",
            "  What can you control?",
            "  Are you local right now?",
            "  What would leave my machine?",
            "  Help me check if setup is ready.",
            "",
            "Everyday local work:",
            "  Move the latest downloaded file to Documents.",
            "  Summarize this PDF.",
            "  Remind me in 10 minutes to stand up.",
            "  Start dictation.",
            "  What changed?",
            "",
            "Voice and hands-free:",
            "  /voice on",
            "  /voice-diagnose",
            "  stop",
            "  approve that",
            "  choose number two",
            "",
            "Optional surfaces, only when you enable them:",
            "  eyra web",
            "  /connectors",
            "  /connector test <id>",
            "  /capabilities",
            "",
            "Plain requests are fine. Eyra should explain what is ready, what is local, and what needs attention.",
        ]
    )
