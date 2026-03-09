"""
Manual mode — interactive text-based chat with command handling.

Returns a string indicating the next interaction style to switch to,
or None to exit the app.
"""

import asyncio
import logging
import re
from typing import Optional

from .base_mode import BaseMode
from chat.message_handler import process_task_stream
from chat.session_state import SessionState, QualityMode, InteractionStyle, LastTaskMeta
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.completion import WordCompleter

# Task shortcut mappings: command -> (prompt, uses_image)
TASK_SHORTCUTS = {
    "#explain": ("Explain what is on this screen in detail.", True),
    "#extract": ("Extract all visible text from this screenshot.", True),
    "#summarize": ("Summarize the key content visible on this screen.", True),
    "#review": ("Review this UI for usability, layout, and design issues.", True),
    "#bug": ("Find any bugs, errors, or issues visible in this screenshot.", True),
    "#compare": ("Describe what is on this screen and highlight anything that differs from the prior conversation context.", True),
}


class ManualMode(BaseMode):
    def __init__(
        self,
        settings,
        session: SessionState,
        complexity_scorer=None,
    ):
        super().__init__(settings)
        self.session = session
        self.complexity_scorer = complexity_scorer

        commands = [
            "/quit", "/history", "/status", "/clear",
            "/mode fast", "/mode balanced", "/mode best",
            "/retry", "/retry best",
            "/watch", "/voice",
            "#image", "#selfie",
        ] + list(TASK_SHORTCUTS.keys())

        self.command_completer = WordCompleter(commands, ignore_case=True)
        self.prompt_style = Style.from_dict({"prompt": "ansigreen bold"})
        self.prompt_session = PromptSession(
            completer=self.command_completer,
            style=self.prompt_style,
            complete_while_typing=True,
            enable_suspend=True,
            color_depth="DEPTH_24_BIT",
        )
        self.logger = logging.getLogger(self.__class__.__name__)

    async def run(self) -> Optional[str]:
        """
        Main loop. Returns next interaction style string or None to exit.
        """
        self.session.interaction_style = InteractionStyle.TEXT

        try:
            while True:
                user_input = await self.prompt_session.prompt_async(
                    "You: ", complete_while_typing=True
                )
                user_input = user_input.strip()
                if not user_input:
                    continue

                # --- Commands ---
                lower = user_input.lower()

                if lower == "/quit":
                    print("Goodbye!")
                    return None

                if lower == "/history":
                    self._show_history()
                    continue

                if lower == "/status":
                    print(f"\n  {self.session.status_summary()}\n")
                    continue

                if lower == "/clear":
                    self.session.clear()
                    print("Session cleared.")
                    continue

                if lower.startswith("/mode "):
                    self._handle_mode_command(lower)
                    continue

                if lower == "/retry" or lower == "/retry best":
                    await self._handle_retry(force_best=lower.endswith("best"))
                    continue

                if lower.startswith("/watch"):
                    goal = user_input[6:].strip() or None
                    self.session.watch_active = True
                    self.session.watch_goal = goal
                    return "watch"

                if lower == "/voice":
                    return "voice"

                # --- Task shortcuts ---
                shortcut = self.resolve_shortcut(user_input)
                if shortcut is not None:
                    prompt, uses_image = shortcut
                    self.session.messages.append({"role": "user", "content": prompt})
                    if uses_image:
                        self.session.last_task = LastTaskMeta("image", prompt, False)
                        await self._handle_image_task(prompt, use_selfie=False)
                    else:
                        self.session.last_task = LastTaskMeta("text", prompt, False)
                        await self._handle_text_task(prompt)
                    print("\n" + "=" * 50 + "\n")
                    continue

                # --- Image commands ---
                if "#selfie" in lower or "#image" in lower:
                    prompt, use_selfie = self.normalize_image_input(user_input)
                    self.session.messages.append({"role": "user", "content": prompt})
                    self.session.last_task = LastTaskMeta("image", prompt, use_selfie)
                    await self._handle_image_task(prompt, use_selfie=use_selfie)
                    print("\n" + "=" * 50 + "\n")
                    continue

                # --- Normal text ---
                self.session.messages.append({"role": "user", "content": user_input})
                self.session.last_task = LastTaskMeta("text", user_input, False)
                await self._handle_text_task(user_input)
                print("\n" + "=" * 50 + "\n")

        except (KeyboardInterrupt, asyncio.CancelledError, EOFError):
            return None

    @staticmethod
    def normalize_image_input(user_input: str) -> tuple:
        """
        Strip #image/#selfie markers from input (case-insensitive) and
        return (prompt, use_selfie). Bare markers become a default instruction.
        """
        lower = user_input.lower()
        use_selfie = "#selfie" in lower
        prompt = re.sub(r"#(?:selfie|image)", "", user_input, flags=re.IGNORECASE).strip()
        if not prompt:
            prompt = "Describe this image."
        return prompt, use_selfie

    def resolve_shortcut(self, user_input: str):
        """
        If user_input starts with a task shortcut, return (prompt, uses_image).
        Returns None if not a shortcut.
        """
        lower = user_input.lower()
        first_word = lower.split()[0] if lower.split() else ""
        if first_word not in TASK_SHORTCUTS:
            return None
        prompt, uses_image = TASK_SHORTCUTS[first_word]
        extra = user_input[len(first_word):].strip()
        if extra:
            prompt = f"{prompt} {extra}"
        return prompt, uses_image

    def prepare_retry(self, force_best: bool = False):
        """
        Prepare session state for a retry. Returns the LastTaskMeta to replay,
        or None if nothing to retry. Appends a fresh user turn and temporarily
        overrides quality mode if force_best is True.
        """
        if not self.session.last_task or not self.session.last_task.text_content:
            return None

        meta = self.session.last_task

        if force_best:
            self._saved_quality_mode = self.session.quality_mode
            self.session.quality_mode = QualityMode.BEST

        # Append a fresh user turn so the model sees a new request,
        # not the old [user, assistant] pair with no new prompt.
        self.session.messages.append({"role": "user", "content": meta.text_content})
        return meta

    def finish_retry(self, force_best: bool):
        """Restore quality mode after a retry if it was temporarily overridden."""
        if force_best and hasattr(self, "_saved_quality_mode"):
            self.session.quality_mode = self._saved_quality_mode
            del self._saved_quality_mode

    def _handle_mode_command(self, lower: str):
        mode_str = lower.split()[-1]
        try:
            self.session.quality_mode = QualityMode(mode_str)
            print(f"Quality mode set to: {self.session.quality_mode.value}")
        except ValueError:
            print("Usage: /mode fast|balanced|best")

    async def _handle_retry(self, force_best: bool = False):
        meta = self.prepare_retry(force_best)
        if not meta:
            print("Nothing to retry.")
            return

        preview = meta.text_content[:80]
        if len(meta.text_content) > 80:
            preview += "..."
        print(f"Retrying: {preview}")

        if meta.task_type == "image":
            await self._handle_image_task(meta.text_content, use_selfie=meta.use_selfie)
        else:
            await self._handle_text_task(meta.text_content)

        self.finish_retry(force_best)
        print("\n" + "=" * 50 + "\n")

    async def _stream_with_spinner(self, stream):
        """Consume a chunk stream, showing a spinner until the first real token arrives."""
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        full_response = ""
        first_token = False
        spinner_task = None

        async def spin():
            i = 0
            while True:
                print(f"\r\033[2mEyra: {frames[i % len(frames)]}\033[0m", end="", flush=True)
                i += 1
                await asyncio.sleep(0.08)

        spinner_task = asyncio.create_task(spin())

        try:
            async for chunk in stream:
                if not first_token:
                    first_token = True
                    spinner_task.cancel()
                    print(f"\r\033[2K\nEyra: ", end="", flush=True)
                print(chunk, end="", flush=True)
                full_response += chunk
        finally:
            if not spinner_task.done():
                spinner_task.cancel()

        if not first_token:
            spinner_task.cancel()
            print(f"\r\033[2K", end="")

        print()
        return full_response

    async def _handle_image_task(self, user_input: str, use_selfie: bool = False):
        stream = process_task_stream(
            task_type="image",
            text_content=user_input,
            complexity_scorer=self.complexity_scorer,
            settings=self.settings,
            messages=self.session.messages,
            use_selfie=use_selfie,
            quality_mode=self.session.quality_mode,
            interaction_style=self.session.interaction_style,
        )
        full_response = await self._stream_with_spinner(stream)
        if full_response:
            self.session.messages.append({"role": "assistant", "content": full_response})
        else:
            print("No response received.")

    async def _handle_text_task(self, user_input: str):
        stream = process_task_stream(
            task_type="text",
            text_content=user_input,
            complexity_scorer=self.complexity_scorer,
            settings=self.settings,
            messages=self.session.messages,
            quality_mode=self.session.quality_mode,
            interaction_style=self.session.interaction_style,
        )
        full_response = await self._stream_with_spinner(stream)
        if full_response:
            self.session.messages.append({"role": "assistant", "content": full_response})
        else:
            print("No response received.")

    def _show_history(self):
        print("\nChat History:")
        for msg in self.session.messages:
            role = msg.get("role", "Unknown").title()
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    item.get("text", "") for item in content if item.get("type") == "text"
                )
            print(f"  {role}: {content}")
        print()
