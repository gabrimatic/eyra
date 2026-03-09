"""
Voice mode — speech input/output via local-whisper.

Listens via `wh listen`, sends transcript to LLM, speaks response
via `wh whisper`. Returns to text mode on exit.
"""

import asyncio
import logging
import shutil
from typing import Optional

from chat.message_handler import process_task_stream
from chat.session_state import SessionState, InteractionStyle, LastTaskMeta


class VoiceMode:
    def __init__(self, settings, session: SessionState, complexity_scorer=None):
        self.settings = settings
        self.session = session
        self.complexity_scorer = complexity_scorer
        self.logger = logging.getLogger(self.__class__.__name__)

    async def run(self) -> Optional[str]:
        """
        Voice loop. Returns 'text' to go back to manual mode,
        None to exit the app.
        """
        if not shutil.which("wh"):
            print(
                "Voice mode requires local-whisper. "
                "Install it and ensure 'wh' is on your PATH."
            )
            return "text"

        self.session.interaction_style = InteractionStyle.VOICE

        print("\nVoice Mode")
        print("Speak when ready. Say 'quit' or press Ctrl+C to return to text.\n")

        consecutive_errors = 0
        max_consecutive_errors = 5

        try:
            while True:
                print("Listening...")
                user_text = await self._listen()
                if not user_text:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        self.logger.error(
                            "Too many consecutive listen failures. Returning to text mode."
                        )
                        print("Listening failed repeatedly. Returning to text mode.")
                        break
                    continue
                consecutive_errors = 0

                print(f"You: {user_text}")

                lower = user_text.strip().lower()
                if lower in ("/quit", "/exit", "quit", "exit", "stop"):
                    break

                self.session.messages.append({"role": "user", "content": user_text})
                self.session.last_task = LastTaskMeta("text", user_text, False)

                print("Thinking...")
                response = await self._get_response(user_text)

                if response:
                    self.session.messages.append(
                        {"role": "assistant", "content": response}
                    )
                    print(f"Eyra: {response}\n")
                    await self._speak(response)

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        print("\nReturning to text mode.")
        return "text"

    async def _listen(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "wh", "listen", "--raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip()
        except Exception as e:
            self.logger.error(f"wh listen failed: {e}")
            return ""

    async def _get_response(self, user_text: str) -> str:
        chunks = []
        async for chunk in process_task_stream(
            task_type="text",
            text_content=user_text,
            complexity_scorer=self.complexity_scorer,
            settings=self.settings,
            messages=self.session.messages,
            quality_mode=self.session.quality_mode,
            interaction_style=self.session.interaction_style,
        ):
            chunks.append(chunk)
        return "".join(chunks).strip()

    async def _speak(self, text: str) -> None:
        if not text.strip():
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "wh", "whisper", text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            self.logger.error(f"wh whisper failed: {e}")
