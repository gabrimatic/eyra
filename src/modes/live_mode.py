"""
Watch mode — continuous screenshot analysis with a goal.

Captures screenshots at intervals, sends to AI with the watch goal,
streams responses. Returns to text mode on stop or Ctrl+C.
"""

import asyncio
import hashlib
import logging
import shutil
from typing import Optional

from .base_mode import BaseMode
from chat.message_handler import process_task_stream, prepare_image
from chat.session_state import SessionState, InteractionStyle, LastTaskMeta


class LiveMode(BaseMode):
    def __init__(
        self,
        settings,
        session: SessionState,
        complexity_scorer=None,
    ):
        super().__init__(settings)
        self.session = session
        self.complexity_scorer = complexity_scorer
        self.logger = logging.getLogger(self.__class__.__name__)

        self.wh_available = shutil.which("wh") is not None
        if not self.wh_available and not self.session.watch_voice_muted:
            self.logger.warning("local-whisper (wh) not found. Voice feedback disabled.")
            self.session.watch_voice_muted = True

    async def run(self) -> Optional[str]:
        """
        Watch loop. Returns 'text' to go back to manual mode.
        """
        self.session.interaction_style = InteractionStyle.WATCH
        self.session.watch_active = True

        goal = self.session.watch_goal
        if not goal:
            goal = await self._prompt_for_goal()
            self.session.watch_goal = goal

        if not self.session.watch_voice_muted and self.wh_available:
            mute = await asyncio.to_thread(
                input, "Mute voice feedback? (y/n): "
            )
            self.session.watch_voice_muted = mute.strip().lower().startswith("y")

        self.session.last_task = LastTaskMeta("image", goal, False)

        print(f"\nWatching: {goal}")
        print("Press Ctrl+C to stop watching.\n")

        try:
            await self._watch_loop(goal)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        self.session.watch_active = False
        print("\nWatch stopped. Returning to text mode.")
        return "text"

    async def _watch_loop(self, goal: str):
        error_count = 0
        max_errors = 3
        self._last_image_hash: Optional[str] = None
        self._last_response_hash: Optional[str] = None

        while self.session.watch_active:
            try:
                # Pre-capture screenshot and hash it to skip unchanged screens
                base64_image = await prepare_image(use_selfie=False)
                image_hash = hashlib.md5(base64_image.encode()).hexdigest()

                if image_hash == self._last_image_hash:
                    # Screen unchanged — skip the model call entirely
                    error_count = 0
                    await asyncio.sleep(self.settings.SCREENSHOT_INTERVAL)
                    continue
                self._last_image_hash = image_hash

                # Build context: shared history + current watch goal as user turn
                watch_messages = list(self.session.messages)
                watch_messages.append({"role": "user", "content": goal})

                full_response = ""
                async for chunk in process_task_stream(
                    task_type="image",
                    text_content=goal,
                    complexity_scorer=self.complexity_scorer,
                    settings=self.settings,
                    messages=watch_messages,
                    quality_mode=self.session.quality_mode,
                    interaction_style=self.session.interaction_style,
                    base64_image=base64_image,
                ):
                    full_response += chunk

                # Response-level dedupe: suppress identical model output
                response_hash = hashlib.md5(full_response.encode()).hexdigest()
                is_new_response = response_hash != self._last_response_hash
                self._last_response_hash = response_hash

                if full_response.strip() and is_new_response:
                    print(f"\nEyra: {full_response}")
                    self.session.messages.append(
                        {"role": "user", "content": goal}
                    )
                    self.session.messages.append(
                        {"role": "assistant", "content": full_response}
                    )

                    if not self.session.watch_voice_muted and self.wh_available:
                        proc = await asyncio.create_subprocess_exec(
                            "wh", "whisper", full_response,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.wait()

                error_count = 0
                await asyncio.sleep(self.settings.SCREENSHOT_INTERVAL)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                error_count += 1
                self.logger.error(f"Watch error: {e}")
                if error_count >= max_errors:
                    self.logger.error("Too many errors. Stopping watch.")
                    break
                await asyncio.sleep(1)

    async def _prompt_for_goal(self) -> str:
        default = "Describe what is on the screen in one sentence."
        goal = await asyncio.to_thread(
            input, f"Watch goal (enter for default): "
        )
        goal = goal.strip()
        if not goal:
            goal = default
            print(f"Using default: {goal}")
        return goal
