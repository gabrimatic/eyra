"""
Manual mode implementation for interactive chat.
Provides command-based interaction with image capture and analysis capabilities.
"""

import asyncio
from pynput import keyboard
from .base_mode import BaseMode
from chat.message_handler import (
    process_message_with_image,
    get_completion,
    display_history,
)


class ManualMode(BaseMode):
    """
    Manual mode implementation allowing interactive chat with commands.
    Supports image capture, history viewing, and natural conversation.
    """

    def __init__(self, client, settings, messages=None):
        super().__init__(client, settings)
        self.messages = messages if messages is not None else []
        self.switch_requested = False
        self.keyboard_listener = None
        self.current_keys = set()
        self.input_queue = asyncio.Queue()

    async def setup_keyboard_listener(self):
        def on_press(key):
            try:
                self.current_keys.add(key)
                if (
                    keyboard.Key.shift in self.current_keys
                    and keyboard.Key.ctrl in self.current_keys
                    and hasattr(key, "char")
                    and key.char == "l"
                ):
                    self.switch_requested = True
                    print("\nSwitching to live mode...")
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

    async def get_input(self):
        while True:
            if self.switch_requested:
                return None
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("\nYou: ").strip()
                )
                await self.input_queue.put(line)
            except EOFError:
                break

    async def main_loop(self):
        input_task = asyncio.create_task(self.get_input())

        try:
            while not self.switch_requested:
                try:
                    user_input = await asyncio.wait_for(
                        self.input_queue.get(),
                        timeout=0.1,  # Small timeout to check switch_requested frequently
                    )

                    # Process input as before
                    if user_input:
                        if user_input.lower() == "/quit":
                            print("\nEyra: Goodbye! Have a great day!")
                            return True
                        elif user_input.lower() == "/history":
                            display_history(self.messages)
                            continue

                        self.messages.append({"role": "user", "content": user_input})

                        if "#selfie" in user_input:
                            response = await process_message_with_image(
                                self.client,
                                self.messages,
                                self.settings.IMAGE_PATH,
                                use_selfie=True,
                            )
                        elif "#image" in user_input:
                            response = await process_message_with_image(
                                self.client,
                                self.messages,
                                self.settings.IMAGE_PATH,
                                use_selfie=False,
                            )
                        else:
                            response = get_completion(self.client, self.messages)

                        self.messages.append(
                            {"role": "assistant", "content": response.content}
                        )
                        print(f"\nEyra:", response.content)

                except asyncio.TimeoutError:
                    # This allows us to check switch_requested frequently
                    continue

        finally:
            input_task.cancel()
            try:
                await input_task
            except asyncio.CancelledError:
                pass

        return False

    async def run(self):
        while True:
            # Reset state at the beginning of each run
            self.switch_requested = False
            self.current_keys = set()

            print(
                "Manual mode started. Commands:\n- '#image': Capture and include a new screenshot\n- '#selfie': Capture and include webcam image\n- '/history': Show chat history\n- 'Ctrl+Shift+L': Switch to live mode\n- '/quit': Exit"
            )

            await self.setup_keyboard_listener()

            try:
                should_exit = await self.main_loop()

                if self.switch_requested:
                    from .live_mode import LiveMode

                    live_mode = LiveMode(self.client, self.settings, self.messages)
                    await live_mode.run()
                    if live_mode.switch_requested:
                        continue

                if should_exit:
                    break

            finally:
                if self.keyboard_listener:
                    self.keyboard_listener.stop()
                    self.keyboard_listener = None
