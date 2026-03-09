"""
Eyra — live AI screen assistant.

Starts immediately as an always-on live session with screen observation,
typed input, and optional voice I/O. No mode switching required.
"""

import asyncio
import logging
import os
import warnings

from utils.settings import Settings
from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import close_all_clients
from runtime.preflight import PreflightManager
from runtime.models import LiveRuntimeState
from runtime.live_session import LiveSession

warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")
os.environ["TOKENIZERS_PARALLELISM"] = "false"


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
        print("\n  Backend is not reachable. Start your backend and try again.\n")
        return

    if preflight.models_missing:
        print(f"\n  Missing models: {', '.join(preflight.models_missing)}")
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
        await close_all_clients()
        logger.info("Session ended.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
    except Exception as e:
        logging.getLogger("Main").error("Unhandled: %s", e)
