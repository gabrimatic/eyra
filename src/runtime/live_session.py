"""Unified live session orchestrator."""

import asyncio
import logging
import re
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import InteractionStyle, QualityMode
from clients.ai_client import THINK_END, THINK_START
from runtime.models import LiveRuntimeState, PreflightResult, RuntimeStatus
from runtime.preflight import WH_INSTALL_HINT
from runtime.speech_controller import SpeechController
from runtime.status_presenter import (
    print_status_change,
    render_header,
    render_help_card,
    render_status_card,
)
from tools.browser import BrowserSession, ClickElementTool, OpenUrlTool, PageScreenshotTool, WebSearchTool
from tools.clipboard import ClipboardTool
from tools.filesystem import (
    CreateDirectoryTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
    parse_allowed_roots,
)
from tools.registry import ToolRegistry
from tools.screenshot import ScreenshotTool
from tools.system_info import SystemInfoTool
from tools.time_tool import TimeTool
from tools.weather import WeatherTool
from utils.settings import Settings
from utils.sound_player import play_sound
from utils.theme import CYAN, DIM, DIM_ITALIC, NC

logger = logging.getLogger(__name__)

_COMMANDS = {
    "/voice", "/mute", "/unmute",
    "/goal", "/status", "/quit", "/clear",
    "/mode", "/help",
}

_QUIT_WORDS = {"quit", "exit", "bye", "goodbye", "q"}

# Split streamed chunks on think-block sentinels so the renderer can style them.
_THINK_SPLIT = re.compile(f"({re.escape(THINK_START)}|{re.escape(THINK_END)})")

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
        self.speech = SpeechController(
            state,
            cooldown_ms=settings.SPEECH_COOLDOWN_MS,
            silence_duration_ms=settings.VOICE_SILENCE_MS,
            vad_threshold=settings.VOICE_VAD_THRESHOLD,
        )
        self.quality_mode = QualityMode.BALANCED
        self._prompt = PromptSession()
        self._shutdown = asyncio.Event()
        # Coordination: set when the session is actively processing
        # (streaming a response, speaking, handling user input).
        # Voice loop yields when this is set.
        self._busy = asyncio.Event()
        self._browser_session = BrowserSession()
        self._tool_registry = self._build_tool_registry()

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(TimeTool())
        registry.register(WeatherTool())
        registry.register(ClipboardTool())
        registry.register(SystemInfoTool())
        registry.register(ScreenshotTool())
        registry.register(WebSearchTool(session=self._browser_session))
        registry.register(OpenUrlTool(session=self._browser_session))
        registry.register(ClickElementTool(session=self._browser_session))
        registry.register(PageScreenshotTool(session=self._browser_session))
        fs_roots = parse_allowed_roots(self.settings.FILESYSTEM_ALLOWED_PATHS)
        registry.register(ReadFileTool(allowed_roots=fs_roots))
        registry.register(WriteFileTool(allowed_roots=fs_roots))
        registry.register(EditFileTool(allowed_roots=fs_roots))
        registry.register(ListDirectoryTool(allowed_roots=fs_roots))
        registry.register(CreateDirectoryTool(allowed_roots=fs_roots))
        return registry

    async def run(self):
        """Main entry point. Runs until quit."""
        self.state.current_status = RuntimeStatus.IDLE
        render_header(self.state, self.settings)

        tasks = [
            asyncio.create_task(self._input_loop(), name="input"),
        ]
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
            await self._browser_session.close()
            print("\n  Goodbye.\n")

    # ── Input loop ────────────────────────────────────────────────────────

    async def _input_loop(self):
        try:
            while not self._shutdown.is_set():
                try:
                    user_input = await self._prompt.prompt_async(
                        ANSI(f"{CYAN}›{NC} "),
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
                    self._shutdown.set()
                    return

                await self._handle_user_input(user_input)

        except asyncio.CancelledError:
            return

    # ── Voice input loop ──────────────────────────────────────────────────

    async def _voice_input_loop(self):
        """Continuously listen for voice input using Silero VAD.

        The mic stays open. Each 32ms frame (512 samples at 16 kHz) is
        classified as speech or non-speech by the VAD. When speech is
        detected, it records until the speaker pauses, then transcribes
        via Local Whisper and processes the result. No fixed recording
        window, no hard timeout.
        """
        consecutive_errors = 0
        max_errors = 5

        try:
            while not self._shutdown.is_set():
                if not self.state.listening_enabled:
                    break

                # Wait while the session is busy or speaking before opening the mic
                if self._busy.is_set() or self.speech.is_speaking:
                    await asyncio.sleep(0.2)
                    continue

                self.state.current_status = RuntimeStatus.LISTENING
                try:
                    # This blocks until speech is detected and transcribed,
                    # or until cancelled (by _stream_response or shutdown).
                    text = await self.speech.listen()
                except Exception as e:
                    consecutive_errors += 1
                    logger.debug("Voice listen error (%d/%d): %s", consecutive_errors, max_errors, e)
                    if consecutive_errors >= max_errors:
                        logger.info("Voice input disabled after %d consecutive errors", max_errors)
                        self.state.listening_enabled = False
                        print_status_change("Voice input disabled after repeated errors")
                        break
                    await asyncio.sleep(1)
                    continue

                consecutive_errors = 0

                # None means silence, cancellation, or no speech detected
                if not text:
                    continue

                await self.speech.interrupt()
                self.state.last_user_input_at = time.time()

                if text.startswith("/"):
                    await self._handle_command(text)
                    continue

                if text.lower() in _QUIT_WORDS:
                    self._shutdown.set()
                    return

                await play_sound("listen")
                print(f"\r\033[2K  {DIM}(voice){NC} {text}")
                await self._handle_user_input(text, interaction_style=InteractionStyle.VOICE)

        except asyncio.CancelledError:
            self.speech.cancel_listen()
            return

    # ── Command handling ──────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> bool:
        lower = cmd.lower().strip()
        parts = lower.split(maxsplit=1)
        command = parts[0]

        if command == "/quit":
            self._shutdown.set()
            return True

        if command == "/voice":
            arg = parts[1] if len(parts) > 1 else ""
            if arg == "on":
                if not self.preflight.wh_available:
                    print("  Local Whisper is not available.")
                    print(f"  {DIM}Install: {WH_INSTALL_HINT}{NC}")
                    return True
                self.state.listening_enabled = True
                self.state.speech_enabled = True
                # Spawn voice loop if not already running
                voice_task = next((t for t in asyncio.all_tasks() if t.get_name() == "voice"), None)
                if voice_task is None or voice_task.done():
                    asyncio.create_task(self._voice_input_loop(), name="voice")
                print_status_change("Voice on (input + speech)")
            elif arg == "off":
                self.state.listening_enabled = False
                self.state.speech_enabled = False
                await self.speech.interrupt()
                self.speech.cancel_listen()
                print_status_change("Voice off")
            else:
                voice_status = "on" if self.state.listening_enabled else "off"
                print(f"  Voice is {voice_status}. Usage: /voice on|off")
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
            print_status_change("Session cleared")
            return True

        if command == "/mode":
            mode_str = parts[1] if len(parts) > 1 else ""
            try:
                self.quality_mode = QualityMode(mode_str)
                print_status_change(f"Quality: {self.quality_mode.value}")
            except ValueError:
                print("  Usage: /mode fast|balanced|best")
            return True

        if command == "/help":
            render_help_card()
            return True

        print(f"  Unknown command: {command}")
        return True

    def _print_status(self):
        model_name = getattr(self.settings, "MODEL", "")
        render_status_card(
            state=self.state,
            quality_mode_value=self.quality_mode.value,
            tool_count=len(self._tool_registry.to_openai_tools()),
            msg_count=len(self.state.conversation_messages),
            model_name=model_name or "",
        )

    # ── Screen context detection ──────────────────────────────────────────

    def _needs_screen_context(self, text: str) -> bool:
        return bool(_SCREEN_CUES.search(text))

    # ── User input handling ───────────────────────────────────────────────

    async def _handle_user_input(self, text: str, interaction_style: InteractionStyle = InteractionStyle.TEXT):
        self.state.conversation_messages.append({"role": "user", "content": text})
        quality = self.quality_mode
        if self._needs_screen_context(text):
            quality = QualityMode.BEST  # force Complex tier so tools are available
        await self._stream_response(text_content=text, quality_mode=quality, interaction_style=interaction_style)

    # ── Shared streaming ──────────────────────────────────────────────────

    async def _stream_response(
        self,
        text_content: str,
        quality_mode: QualityMode = QualityMode.BALANCED,
        interaction_style: InteractionStyle = InteractionStyle.TEXT,
    ):
        self._busy.set()
        self.speech.cancel_listen()
        try:
            self.state.current_status = RuntimeStatus.THINKING
            await play_sound("process")

            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            full_response = ""
            first_token = False
            in_think = False
            think_had_content = False

            async def spin():
                i = 0
                while True:
                    print(f"\r  {DIM}Eyra {frames[i % len(frames)]}{NC}", end="", flush=True)
                    i += 1
                    await asyncio.sleep(0.08)

            spinner = asyncio.create_task(spin())

            try:
                async for chunk in process_task_stream(
                    text_content=text_content,
                    complexity_scorer=self.scorer,
                    settings=self.settings,
                    messages=list(self.state.conversation_messages),
                    quality_mode=quality_mode,
                    interaction_style=interaction_style,
                    tool_registry=self._tool_registry,
                ):
                    if not first_token:
                        first_token = True
                        spinner.cancel()
                        await play_sound("respond")
                        print(f"\r\033[2K\n  {CYAN}Eyra{NC} ", end="", flush=True)

                    for segment in _THINK_SPLIT.split(chunk):
                        if segment == THINK_START:
                            in_think = True
                            think_had_content = False
                            print(DIM_ITALIC, end="", flush=True)
                        elif segment == THINK_END:
                            in_think = False
                            print(NC, end="", flush=True)
                            if think_had_content:
                                print("\n\n", end="", flush=True)
                        elif segment:
                            print(segment, end="", flush=True)
                            if in_think:
                                think_had_content = True
                            else:
                                full_response += segment
            finally:
                if not spinner.done():
                    spinner.cancel()

            if not first_token:
                print("\r\033[2K", end="")

            print("\n")

            if full_response.strip():
                self.state.conversation_messages.append({"role": "assistant", "content": full_response})
                self.state.current_status = RuntimeStatus.SPEAKING
                await self.speech.speak(full_response.strip()[:200])

            self.state.current_status = RuntimeStatus.IDLE
        finally:
            self._busy.clear()
