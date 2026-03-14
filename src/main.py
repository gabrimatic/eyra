"""
Eyra — personal on-device AI agent.

Starts as an always-on live session with typed input, optional voice I/O,
and on-demand tool use. The model decides when to capture the screen.
"""

import asyncio
import logging
import os
import warnings

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import close_all_clients, get_used_model_names
from runtime.live_session import LiveSession
from runtime.models import LiveRuntimeState
from runtime.preflight import PreflightManager
from utils.settings import Settings
from utils.theme import NC, RED, YELLOW

warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")


async def main() -> None:
    log_format = "[%(asctime)s] %(levelname)s - %(message)s"
    log_datefmt = "%Y-%m-%d %H:%M:%S"
    log_file = os.path.join(os.path.dirname(__file__), "..", "eyra.log")

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=log_datefmt))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=log_datefmt))

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[console_handler, file_handler],
    )
    for name in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logger = logging.getLogger("Main")
    logger.info("Starting Eyra...")

    settings = Settings.load_from_env()
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


if __name__ == "__main__":
    from runtime.startup import maybe_run_startup_selector
    maybe_run_startup_selector()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
    except Exception as e:
        logging.getLogger("Main").error("Unhandled: %s", e)
