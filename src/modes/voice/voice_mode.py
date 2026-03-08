import asyncio
import logging
import shutil

from chat.message_handler import process_task_stream


class VoiceMode:
    """
    Voice interaction mode.

    Listens via `wh listen` (local-whisper STT), sends transcript to the LLM,
    and speaks the response via `wh whisper` (local-whisper Kokoro TTS).

    Requires local-whisper to be installed and running (`wh status`).
    """

    def __init__(self, settings, messages=None, complexity_scorer=None):
        self.settings = settings
        self.messages = messages if messages else []
        self.complexity_scorer = complexity_scorer
        self.switch_requested = False
        self.logger = logging.getLogger(self.__class__.__name__)

    async def run(self):
        if not shutil.which("wh"):
            print(
                "Voice mode requires local-whisper. "
                "Install it and ensure 'wh' is on your PATH."
            )
            return

        print("\n=== Voice Mode ===")
        print("Speak when ready. Say '/quit' to exit.\n")

        while True:
            try:
                print("Listening...")
                user_text = await self._listen()
                if not user_text:
                    continue

                print(f"You: {user_text}")

                if user_text.strip().lower() in ["/quit", "/exit"]:
                    print("Exiting voice mode.")
                    break

                self.messages.append({"role": "user", "content": user_text})

                print("Thinking...")
                response = await self._get_response(user_text)

                self.messages.append({"role": "assistant", "content": response})
                print(f"Assistant: {response}\n")

                await self._speak(response)

            except KeyboardInterrupt:
                print("\nExiting voice mode.")
                break
            except Exception as e:
                self.logger.error(f"Voice mode error: {e}")

    async def _listen(self) -> str:
        """Record via wh listen and return the transcription."""
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
        """Stream LLM response and return the full text."""
        chunks = []
        async for chunk in process_task_stream(
            task_type="text",
            text_content=user_text,
            complexity_scorer=self.complexity_scorer,
            settings=self.settings,
            messages=self.messages,
        ):
            chunks.append(chunk)
        return "".join(chunks).strip()

    async def _speak(self, text: str) -> None:
        """Speak via wh whisper (local-whisper Kokoro TTS)."""
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
