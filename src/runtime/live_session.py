"""Unified live session orchestrator."""

import asyncio
import json
import logging
import re
import time
from datetime import datetime

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import InteractionStyle, QualityMode
from clients.ai_client import THINK_END, THINK_START
from runtime.models import LiveRuntimeState, PreflightResult, RuntimeStatus
from runtime.preflight import PreflightManager
from runtime.speech_controller import SpeechController
from runtime.status_presenter import (
    print_status_change,
    render_header,
    render_help_card,
    render_status_card,
    voice_status_label,
)
from runtime.tasks import BackgroundTask, BackgroundTaskManager, TaskStatus
from runtime.tooling import build_tool_registry
from tools.browser import BrowserSession
from tools.registry import ToolRegistry
from utils.settings import Settings
from utils.sound_player import play_sound
from utils.theme import CYAN, DIM, DIM_ITALIC, NC, YELLOW

logger = logging.getLogger(__name__)

_COMMANDS = {
    "/voice", "/mute", "/unmute",
    "/goal", "/status", "/quit", "/clear",
    "/mode", "/help", "/tasks", "/task", "/cancel",
}

_QUIT_WORDS = {"quit", "exit", "bye", "goodbye", "q"}

# Split streamed chunks on think-block sentinels so the renderer can style them.
_THINK_SPLIT = re.compile(f"({re.escape(THINK_START)}|{re.escape(THINK_END)})")

# Screen-intent detection: only match UI-specific nouns or
# action verbs with a visual direct object.
_UI_NOUNS = (
    r"screen|display|window|app|browser|tab|"
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


def _settings_int(settings: Settings, name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _settings_bool(settings: Settings, name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    return value if isinstance(value, bool) else default


def _settings_str(settings: Settings, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return value if isinstance(value, str) else default


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
        self._voice_task: asyncio.Task | None = None
        self._input_tasks: set[asyncio.Task] = set()
        self._render_lock = asyncio.Lock()
        self._model_semaphore = asyncio.Semaphore(max(1, _settings_int(settings, "MODEL_CONCURRENCY", 1)))
        self._pending_overwrite: dict[str, str] | None = None
        self.task_manager = BackgroundTaskManager(
            max_concurrent=_settings_int(settings, "MAX_BACKGROUND_TASKS", 2),
            task_timeout_seconds=_settings_int(settings, "TASK_TIMEOUT_SECONDS", 300),
            on_event=self._on_task_event,
        )

    def _build_tool_registry(self) -> ToolRegistry:
        return build_tool_registry(self.settings, browser_session=self._browser_session)

    async def run(self):
        """Main entry point. Runs until quit."""
        self.state.current_status = RuntimeStatus.IDLE
        render_header(self.state, self.settings)

        tasks = [
            asyncio.create_task(self._input_loop(), name="input"),
        ]
        if self.state.listening_enabled:
            self._start_voice_task()

        try:
            await self._shutdown.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for t in tasks:
                t.cancel()
            if self._voice_task is not None:
                self._voice_task.cancel()
                tasks.append(self._voice_task)
            await self.task_manager.shutdown()
            for t in list(self._input_tasks):
                t.cancel()
                tasks.append(t)
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.speech.interrupt()
            await self._browser_session.close()
            print("\n  Goodbye.\n")

    def _start_voice_task(self) -> None:
        """Start the owned voice loop if it is not already running."""
        if self._voice_task is None or self._voice_task.done():
            self._voice_task = asyncio.create_task(self._voice_input_loop(), name="voice")

    async def _stop_voice_task(self) -> None:
        """Stop the owned voice loop without blocking on itself."""
        self.speech.cancel_listen()
        task = self._voice_task
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._voice_task = None

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
                except KeyboardInterrupt:
                    # Ctrl-C during prompt: show a fresh prompt
                    print()
                    continue

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

                self._schedule_input(user_input, InteractionStyle.TEXT)

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

                # Wait while speaking before opening the mic. Background tasks do not block listening.
                if self.speech.is_speaking:
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
                print(f"\r\033[2K  {YELLOW}(voice){NC} {text}")
                self._schedule_input(text, InteractionStyle.VOICE)

        except asyncio.CancelledError:
            self.speech.cancel_listen()
            return

    def _schedule_input(self, text: str, interaction_style: InteractionStyle) -> None:
        task = asyncio.create_task(
            self._handle_user_input(text, interaction_style=interaction_style),
            name="eyra-input",
        )
        self._input_tasks.add(task)
        task.add_done_callback(self._input_tasks.discard)

    # ── Command handling ──────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> bool:
        stripped = cmd.strip()
        parts_original = stripped.split(maxsplit=1)
        lower_parts = stripped.lower().split(maxsplit=1)
        command = lower_parts[0]

        if command == "/quit":
            self._shutdown.set()
            return True

        if command == "/voice":
            arg = lower_parts[1] if len(lower_parts) > 1 else ""
            if arg == "on":
                if not await self._ensure_voice_available():
                    print_status_change("Voice not available")
                    return True
                self.state.listening_enabled = bool(self.preflight.listening_available)
                self.state.speech_enabled = bool(self.preflight.speech_available)
                if not (self.state.listening_enabled or self.state.speech_enabled):
                    print_status_change("Voice not available")
                    return True
                if self.state.listening_enabled:
                    self._start_voice_task()
                print_status_change(f"Voice on ({voice_status_label(self.state)})")
            elif arg == "off":
                self.state.listening_enabled = False
                self.state.speech_enabled = False
                await self.speech.interrupt()
                await self._stop_voice_task()
                print_status_change("Voice off")
            else:
                voice_status = voice_status_label(self.state)
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
            goal = parts_original[1] if len(parts_original) > 1 else None
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

        if command == "/tasks":
            self._print_tasks()
            return True

        if command == "/task":
            task_id = lower_parts[1] if len(lower_parts) > 1 else ""
            self._print_task_detail(task_id)
            return True

        if command == "/cancel":
            arg = lower_parts[1] if len(lower_parts) > 1 else ""
            if arg == "all":
                count = self.task_manager.cancel_all()
                print_status_change(f"Cancelled {count} task{'s' if count != 1 else ''}")
            elif arg:
                if self.task_manager.cancel_task(arg):
                    print_status_change(f"Cancelled task {arg}")
                else:
                    print(f"  No cancellable task found for: {arg}")
            else:
                print("  Usage: /cancel <id>|all")
            return True

        if command == "/clear":
            self.state.conversation_messages.clear()
            print_status_change("Session cleared")
            return True

        if command == "/mode":
            mode_str = lower_parts[1] if len(lower_parts) > 1 else ""
            try:
                requested = QualityMode(mode_str)
                if requested == QualityMode.FAST and not self.settings.COMPLEXITY_ROUTING_ENABLED:
                    print("  Fast mode needs complexity routing enabled. Staying in balanced mode.")
                    self.quality_mode = QualityMode.BALANCED
                    return True
                self.quality_mode = requested
                print_status_change(f"Quality: {self.quality_mode.value}")
            except ValueError:
                print("  Usage: /mode fast|balanced|best")
            return True

        if command == "/help":
            render_help_card()
            return True

        print(f"  Unknown command: {command}")
        return True

    async def _ensure_voice_available(self) -> bool:
        """Recover Local Whisper at runtime when startup skipped or failed voice checks."""
        if (
            self.preflight.listening_available is True
            and self.preflight.speech_available is True
            and self.state.wh_bin
        ):
            return True

        result = PreflightResult(
            backend_reachable=self.preflight.backend_reachable,
            models_ready=list(self.preflight.models_ready),
            models_missing=list(self.preflight.models_missing),
            screen_capture_available=self.preflight.screen_capture_available,
        )
        available = await PreflightManager(self.settings).check_local_whisper(
            result,
            listening_requested=True,
            speech_requested=True,
        )
        self.preflight.wh_available = available
        self.preflight.listening_available = (
            result.listening_available if result.listening_available is not None else available
        )
        self.preflight.speech_available = (
            result.speech_available if result.speech_available is not None else available
        )
        self.preflight.wh_bin = result.wh_bin
        self.state.wh_bin = result.wh_bin
        return available

    def _print_status(self):
        model_name = getattr(self.settings, "MODEL", "")
        render_status_card(
            state=self.state,
            quality_mode_value=self.quality_mode.value,
            tool_count=len(self._tool_registry.to_openai_tools()),
            msg_count=len(self.state.conversation_messages),
            model_name=model_name or "",
            task_summary=self._task_summary(),
        )

    def _task_summary(self) -> str:
        active = len(self.task_manager.active_tasks())
        recent = len(self.task_manager.list_tasks(include_recent=True))
        return f"{active} active, {recent} recent"

    def _print_tasks(self):
        rows = self.task_manager.list_tasks(include_recent=True)
        print()
        print("  Tasks")
        if not rows:
            print("  No tasks yet.")
            print()
            return
        for task in rows:
            print(f"  {task.id}  {task.status.value:<16} {task.title}")
            if task.progress_summary:
                print(f"      {task.progress_summary}")
        print()

    def _print_task_detail(self, task_id: str):
        if not task_id:
            print("  Usage: /task <id>")
            return
        task = self.task_manager.get_task(task_id)
        if task is None:
            print(f"  No task found for: {task_id}")
            return
        print()
        print(f"  Task {task.id}: {task.title}")
        print(f"  Status: {task.status.value}")
        print(f"  Request: {task.original_request}")
        print(f"  Progress: {task.progress_summary}")
        if task.needs_user_input:
            print("  Waiting for user input: yes")
        if task.required_filesystem or task.required_network or task.required_vision:
            reqs = []
            if task.required_filesystem:
                reqs.append("filesystem")
            if task.required_network:
                reqs.append("network")
            if task.required_vision:
                reqs.append("vision")
            print(f"  Required: {', '.join(reqs)}")
        if task.final_result:
            print(f"\n  Result:\n{self._indent(task.final_result)}")
        if task.error:
            print(f"\n  Error: {task.error}")
        print()

    # ── Screen context detection ──────────────────────────────────────────

    def _needs_screen_context(self, text: str) -> bool:
        return bool(_SCREEN_CUES.search(text))

    # ── User input handling ───────────────────────────────────────────────

    async def _handle_user_input(self, text: str, interaction_style: InteractionStyle = InteractionStyle.TEXT):
        self.state.conversation_messages.append({"role": "user", "content": text})
        if await self._handle_local_intent(text):
            return

        quality = self.quality_mode
        if self._needs_screen_context(text):
            if not self._tool_actions_available():
                print(
                    "  Screen analysis needs local tools to capture the screen. "
                    "The selected model cannot use local tools."
                )
                return
            if not self._vision_available():
                print("  Screen analysis needs a vision-capable model. The selected model cannot process images.")
                return
            quality = QualityMode.BEST  # force Complex tier so tools are available
        if _settings_bool(self.settings, "BACKGROUND_TASKS_ENABLED", True) and self._should_background_task(text):
            title = self._task_title(text)
            task = self.task_manager.create_task(
                title=title,
                original_request=text,
                worker=lambda task: self._run_worker_task(
                    task,
                    text_content=text,
                    quality_mode=quality,
                    interaction_style=interaction_style,
                ),
                related_context=list(self.state.conversation_messages[-6:]),
                used_tools=True,
                required_network=self._requires_network(text),
                required_filesystem=self._requires_filesystem(text),
                required_vision=self._needs_screen_context(text),
            )
            print_status_change(f"Task {task.id} accepted: {task.title}")
            return

        await self._stream_response(text_content=text, quality_mode=quality, interaction_style=interaction_style)

    async def _handle_local_intent(self, text: str) -> bool:
        lowered = text.strip().lower()
        lowered_clean = lowered.rstrip(".!?")
        if lowered_clean in {"overwrite it", "overwrite that", "overwrite that file", "replace it", "replace that file"}:
            if not self._tool_actions_available():
                self._print_model_without_tools()
                return True
            if self._pending_overwrite is None:
                print("  I do not have a pending file overwrite. Please name the file to replace.")
                return True
            result = await self._tool_registry.execute("write_file", json.dumps({**self._pending_overwrite, "overwrite": True}))
            self._pending_overwrite = None
            print(f"  {result.content}")
            return True

        if lowered in {"what are you doing?", "what are you doing", "what's going on?", "what's going on"}:
            self._print_tasks()
            return True
        if lowered in {"show tasks", "show my tasks", "list tasks", "what happened with that task?"}:
            self._print_tasks()
            return True
        if re.search(r"\bcancel (that|it|the task)\b", lowered):
            task = self.task_manager.latest_active_task()
            if task is None:
                print("  No running task to cancel.")
            elif self.task_manager.cancel_task(task.id):
                print_status_change(f"Cancelled task {task.id}")
            return True
        if lowered in {"cancel all", "cancel everything"}:
            count = self.task_manager.cancel_all()
            print_status_change(f"Cancelled {count} task{'s' if count != 1 else ''}")
            return True
        if re.search(r"\bwhat('?s| is) the time\b|\bwhat time is it\b|\bcurrent time\b", lowered):
            now = datetime.now().strftime("%A, %B %-d, %Y at %-I:%M %p")
            print(f"  {CYAN}Eyra{NC} {now}")
            return True
        if self._requires_network(text) and not _settings_bool(self.settings, "NETWORK_TOOLS_ENABLED", False):
            print(
                "  Network tools are disabled. Enable NETWORK_TOOLS_ENABLED=true before asking Eyra to browse, "
                "summarize websites, or check weather."
            )
            return True
        if await self._handle_direct_filesystem_intent(text):
            return True
        return False

    async def _handle_direct_filesystem_intent(self, text: str) -> bool:
        """Handle common safe file requests deterministically before asking a model.

        Local file operations should not depend on a small local model guessing the
        right tool call. These patterns cover the common voice-friendly forms while
        leaving ambiguous requests for the normal worker path.
        """
        stripped = " ".join(text.strip().split())
        lowered = stripped.lower()

        move_match = re.fullmatch(
            r"move\s+(?P<name>.+?)\s+from\s+(?:my\s+)?(?P<src>desktop|documents|downloads|tmp|/tmp)"
            r"\s+to\s+(?:my\s+)?(?P<dest>desktop|documents|downloads|tmp|/tmp)\.?",
            stripped,
            re.I,
        )
        if move_match:
            if not self._tool_actions_available():
                self._print_model_without_tools()
                return True
            name = move_match.group("name").strip().strip("'\"")
            source = self._path_in_named_folder(move_match.group("src"), name)
            destination = self._path_in_named_folder(move_match.group("dest"), name)
            result = await self._tool_registry.execute(
                "move_path",
                json.dumps({"source": source, "destination": destination, "overwrite": "overwrite" in lowered}),
            )
            print(f"  {result.content}")
            return True

        copy_match = re.fullmatch(
            r"copy\s+(?P<name>.+?)\s+from\s+(?:my\s+)?(?P<src>desktop|documents|downloads|tmp|/tmp)"
            r"\s+to\s+(?:my\s+)?(?P<dest>desktop|documents|downloads|tmp|/tmp)\.?",
            stripped,
            re.I,
        )
        if copy_match:
            if not self._tool_actions_available():
                self._print_model_without_tools()
                return True
            name = copy_match.group("name").strip().strip("'\"")
            source = self._path_in_named_folder(copy_match.group("src"), name)
            destination = self._path_in_named_folder(copy_match.group("dest"), name)
            result = await self._tool_registry.execute(
                "copy_path",
                json.dumps({"source": source, "destination": destination, "overwrite": "overwrite" in lowered}),
            )
            print(f"  {result.content}")
            return True

        write_match = re.fullmatch(
            r"(?:create|write|save)\s+(?:another\s+)?(?:a\s+)?(?:text\s+)?file\s+"
            r"(?:named|called)?\s*(?P<name>.+?)\s+in\s+(?:my\s+)?(?P<folder>desktop|documents|downloads|tmp|/tmp)"
            r"\s+with\s+(?:the\s+)?content:?\s*(?P<content>.*)",
            stripped,
            re.I,
        )
        if write_match:
            if not self._tool_actions_available():
                self._print_model_without_tools()
                return True
            path = self._path_in_named_folder(
                write_match.group("folder"),
                write_match.group("name").strip().strip("'\""),
            )
            content = write_match.group("content")
            overwrite = bool(
                re.search(
                    r"\b(overwrite|replace)\s+(?:it|that|the|existing|if)\b|\bwith\s+overwrite\b",
                    lowered,
                )
            )
            result = await self._tool_registry.execute(
                "write_file",
                json.dumps({"path": path, "content": content, "overwrite": overwrite}),
            )
            if result.content.startswith("File already exists:"):
                self._pending_overwrite = {"path": path, "content": content}
            print(f"  {result.content}")
            return True

        read_match = re.fullmatch(r"(?:read|open|show)\s+(?P<path>[/~][^\n]+)", stripped, re.I)
        if read_match:
            if not self._tool_actions_available():
                self._print_model_without_tools()
                return True
            path = read_match.group("path").strip().strip("'\"")
            result = await self._tool_registry.execute("read_file", json.dumps({"path": path}))
            print(f"  {result.content}")
            return True

        return False

    def _tool_actions_available(self) -> bool:
        model = _settings_str(self.settings, "WORKER_MODEL", "") or _settings_str(self.settings, "MODEL", "")
        checked = set(getattr(self.preflight, "tool_capability_checked_models", []))
        capable = set(getattr(self.preflight, "tool_capable_models", []))
        return model not in checked or model in capable

    def _vision_available(self) -> bool:
        model = _settings_str(self.settings, "WORKER_MODEL", "") or _settings_str(self.settings, "MODEL", "")
        checked = set(getattr(self.preflight, "vision_capability_checked_models", []))
        capable = set(getattr(self.preflight, "vision_capable_models", []))
        return model not in checked or model in capable

    @staticmethod
    def _print_model_without_tools() -> None:
        print(
            "  The selected model cannot use local tools. Text chat still works, but this task needs a tool-capable model."
        )

    @staticmethod
    def _path_in_named_folder(folder: str, name: str) -> str:
        folder_key = folder.strip().lower()
        if folder_key in {"tmp", "/tmp"}:
            return f"/tmp/{name}"
        return f"~/{folder_key.title()}/{name}"

    def _should_background_task(self, text: str) -> bool:
        lowered = text.lower()
        if self._needs_screen_context(text) or self._requires_filesystem(text) or self._requires_network(text):
            return True
        return bool(re.search(
            r"\b(summarize|read|open|move|copy|create|write|edit|organize|inspect|translate|pdf|file|folder|website)\b",
            lowered,
        ))

    def _requires_filesystem(self, text: str) -> bool:
        return bool(re.search(r"\b(file|folder|pdf|desktop|documents|downloads|clipboard|move|copy|write|create|open|read)\b", text, re.I))

    def _requires_network(self, text: str) -> bool:
        return bool(re.search(r"https?://|\b(website|web page|webpage|weather|browse|search the web)\b", text, re.I))

    def _task_title(self, text: str) -> str:
        title = " ".join(text.strip().split())
        if len(title) > 48:
            title = title[:45].rstrip() + "..."
        return title or "Task"

    async def _run_worker_task(
        self,
        task: BackgroundTask,
        text_content: str,
        quality_mode: QualityMode,
        interaction_style: InteractionStyle,
    ) -> str:
        task.mark_progress("Working")
        direct_pdf_result = await self._run_direct_pdf_task(task, text_content, quality_mode, interaction_style)
        if direct_pdf_result is not None:
            return direct_pdf_result

        async with self._model_semaphore:
            worker_settings = self.settings
            worker_model = _settings_str(self.settings, "WORKER_MODEL", "")
            if worker_model:
                worker_settings = Settings(**{**self.settings.__dict__, "MODEL": worker_model})
            result = ""
            async for chunk in process_task_stream(
                text_content=text_content,
                complexity_scorer=self.scorer,
                settings=worker_settings,
                messages=list(task.related_context) or [{"role": "user", "content": text_content}],
                quality_mode=quality_mode,
                interaction_style=interaction_style,
                tool_registry=self._tool_registry,
                current_goal=self.state.current_goal,
                require_tools=True,
            ):
                result += chunk
                if len(result) > 120 and task.progress_summary == "Working":
                    task.mark_progress("Preparing final answer")
            return result

    async def _run_direct_pdf_task(
        self,
        task: BackgroundTask,
        text_content: str,
        quality_mode: QualityMode,
        interaction_style: InteractionStyle,
    ) -> str | None:
        if not re.search(r"\bpdf\b", text_content, re.I):
            return None
        path_match = re.search(r"(?P<path>(?:~|/)[^\s'\"<>]+?\.pdf)\b", text_content, re.I)
        if path_match is None:
            return None

        pdf_path = path_match.group("path").rstrip(".,;:")
        task.mark_progress("Reading PDF locally")
        extracted = await self._tool_registry.execute("read_pdf", json.dumps({"path": pdf_path, "max_chars": 50000}))
        if "No extractable text found" in extracted.content or extracted.content.startswith(("Access denied:", "Not a file:", "Not a PDF file:", "Could not read PDF")):
            return extracted.content

        task.mark_progress("Summarizing extracted PDF text")
        worker_settings = self.settings
        worker_model = _settings_str(self.settings, "WORKER_MODEL", "")
        if worker_model:
            worker_settings = Settings(**{**self.settings.__dict__, "MODEL": worker_model})

        prompt = (
            "Summarize the PDF for the user's request. Be concise, factual, and do not ask for a follow-up. "
            "If the user asked for a focus area, answer that focus directly.\n\n"
            f"User request: {text_content}\n\n"
            f"Extracted local PDF text:\n{extracted.content[:50000]}"
        )
        result = ""
        async with self._model_semaphore:
            async for chunk in process_task_stream(
                text_content=prompt,
                complexity_scorer=self.scorer,
                settings=worker_settings,
                messages=[{"role": "user", "content": prompt}],
                quality_mode=quality_mode,
                interaction_style=interaction_style,
                tool_registry=None,
                current_goal=self.state.current_goal,
                require_tools=False,
            ):
                result += chunk
                if len(result) > 120 and task.progress_summary == "Summarizing extracted PDF text":
                    task.mark_progress("Preparing final answer")
        if result.strip():
            return result
        return self._fallback_pdf_summary(extracted.content)

    @staticmethod
    def _fallback_pdf_summary(extracted_text: str) -> str:
        lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
        page_lines = [line for line in lines if line.startswith("[Page ")]
        content_lines = [
            line
            for line in lines
            if not line.startswith(("PDF:", "Pages:", "Showing first", "[Page "))
        ]
        seen: set[str] = set()
        key_lines: list[str] = []
        for line in content_lines:
            normalized = line.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            key_lines.append(line)
            if len(key_lines) >= 8:
                break
        if not key_lines:
            return "The PDF text was extracted locally, but there was not enough readable text to summarize."
        pages = f"{len(page_lines)} page markers" if page_lines else "the extracted pages"
        bullets = "\n".join(f"- {line}" for line in key_lines)
        return f"Local PDF summary from {pages}:\n{bullets}"

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

            async with self._model_semaphore:
                async with self._render_lock:
                    spinner = asyncio.create_task(spin())

                    try:
                        async for chunk in process_task_stream(
                            text_content=text_content,
                            complexity_scorer=self.scorer,
                            settings=self.settings,
                            messages=self.state.conversation_messages,
                            quality_mode=quality_mode,
                            interaction_style=interaction_style,
                            tool_registry=self._tool_registry,
                            current_goal=self.state.current_goal,
                            require_tools=False,
                        ):
                            if not first_token:
                                first_token = True
                                spinner.cancel()
                                try:
                                    await spinner
                                except asyncio.CancelledError:
                                    pass
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
                    else:
                        print("\n")

            if full_response.strip():
                self.state.conversation_messages.append({"role": "assistant", "content": full_response})
                self.state.current_status = RuntimeStatus.SPEAKING
                await self.speech.speak(full_response.strip()[:200])
                await self.speech.wait_for_speech()

            self.state.current_status = RuntimeStatus.IDLE
        finally:
            self._busy.clear()

    def _on_task_event(self, task: BackgroundTask, event: str) -> None:
        if not _settings_bool(self.settings, "TASK_STATUS_UPDATES", True):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._render_task_event(task, event))

    async def _render_task_event(self, task: BackgroundTask, event: str) -> None:
        async with self._render_lock:
            if event in {"accepted", "started"}:
                return
            print()
            if task.status == TaskStatus.COMPLETED:
                print(f"  {CYAN}Task {task.id} completed:{NC} {task.title}")
                print(self._indent(task.final_result or "Done."))
                await self.speech.speak((task.final_result or "Task completed.")[:200])
            elif task.status == TaskStatus.FAILED:
                print(f"  Task {task.id} failed: {task.error or 'Unknown error'}")
            elif task.status == TaskStatus.CANCELLED:
                print(f"  Task {task.id} cancelled: {task.title}")
            elif task.status == TaskStatus.WAITING_FOR_USER:
                print(f"  Task {task.id} is waiting: {task.progress_summary}")
            print()

    @staticmethod
    def _indent(text: str) -> str:
        return "\n".join(f"    {line}" if line else "" for line in text.splitlines())
