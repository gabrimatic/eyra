"""Unified live session orchestrator."""

import asyncio
import hashlib
import logging
import re
import time
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import QualityMode, InteractionStyle
from runtime.models import (
    LiveRuntimeState,
    PreflightResult,
    RuntimeStatus,
    ObservationEvent,
)
from runtime.screen_observer import ScreenObserver
from runtime.speech_controller import SpeechController, ServiceBusyError
from runtime.status_presenter import render_header, print_status_change, status_line
from utils.image_history import manage_message_history
from utils.settings import Settings

logger = logging.getLogger(__name__)

CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
DIM = "\033[2m"
BOLD = "\033[1m"
NC = "\033[0m"

_COMMANDS = {
    "/pause", "/resume", "/mute", "/unmute",
    "/goal", "/status", "/quit", "/clear",
    "/mode", "/inspect",
}

_QUIT_WORDS = {"quit", "exit", "bye", "goodbye", "q"}

# Screen-intent detection: only match UI-specific nouns or
# action verbs with a visual direct object.
_UI_NOUNS = (
    r"screen|display|window|app|browser|tab|page|"
    r"button|menu|dialog|popup|modal|sidebar|toolbar|"
    r"notification|icon|cursor|selection|highlight"
)
_SCREEN_CUES = re.compile(
    rf"\b({_UI_NOUNS})\b"
    r"|"
    rf"\b(look\s+at|show\s+me|read\s+the|text\s+on|code\s+on|what'?s\s+on)\s+(the\s+)?({_UI_NOUNS}|this|that|it|here)\b"
    r"|"
    r"\b(what\s+is\s+(this|that)|what'?s\s+(this|that)|see\s+(this|that|here)|explain\s+(this|that))\b",
    re.I,
)


class LiveSession:
    def __init__(
        self,
        settings: Settings,
        preflight: PreflightResult,
        state: LiveRuntimeState,
        complexity_scorer: ComplexityScorer,
    ):
        self.settings = settings
        self.preflight = preflight
        self.state = state
        self.scorer = complexity_scorer
        self.observer = ScreenObserver(state, debounce_ms=settings.OBSERVATION_DEBOUNCE_MS)
        self.speech = SpeechController(state, cooldown_ms=settings.SPEECH_COOLDOWN_MS)
        self.quality_mode = QualityMode.BALANCED
        self._prompt = PromptSession()
        self._shutdown = asyncio.Event()
        # Coordination: set when the session is actively processing
        # (streaming a response, speaking, handling user input).
        # Observation and voice loops yield when this is set.
        self._busy = asyncio.Event()

    def _default_status(self) -> RuntimeStatus:
        if self.state.observing and not self.state.paused:
            return RuntimeStatus.OBSERVING
        return RuntimeStatus.IDLE

    async def run(self):
        """Main entry point. Runs until quit."""
        self.state.current_status = self._default_status()
        render_header(self.state)

        tasks = [
            asyncio.create_task(self._input_loop(), name="input"),
        ]
        if self.settings.LIVE_OBSERVATION_ENABLED and self.state.observing:
            tasks.append(asyncio.create_task(self._observation_loop(), name="observe"))
        if self.state.listening_enabled:
            tasks.append(asyncio.create_task(self._voice_input_loop(), name="voice"))

        try:
            await self._shutdown.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.speech.interrupt()
            print(f"\n{DIM}Session ended.{NC}\n")

    # ── Input loop ────────────────────────────────────────────────────────

    async def _input_loop(self):
        try:
            while not self._shutdown.is_set():
                try:
                    user_input = await self._prompt.prompt_async(
                        ANSI(f"{DIM}You:{NC} "),
                    )
                except EOFError:
                    self._shutdown.set()
                    return

                user_input = user_input.strip()
                if not user_input:
                    continue

                await self.speech.interrupt()
                self.state.last_user_input_at = time.time()

                if user_input.startswith("/"):
                    if await self._handle_command(user_input):
                        continue

                if user_input.lower() in _QUIT_WORDS:
                    print("Goodbye!")
                    self._shutdown.set()
                    return

                await self._handle_user_input(user_input)

        except asyncio.CancelledError:
            return

    # ── Voice input loop ──────────────────────────────────────────────────

    async def _voice_input_loop(self):
        consecutive_errors = 0
        max_errors = 5

        try:
            while not self._shutdown.is_set():
                if not self.state.listening_enabled:
                    break

                # Yield while the session is busy (streaming, speaking, etc.)
                if self._busy.is_set():
                    await asyncio.sleep(0.5)
                    continue

                # Don't listen while speech is playing
                if self.speech.is_speaking:
                    await asyncio.sleep(0.5)
                    continue

                self.state.current_status = RuntimeStatus.LISTENING
                try:
                    text = await self.speech.listen()
                except ServiceBusyError:
                    logger.debug("Service busy, backing off")
                    await asyncio.sleep(2)
                    continue
                except Exception as e:
                    consecutive_errors += 1
                    logger.debug("Voice listen error (%d/%d): %s", consecutive_errors, max_errors, e)
                    if consecutive_errors >= max_errors:
                        logger.info("Voice input disabled after %d consecutive errors", max_errors)
                        self.state.listening_enabled = False
                        print_status_change("Voice input disabled after repeated errors")
                        break
                    await asyncio.sleep(2)
                    continue

                consecutive_errors = 0

                if not text:
                    continue

                await self.speech.interrupt()
                self.state.last_user_input_at = time.time()

                if text.startswith("/"):
                    await self._handle_command(text)
                    continue

                if text.lower() in _QUIT_WORDS:
                    print("Goodbye!")
                    self._shutdown.set()
                    return

                print(f"  {DIM}You (voice):{NC} {text}")
                await self._handle_user_input(text, interaction_style=InteractionStyle.VOICE)

        except asyncio.CancelledError:
            return

    # ── Command handling ──────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> bool:
        lower = cmd.lower().strip()
        parts = lower.split(maxsplit=1)
        command = parts[0]

        if command == "/quit":
            print("Goodbye!")
            self._shutdown.set()
            return True

        if command == "/pause":
            self.state.paused = True
            self.state.current_status = RuntimeStatus.PAUSED
            print_status_change("Observation paused")
            return True

        if command == "/resume":
            self.state.paused = False
            self.state.current_status = self._default_status()
            print_status_change("Observation resumed")
            return True

        if command == "/mute":
            self.state.speech_muted = True
            print_status_change("Speech muted")
            return True

        if command == "/unmute":
            self.state.speech_muted = False
            print_status_change("Speech unmuted")
            return True

        if command == "/goal":
            goal = parts[1] if len(parts) > 1 else None
            if goal:
                self.state.current_goal = goal
                print_status_change(f"Goal: {goal}")
            else:
                if self.state.current_goal:
                    print_status_change(f"Current goal: {self.state.current_goal}")
                else:
                    print_status_change("No goal set. Usage: /goal <description>")
            return True

        if command == "/status":
            self._print_status()
            return True

        if command == "/clear":
            self.state.conversation_messages.clear()
            self.state.last_screen_summary = None
            self.state.last_response_hash = None
            print_status_change("Session cleared")
            return True

        if command == "/mode":
            mode_str = parts[1] if len(parts) > 1 else ""
            try:
                self.quality_mode = QualityMode(mode_str)
                print_status_change(f"Quality: {self.quality_mode.value}")
            except ValueError:
                print(f"  Usage: /mode fast|balanced|best")
            return True

        if command == "/inspect":
            await self._inspect_screen()
            return True

        print(f"  Unknown command: {command}")
        return True

    def _print_status(self):
        status = status_line(self.state)
        obs = "on" if self.state.observing and not self.state.paused else "paused" if self.state.paused else "off"
        listen = "on" if self.state.listening_enabled else "off"
        speech = "muted" if self.state.speech_muted else "on" if self.state.speech_enabled else "off"
        goal = self.state.current_goal or "none"
        msgs = len(self.state.conversation_messages)
        print(f"\n  Status: {status}  Observation: {obs}  Listening: {listen}  Speech: {speech}")
        print(f"  Goal: {goal}  Quality: {self.quality_mode.value}  History: {msgs} messages\n")

    # ── Screen context detection ──────────────────────────────────────────

    def _needs_screen_context(self, text: str) -> bool:
        if _SCREEN_CUES.search(text):
            return True

        msgs = self.state.conversation_messages
        if len(msgs) >= 2:
            last_assistant = None
            for m in reversed(msgs[:-1]):
                if m.get("role") == "assistant":
                    last_assistant = m.get("content", "")
                    break
            if last_assistant and self.state.last_screen_summary:
                if last_assistant.strip() == self.state.last_screen_summary.strip():
                    return True

        return False

    # ── User input handling ───────────────────────────────────────────────

    async def _handle_user_input(self, text: str, interaction_style: InteractionStyle = InteractionStyle.TEXT):
        self.state.conversation_messages.append({"role": "user", "content": text})

        needs_screen = self._needs_screen_context(text)

        if needs_screen:
            base64_image = await self.observer.capture_full()
            await self._stream_response(
                task_type="image",
                text_content=text,
                base64_image=base64_image,
                interaction_style=interaction_style,
            )
        else:
            await self._stream_response(
                task_type="text",
                text_content=text,
                interaction_style=interaction_style,
            )

    async def _inspect_screen(self):
        print_status_change("Capturing screen...")
        base64_image = await self.observer.capture_full()
        if not base64_image:
            print("  Could not capture screen.")
            return

        prompt = "Describe what is on this screen."
        if self.state.current_goal:
            prompt = f"{prompt} Focus on: {self.state.current_goal}"

        self.state.conversation_messages.append({"role": "user", "content": prompt})

        await self._stream_response(
            task_type="image",
            text_content=prompt,
            base64_image=base64_image,
        )

    # ── Observation loop ──────────────────────────────────────────────────

    async def _observation_loop(self):
        cooldown_s = self.settings.OBSERVATION_COOLDOWN_MS / 1000.0
        startup_time = time.time()

        # Grace period: ignore screen changes from the app's own startup output.
        # Let the fingerprint baseline stabilize before reacting to changes.
        grace_s = max(cooldown_s, 5.0)
        await asyncio.sleep(grace_s)

        # Take a fresh baseline fingerprint after grace period
        try:
            await self.observer.check()
        except Exception:
            pass

        try:
            while not self._shutdown.is_set():
                if self.state.paused:
                    await asyncio.sleep(0.5)
                    continue

                # Don't observe while the session is actively responding or speaking
                if self._busy.is_set() or self.speech.is_speaking:
                    await asyncio.sleep(1)
                    continue

                # Don't observe too soon after user input (they're likely still reading)
                if self.state.last_user_input_at:
                    since_input = time.time() - self.state.last_user_input_at
                    if since_input < cooldown_s:
                        await asyncio.sleep(0.5)
                        continue

                event = await self.observer.check()

                if event and event.material_change:
                    if self.state.last_observation_at:
                        elapsed = time.time() - self.state.last_observation_at
                        if elapsed < cooldown_s:
                            await asyncio.sleep(0.5)
                            continue

                    # Double-check not busy (could have changed during check)
                    if self._busy.is_set():
                        continue

                    self.state.current_status = RuntimeStatus.CHANGE_DETECTED
                    logger.debug("Change: %s", event.reason)

                    base64_image = await self.observer.capture_full()
                    if base64_image:
                        await self._handle_observation(base64_image, event)

                    self.state.last_observation_at = time.time()
                    self.state.current_status = self._default_status()

                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            return

    async def _handle_observation(self, base64_image: str, event: ObservationEvent):
        self._busy.set()
        try:
            self.state.current_status = RuntimeStatus.ANALYZING

            prompt = "Briefly describe what changed on this screen."
            if self.state.current_goal:
                prompt = (
                    f"You are watching the screen. Goal: {self.state.current_goal}. "
                    f"Briefly describe what you see that is relevant. "
                    f"If nothing relevant, respond with just 'nothing relevant'."
                )

            messages = []
            if self.state.last_screen_summary:
                messages.append({"role": "assistant", "content": f"Previous screen: {self.state.last_screen_summary}"})
            messages.append({"role": "user", "content": prompt})

            full_response = ""
            async for chunk in process_task_stream(
                task_type="image",
                text_content=prompt,
                complexity_scorer=self.scorer,
                settings=self.settings,
                messages=messages,
                quality_mode=QualityMode.FAST,
                interaction_style=InteractionStyle.WATCH,
                base64_image=base64_image,
            ):
                full_response += chunk

            if not full_response.strip():
                return

            response_hash = hashlib.md5(full_response.encode()).hexdigest()
            if response_hash == self.state.last_response_hash:
                return
            self.state.last_response_hash = response_hash

            lower = full_response.strip().lower()
            if lower in ("nothing relevant", "nothing relevant.", "no changes", "no changes."):
                return

            self.state.last_screen_summary = full_response.strip()
            print(f"\n  {CYAN}Eyra:{NC} {full_response.strip()}\n", flush=True)

            self.state.conversation_messages.append({"role": "user", "content": prompt})
            self.state.conversation_messages.append({"role": "assistant", "content": full_response.strip()})

            self.state.current_status = RuntimeStatus.SPEAKING
            await self.speech.speak(full_response.strip())
            # Don't block on speech here — let it play in background
            # The voice loop will wait via is_speaking check
            self.state.current_status = self._default_status()
        finally:
            self._busy.clear()

    # ── Shared streaming ──────────────────────────────────────────────────

    async def _stream_response(
        self,
        task_type: str,
        text_content: str,
        base64_image: Optional[str] = None,
        interaction_style: InteractionStyle = InteractionStyle.TEXT,
    ):
        self._busy.set()
        try:
            self.state.current_status = RuntimeStatus.THINKING

            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            full_response = ""
            first_token = False

            async def spin():
                i = 0
                while True:
                    print(f"\r  {DIM}Eyra: {frames[i % len(frames)]}{NC}", end="", flush=True)
                    i += 1
                    await asyncio.sleep(0.08)

            spinner = asyncio.create_task(spin())

            try:
                async for chunk in process_task_stream(
                    task_type=task_type,
                    text_content=text_content,
                    complexity_scorer=self.scorer,
                    settings=self.settings,
                    messages=list(self.state.conversation_messages),
                    quality_mode=self.quality_mode,
                    interaction_style=interaction_style,
                    base64_image=base64_image,
                ):
                    if not first_token:
                        first_token = True
                        spinner.cancel()
                        print(f"\r\033[2K\n  {CYAN}Eyra:{NC} ", end="", flush=True)
                    print(chunk, end="", flush=True)
                    full_response += chunk
            finally:
                if not spinner.done():
                    spinner.cancel()

            if not first_token:
                print(f"\r\033[2K", end="")

            print("\n")

            if full_response.strip():
                self.state.conversation_messages.append({"role": "assistant", "content": full_response})
                self.state.current_status = RuntimeStatus.SPEAKING
                await self.speech.speak(full_response.strip()[:200])
                # Don't block on speech — let it play while user reads
                # Voice loop will wait via is_speaking check

            self.state.current_status = self._default_status()
        finally:
            self._busy.clear()
