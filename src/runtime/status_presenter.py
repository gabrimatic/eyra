"""Clean user-facing status display."""

from __future__ import annotations

from runtime.models import LiveRuntimeState, RuntimeStatus
from utils.theme import BOLD, CYAN, DIM, GREEN, NC, RED, YELLOW

_BOX_WIDTH = 36

_STATUS_LABELS = {
    RuntimeStatus.STARTING: f"{DIM}starting{NC}",
    RuntimeStatus.PREFLIGHT: f"{DIM}checking{NC}",
    RuntimeStatus.LISTENING: f"{GREEN}listening{NC}",
    RuntimeStatus.THINKING: f"{CYAN}thinking{NC}",
    RuntimeStatus.SPEAKING: f"{CYAN}speaking{NC}",
    RuntimeStatus.IDLE: f"{DIM}idle{NC}",
    RuntimeStatus.BACKEND_UNAVAILABLE: f"{RED}backend unavailable{NC}",
    RuntimeStatus.PERMISSION_REQUIRED: f"{RED}needs permission{NC}",
    RuntimeStatus.ERROR: f"{RED}error{NC}",
}


def _box_top(label: str = "") -> str:
    if label:
        inner = f"─ {label} "
        dashes = "─" * (_BOX_WIDTH - len(inner))
        return f"╭{inner}{dashes}╮"
    return f"╭{'─' * _BOX_WIDTH}╮"


def _box_bottom() -> str:
    return f"╰{'─' * _BOX_WIDTH}╯"


def _box_row(text: str = "") -> str:
    """Return a box row with plain text. Caller responsible for ANSI in text."""
    return f"│  {text}"


def render_header(state: LiveRuntimeState, settings=None):
    """Print the live session header."""
    print()
    print(f"╭{'─' * _BOX_WIDTH}╮")
    label = "Eyra"
    pad = _BOX_WIDTH - 2 - len(label)
    print(f"│  {BOLD}{label}{NC}{' ' * pad}│")
    print(f"╰{'─' * _BOX_WIDTH}╯")
    print()

    voice = (
        f"{DIM}off{NC}" if not (state.listening_enabled or state.speech_enabled)
        else f"{YELLOW}muted{NC}" if state.speech_muted
        else f"{GREEN}on{NC}"
    )
    backend = f"{GREEN}ready{NC}" if state.backend_ready else f"{RED}unavailable{NC}"

    cap_line = f"  Voice: {voice}    Backend: {backend}"
    print(cap_line)

    if settings is not None:
        model_name = getattr(settings, "MODEL", None)
        if model_name:
            print(f"  {DIM}Model: {model_name}{NC}")

    if state.current_goal:
        print(f"  Goal: {state.current_goal}")

    print()
    input_hint = "Type anything or speak." if state.listening_enabled else "Type anything."
    print(f"  {DIM}{input_hint} /help for commands.{NC}")
    print()


def status_line(state: LiveRuntimeState) -> str:
    """One-line status string for inline updates."""
    return _STATUS_LABELS.get(state.current_status, state.current_status.value)


def print_status_change(label: str):
    """Print a brief inline status update."""
    print(f"  {DIM}› {label}{NC}", flush=True)


def _box_row_padded(label: str, value: str) -> str:
    """Build a box row with label: value, right-padded and truncated to fit."""
    content = f"{label}: {value}"
    max_content = _BOX_WIDTH - 2  # 2 for leading "  "
    if len(content) > max_content:
        content = content[: max_content - 1] + "…"
    pad = _BOX_WIDTH - 2 - len(content)
    return f"│  {content}{' ' * max(pad, 0)}│"


def render_status_card(
    state: LiveRuntimeState,
    quality_mode_value: str,
    tool_count: int,
    msg_count: int,
    model_name: str = "",
):
    """Print a full status card."""
    voice = "off" if not (state.listening_enabled or state.speech_enabled) else "muted" if state.speech_muted else "on"
    goal = state.current_goal or "none"

    print()
    print(_box_top("Status"))
    print(_box_row_padded("Voice", voice))
    if model_name:
        print(_box_row_padded("Model", model_name))
    print(_box_row_padded("Quality", quality_mode_value))
    print(_box_row_padded("Goal", goal))
    print(_box_row_padded("History", f"{msg_count} messages"))
    print(_box_row_padded("Tools", f"{tool_count} available"))
    print(f"╰{'─' * _BOX_WIDTH}╯")
    print()


def render_help_card():
    """Print the /help command card."""
    cmds = [
        ("/voice     ", "on|off"),
        ("/mute      ", "Mute speech output"),
        ("/unmute    ", "Unmute speech"),
        ("/goal TEXT ", "Set a goal"),
        ("/mode MODE ", "fast|balanced|best"),
        ("/status    ", "Show session info"),
        ("/clear     ", "Reset conversation"),
        ("/help      ", "Show this help"),
        ("/quit      ", "Exit Eyra"),
    ]
    print()
    print(_box_top("Commands"))
    for cmd, desc in cmds:
        row = f"{CYAN}{cmd}{NC}{desc}"
        pad = _BOX_WIDTH - len(cmd) - len(desc) - 2
        print(f"│  {row}{' ' * max(pad, 0)}│")
    print(f"╰{'─' * _BOX_WIDTH}╯")
    print()
