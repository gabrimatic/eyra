"""Clean user-facing status display."""

from runtime.models import LiveRuntimeState, RuntimeStatus

CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
DIM = "\033[2m"
BOLD = "\033[1m"
NC = "\033[0m"

_STATUS_LABELS = {
    RuntimeStatus.STARTING: f"{DIM}starting{NC}",
    RuntimeStatus.PREFLIGHT: f"{DIM}checking{NC}",
    RuntimeStatus.OBSERVING: f"{GREEN}observing{NC}",
    RuntimeStatus.LISTENING: f"{GREEN}listening{NC}",
    RuntimeStatus.CHANGE_DETECTED: f"{YELLOW}change detected{NC}",
    RuntimeStatus.ANALYZING: f"{CYAN}analyzing screen{NC}",
    RuntimeStatus.THINKING: f"{CYAN}thinking{NC}",
    RuntimeStatus.SPEAKING: f"{CYAN}speaking{NC}",
    RuntimeStatus.PAUSED: f"{YELLOW}paused{NC}",
    RuntimeStatus.IDLE: f"{DIM}idle{NC}",
    RuntimeStatus.BACKEND_UNAVAILABLE: f"{RED}backend unavailable{NC}",
    RuntimeStatus.PERMISSION_REQUIRED: f"{RED}needs permission{NC}",
    RuntimeStatus.ERROR: f"{RED}error{NC}",
}


def render_header(state: LiveRuntimeState):
    """Print the live session header."""
    print()
    print(f"{BOLD}Eyra Live{NC}")
    print()

    obs = f"{GREEN}on{NC}" if state.observing and not state.paused else f"{YELLOW}paused{NC}" if state.paused else f"{DIM}off{NC}"
    listen = f"{GREEN}on{NC}" if state.listening_enabled else f"{DIM}off{NC}"
    speech = f"{GREEN}on{NC}" if state.speech_enabled and not state.speech_muted else f"{YELLOW}muted{NC}" if state.speech_muted else f"{DIM}off{NC}"
    backend = f"{GREEN}local{NC}" if state.backend_ready else f"{RED}unavailable{NC}"

    print(f"  Observation: {obs}  Listening: {listen}  Speech: {speech}")
    print(f"  Backend: {backend}  Routing: {GREEN}automatic{NC}")

    if state.current_goal:
        print(f"  Goal: {state.current_goal}")

    print()
    input_hint = "Type anything or speak." if state.listening_enabled else "Type anything."
    print(f"  {DIM}{input_hint} /pause /mute /goal /status /quit{NC}")
    print()


def status_line(state: LiveRuntimeState) -> str:
    """One-line status string for inline updates."""
    return _STATUS_LABELS.get(state.current_status, state.current_status.value)


def print_status_change(label: str):
    """Print a brief inline status update."""
    print(f"  {DIM}› {label}{NC}", flush=True)
