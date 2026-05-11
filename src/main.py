"""
Eyra — personal on-device AI agent.

Starts as an always-on live session with typed input, optional voice I/O,
and on-demand tool use. The model decides when to capture the screen.
"""

import asyncio
import logging
import os
import warnings
from pathlib import Path

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import close_all_clients, get_used_model_names
from runtime.live_session import LiveSession
from runtime.models import LiveRuntimeState
from runtime.preflight import PreflightManager
from utils.settings import Settings
from utils.theme import NC, RED, YELLOW

warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")


def get_log_file_path() -> Path:
    """Return the user-writable log file path for this runtime."""
    override = os.getenv("EYRA_LOG_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "posix" and os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Logs" / "Eyra" / "eyra.log"
    state_home = os.getenv("XDG_STATE_HOME", "").strip()
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "eyra" / "eyra.log"


def _file_handler_for(log_file: Path, formatter: logging.Formatter) -> logging.Handler:
    """Create a file handler, falling back to /tmp if the preferred path is unavailable."""
    candidates = [log_file, Path("/tmp") / "eyra.log"]
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(candidate)
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(formatter)
            return handler
        except OSError:
            continue
    handler = logging.NullHandler()
    handler.setLevel(logging.DEBUG)
    return handler


async def main() -> None:
    log_format = "[%(asctime)s] %(levelname)s - %(message)s"
    log_datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=log_datefmt)
    log_file = get_log_file_path()

    file_handler = _file_handler_for(log_file, formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.CRITICAL)
    console_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[console_handler, file_handler],
    )
    for name in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logger = logging.getLogger("Main")
    logger.info("Starting Eyra...")

    try:
        settings = Settings.load_from_env()
    except ValueError as e:
        print(f"\n  {RED}Configuration error:{NC} {e}\n")
        return
    scorer = ComplexityScorer()

    # Preflight: check backend, models, capabilities
    preflight = await PreflightManager(settings).run()

    if not preflight.backend_reachable:
        print(f"\n  {RED}Backend is not reachable.{NC} Start your backend and try again.\n")
        return

    if preflight.models_missing:
        missing = ", ".join(preflight.models_missing)
        print(f"\n  {YELLOW}Missing models:{NC} {missing}")
        print("  Run setup.sh or pull them manually.\n")
        return

    # Build runtime state from preflight results
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)

    # Launch live session
    session = LiveSession(
        settings=settings,
        preflight=preflight,
        state=state,
        complexity_scorer=scorer,
    )

    try:
        await session.run()
    finally:
        await PreflightManager.unload_models(settings, get_used_model_names())
        await close_all_clients()
        logger.info("Session ended.")


def run() -> None:
    """Console-script entry point."""
    from runtime.startup import maybe_run_startup_selector

    maybe_run_startup_selector()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Interrupted.\n")
    except Exception as e:
        logging.getLogger("Main").exception("Unhandled: %s", e)
        print(f"\n  {RED}Something went wrong.{NC} Check {get_log_file_path()} and try again.\n")


if __name__ == "__main__":
    run()
