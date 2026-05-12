"""Shared user-intent detection for terminal and web runtimes."""

from __future__ import annotations

import re

_UI_NOUNS = (
    r"screen|display|window|app|browser|tab|"
    r"button|menu|dialog|popup|modal|sidebar|toolbar|"
    r"notification|icon|cursor|selection|highlight"
)

_SCREEN_CUES = re.compile(
    rf"\b({_UI_NOUNS})\b"
    r"|"
    rf"\b(look(?:ing)?\s+at|show\s+me|read\s+the|text\s+on|code\s+on|what'?s\s+on)\s+(the\s+)?({_UI_NOUNS}|this|that|it|here)\b"
    r"|"
    r"\bwhat\s+(?:i'?m|i am)\s+looking\s+at\b"
    r"|"
    r"\b(what\s+is\s+(this|that)|what'?s\s+(this|that)|see\s+(this|that|here)|explain\s+(this|that))\b",
    re.I,
)

_PDF_PATH = re.compile(r"(?P<path>(?:~|/)[^\s'\"<>]+?\.pdf)\b", re.I)


def needs_screen_context(text: str) -> bool:
    """Return True when a request should be answered from the current screen."""
    return bool(_SCREEN_CUES.search(text))


def requires_filesystem(text: str) -> bool:
    """Return True when a request likely needs local filesystem context."""
    return bool(
        re.search(
            r"\b(file|folder|pdf|desktop|documents|downloads|clipboard|move|copy|write|create|open|read)\b",
            text,
            re.I,
        )
    )


def requires_network(text: str) -> bool:
    """Return True when a request needs opt-in network tools."""
    return bool(re.search(r"https?://|\b(website|web page|webpage|weather|browse|search the web)\b", text, re.I))


def extract_pdf_path(text: str) -> str | None:
    """Return the first explicit local PDF path in a request."""
    match = _PDF_PATH.search(text)
    if match is None:
        return None
    return match.group("path").rstrip(".,;:")


def requires_model_driven_tools(text: str) -> bool:
    """Return True when a request needs model-driven tool calling.

    Controller-owned screen and explicit local PDF requests can run without
    native model tools, so they are excluded here.
    """
    if needs_screen_context(text):
        return False
    if re.search(r"\bpdf\b", text, re.I) and extract_pdf_path(text):
        return False
    return requires_filesystem(text) or requires_network(text)


def should_background_task(text: str) -> bool:
    """Return True for requests that should not block the coordinator."""
    lowered = text.lower()
    if needs_screen_context(text) or requires_filesystem(text) or requires_network(text):
        return True
    return bool(
        re.search(
            r"\b(summarize|read|open|move|copy|create|write|edit|organize|inspect|translate|pdf|file|folder|website)\b",
            lowered,
        )
    )


def task_title(text: str) -> str:
    """Create a compact user-visible task title."""
    title = " ".join(text.strip().split())
    if len(title) > 48:
        title = title[:45].rstrip() + "..."
    return title or "Task"
