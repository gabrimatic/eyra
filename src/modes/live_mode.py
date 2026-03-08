# live_mode.py

"""
Live mode implementation for continuous screen analysis with voice feedback.
"""

import asyncio
import logging
import shutil
from datetime import datetime
from typing import Optional, Any

from .base_mode import BaseMode
from chat.message_handler import process_task_stream


class LiveMode(BaseMode):
    """
    Continuously captures screenshots at intervals, analyzing them via the complexity_scorer.
    Optionally provides TTS feedback.
    """

    def __init__(
        self,
        settings: Any,
        messages: Optional[list] = None,
        complexity_scorer: Optional[Any] = None,
    ):
        super().__init__(settings)
        self.messages = messages if messages is not None else []
        self.complexity_scorer = complexity_scorer
        self.switch_requested = False
        self.description_prompt: Optional[str] = None
        self.muted: bool = False
        self.error_count = 0
        self.max_consecutive_errors = 3
        self.running = True

        self.logger = logging.getLogger(self.__class__.__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        self.wh_available = shutil.which("wh") is not None
        if not self.wh_available:
            self.logger.warning("local-whisper (wh) not found. Voice feedback will be disabled.")
            self.muted = True

    async def run(self) -> None:
        """
        Main entry for Live Mode:
          1) Ask for description prompt
          2) Ask if user wants TTS muted
          3) Start main loop, capturing screenshots at intervals
        """
        self.logger.info("Running Live Mode...")
        await self._prompt_for_description()
        await self._prompt_for_mute()

        while True:
            self.running = True
            self.switch_requested = False

            # Show some instructions
            help_text = """
Live Mode Started
- Press Ctrl+C to exit
            """
            print(help_text)

            await self._main_loop()

            if self.switch_requested:
                from .manual_mode import ManualMode

                manual_mode = ManualMode(
                    self.settings,
                    self.messages,
                    self.complexity_scorer,
                )
                await manual_mode.run()

                if manual_mode.switch_requested:
                    # If manual mode wants to come back to Live mode
                    self.logger.info("Restarting Live Mode after manual mode switch.")
                    continue
                else:
                    # Done
                    self.logger.info("Exiting Live Mode after manual mode.")
                    break
            else:
                self.logger.info("Exiting Live Mode by user choice.")
                break

    async def _main_loop(self) -> None:
        """
        Continuously capture screenshots, analyze them, speak results if not muted.
        """
        while self.running:
            try:
                if self.switch_requested:
                    self.logger.info("Switch requested. Exiting main loop.")
                    break

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.logger.info(f"{timestamp} - Capturing screenshot...")

                print("\nEyra:", end="", flush=True)
                full_response = ""
                async for chunk in process_task_stream(
                    task_type="image",
                    complexity_scorer=self.complexity_scorer,
                    settings=self.settings,
                    messages=[{"role": "user", "content": self.description_prompt}],
                ):
                    print(chunk, end="", flush=True)
                    full_response += chunk
                print()

                self.messages.append({"role": "assistant", "content": full_response})
                if not self.muted and full_response.strip():
                    self.logger.info("Playing voice feedback...")
                    proc = await asyncio.create_subprocess_exec(
                        "wh", "whisper", full_response,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    self.logger.info("Voice feedback completed.")

                # Sleep before next capture
                await asyncio.sleep(self.settings.SCREENSHOT_INTERVAL)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt. Stopping live mode.")
                self.running = False
                break
            except Exception as e:
                if not await self._handle_error(e):
                    break

    async def _prompt_for_description(self) -> None:
        """
        Prompt user for a description or use a default if empty.
        """
        self.logger.info("Enter the description prompt for analyzing screenshots:")
        dp = await asyncio.to_thread(input, "> ")
        dp = dp.strip()
        if not dp:
            dp = "Provide a general description of the photo in no more than 20 words."
            self.logger.info("No description provided. Using default prompt.")
        self.description_prompt = dp

    async def _prompt_for_mute(self) -> None:
        """
        Ask user if they want voice feedback muted.
        Skip if TTS is not available.
        """
        if not self.wh_available:
            self.logger.info("local-whisper not found. Voice feedback is disabled.")
            self.muted = True
            return

        self.logger.info("Do you want to mute voice feedback? (y/n):")
        ans = await asyncio.to_thread(input, "> ")
        self.muted = ans.lower().startswith("y")
        self.logger.info(f"Muted TTS: {self.muted}")

    async def _handle_error(self, error: Exception) -> bool:
        """
        If repeated errors exceed max_consecutive_errors, exit.
        Otherwise, attempt to continue.
        """
        self.error_count += 1
        self.logger.error(f"Live mode error: {error}")
        if self.error_count >= self.max_consecutive_errors:
            self.logger.error("Too many consecutive errors. Stopping live mode.")
            return False

        self.logger.info("Retrying after short pause...")
        await asyncio.sleep(1)
        return True
