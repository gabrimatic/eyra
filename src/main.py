# main.py

import asyncio
import logging
import os
import warnings

from utils.settings import Settings
from modes.manual_mode import ManualMode
from modes.live_mode import LiveMode
from modes.voice.voice_mode import (
    VoiceMode,
)
from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import close_all_clients

warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")

os.environ["TOKENIZERS_PARALLELISM"] = "false"


async def main() -> None:
    """
    Main application entry point.

    1. Load spaCy model once (async).
    2. Load settings from environment or config file.
    3. Initialize ComplexityScorer for task routing.
    4. Provide a menu for the user to pick Manual Mode, Live Mode, or Voice Mode.
    5. Run until the user chooses to exit.
    6. Close all clients gracefully.
    """
    # Configure logging globally
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("Main")

    logger.info("Starting application...")

    # Step 2: Load settings
    try:
        settings = Settings.load_from_env()
        logger.info("Settings loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return

    try:
        complexity_scorer = ComplexityScorer()
        logger.info("ComplexityScorer initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize ComplexityScorer: {e}")
        return

    # Step 4: Provide a menu
    menu_text = """
Select Mode
1. Manual Mode (Interactive chat)
2. Live Mode (Automatic screenshot analysis)
3. Voice Mode (Voice interaction)
    """
    print(menu_text)

    # We keep a single `messages` list across mode switches
    messages = []
    try:
        while True:
            mode_choice = input("Enter mode number (1, 2, or 3): ").strip()
            if mode_choice not in ["1", "2", "3"]:
                logger.warning("Invalid choice. Please enter 1, 2, or 3.")
                continue

            if mode_choice == "1":
                selected_mode = "Manual"
            elif mode_choice == "2":
                selected_mode = "Live"
            else:
                selected_mode = "Voice"  # We'll call this 'Voice'

            logger.info(f"Starting {selected_mode} Mode...")

            # Create the mode instance
            if selected_mode == "Manual":
                mode_instance = ManualMode(
                    settings=settings,
                    messages=messages,
                    complexity_scorer=complexity_scorer,
                )
            elif selected_mode == "Live":
                mode_instance = LiveMode(
                    settings=settings,
                    messages=messages,
                    complexity_scorer=complexity_scorer,
                )
            else:
                # Use your new async VoiceMode with sentence-based partial TTS
                mode_instance = VoiceMode(
                    settings=settings,
                    messages=messages,
                    complexity_scorer=complexity_scorer,
                )

            try:
                # 5) Run the selected mode *asynchronously*—no nested loop calls
                await mode_instance.run()

                # If the user didn't request a switch, we break the menu loop
                if not getattr(mode_instance, "switch_requested", False):
                    logger.info(f"{selected_mode} Mode completed without switching.")
                    break
            except Exception as e:
                logger.error(f"Error while running {selected_mode} Mode: {e}")
                break

    finally:
        await close_all_clients()
        logger.info("Application closed successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("Main").info("Application interrupted by user. Exiting...")
    except Exception as e:
        logging.getLogger("Main").error(f"Unhandled exception: {e}")
