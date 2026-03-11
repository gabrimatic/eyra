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
        inner = f" {label} "
        dashes = "─" * (_BOX_WIDTH - len(inner))
        return f"╭─{inner}{dashes}╮"
    return f"╭{'─' * (_BOX_WIDTH)}╮"


def _box_bottom() -> str:
    return f"╰{'─' * _BOX_WIDTH}╯"


def _box_row(text: str = "") -> str:
    """Return a box row with plain text. Caller responsible for ANSI in text."""
    return f"│  {text}"


def render_header(state: LiveRuntimeState, settings=None):
    """Print the live session header."""
    print()
    print(f"╭{'─' * _BOX_WIDTH}╮")
    print(f"│  {BOLD}Eyra{NC}{' ' * (_BOX_WIDTH - 4)}│")
    print(f"╰{'─' * _BOX_WIDTH}╯")
    print()

    voice = f"{GREEN}on{NC}" if state.listening_enabled else f"{DIM}off{NC}"
    speech = (
        f"{YELLOW}muted{NC}" if state.speech_muted
        else f"{GREEN}on{NC}" if state.speech_enabled
        else f"{DIM}off{NC}"
    )
    backend = f"{GREEN}ready{NC}" if state.backend_ready else f"{RED}unavailable{NC}"

    cap_line = f"  Voice: {voice}    Speech: {speech}    Backend: {backend}"
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


def render_status_card(
    state: LiveRuntimeState,
    quality_mode_value: str,
    tool_count: int,
    msg_count: int,
    model_name: str = "",
):
    """Print a full status card."""
    voice = "on" if state.listening_enabled else "off"
    speech = "muted" if state.speech_muted else "on" if state.speech_enabled else "off"
    goal = state.current_goal or "none"

    print()
    print(f"╭─ Status {'─' * (_BOX_WIDTH - 9)}╮")
    print(f"│  Voice: {voice}    Speech: {speech}{' ' * (_BOX_WIDTH - 18 - len(voice) - len(speech))}│")
    if model_name:
        pad = _BOX_WIDTH - 10 - len(model_name)
        print(f"│  Model: {model_name}{' ' * max(pad, 0)}│")
    pad_q = _BOX_WIDTH - 12 - len(quality_mode_value)
    print(f"│  Quality: {quality_mode_value}{' ' * max(pad_q, 0)}│")
    pad_g = _BOX_WIDTH - 9 - len(goal)
    print(f"│  Goal: {goal}{' ' * max(pad_g, 0)}│")
    hist = f"{msg_count} messages"
    pad_h = _BOX_WIDTH - 13 - len(hist)
    print(f"│  History: {hist}{' ' * max(pad_h, 0)}│")
    tools = f"{tool_count} available"
    pad_t = _BOX_WIDTH - 11 - len(tools)
    print(f"│  Tools: {tools}{' ' * max(pad_t, 0)}│")
    print(f"╰{'─' * _BOX_WIDTH}╯")
    print()


def render_help_card():
    """Print the /help command card."""
    cmds = [
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
    print(f"╭─ Commands {'─' * (_BOX_WIDTH - 11)}╮")
    for cmd, desc in cmds:
        row = f"{CYAN}{cmd}{NC}{desc}"
        pad = _BOX_WIDTH - len(cmd) - len(desc) - 2
        print(f"│  {row}{' ' * max(pad, 0)}│")
    print(f"╰{'─' * _BOX_WIDTH}╯")
    print()
