"""
Live mode implementation for continuous screen analysis.
Provides automated screenshot capture and analysis with voice feedback.
"""

import asyncio
from datetime import datetime
from pynput import keyboard
from .base_mode import BaseMode
from chat.message_handler import process_message_with_image
from utils.text_to_speech import speak_text


class LiveMode(BaseMode):
    """
    Live mode implementation for continuous screen analysis.
    Captures and analyzes screenshots at regular intervals.
    """

    def __init__(self, client, settings, messages=None):
        super().__init__(client, settings)
        self.messages = messages if messages is not None else []
        self.switch_requested = False
        self.keyboard_listener = None
        self.current_keys = set()
        self.running = True

    async def setup_keyboard_listener(self):
        def on_press(key):
            try:
                self.current_keys.add(key)
                if (
                    keyboard.Key.shift in self.current_keys
                    and keyboard.Key.ctrl in self.current_keys
                    and hasattr(key, "char")
                    and key.char == "m"
                ):
                    self.switch_requested = True
                    self.running = False
                    print("\nSwitching to manual mode...")
                    return False
            except AttributeError:
                pass

        def on_release(key):
            try:
                self.current_keys.discard(key)
            except KeyError:
                pass

        self.keyboard_listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self.keyboard_listener.start()

    async def main_loop(self):
        while self.running:
            try:
                if self.switch_requested:
                    break

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{timestamp}] Capturing screenshot...")

                self.messages.append(
                    {
                        "role": "user",
                        "content": "Provide a general description of the photo in no more than 20 words. Keep it concise.",
                    }
                )

                response = await process_message_with_image(
                    self.client,
                    self.messages,
                    self.settings.IMAGE_PATH,
                    use_selfie=False,
                )

                self.messages.append({"role": "assistant", "content": response.content})
                print(f"[{timestamp}] Eyra: {response.content}")

                print("[Speaking...]")
                await speak_text(response.content)
                print("[Speech completed]")
                await asyncio.sleep(
                    0.4
                )  # Small delay to prevent rapid screenshot capture

            except KeyboardInterrupt:
                print("\nExiting live mode...")
                break

    async def run(self):
        while True:
            # Reset state at the beginning of each run
            self.running = True
            self.switch_requested = False
            self.current_keys = set()

            print(
                "Live mode started. Press Ctrl+Shift+M to switch to manual mode, or Ctrl+C to exit."
            )

            await self.setup_keyboard_listener()

            try:
                await self.main_loop()

                if self.switch_requested:
                    from .manual_mode import ManualMode

                    manual_mode = ManualMode(self.client, self.settings, self.messages)
                    await manual_mode.run()

                    # Check if we need to restart live mode
                    if manual_mode.switch_requested:
                        continue  # Restart live mode
                    else:
                        break  # Exit live mode
                else:
                    break  # Exit live mode if not switching
            finally:
                if self.keyboard_listener:
                    self.keyboard_listener.stop()
                    self.keyboard_listener = None
