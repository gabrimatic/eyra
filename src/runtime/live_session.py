"""Unified live session orchestrator."""

import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import InteractionStyle, QualityMode
from clients.ai_client import THINK_END, THINK_START
from runtime.capabilities import build_capability_snapshot, format_capability_answer
from runtime.coding_jobs import approval_id_from_text, parse_coding_job_request
from runtime.context import build_context_snapshot, format_context_answer
from runtime.dictation import DictationState, dictation_command, parse_dictation_target
from runtime.intents import (
    extract_pdf_path,
    needs_screen_context,
    requires_filesystem,
    requires_model_driven_tools,
    requires_network,
    should_background_task,
    task_title,
)
from runtime.jobs import DurableJobStore, JobStatus, RiskLevel
from runtime.models import LiveRuntimeState, PreflightResult, RuntimeStatus
from runtime.operator_loop import build_file_move_operator_loop
from runtime.planner import plan_task
from runtime.preflight import PreflightManager
from runtime.shared import RuntimeSharedState
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
from runtime.triggers import TriggerStatus, TriggerStore
from runtime.vision import analyze_screen
from runtime.voice_diagnostics import VoiceDiagnostics
from tools.approval import ApprovalManager
from tools.browser import BrowserSession
from tools.registry import ToolRegistry
from utils.settings import Settings
from utils.sound_player import play_sound
from utils.theme import CYAN, DIM, DIM_ITALIC, NC, YELLOW
from web.server import WebAssistantRuntime, start_web_server_in_thread

logger = logging.getLogger(__name__)

_COMMANDS = {
    "/voice", "/voice-diagnose", "/voice-test", "/mute", "/unmute",
    "/goal", "/status", "/quit", "/clear",
    "/mode", "/help", "/tasks", "/task", "/cancel", "/pause", "/resume",
    "/approvals", "/approve", "/reject", "/operations", "/capabilities", "/context", "/triggers", "/trigger",
}

_QUIT_WORDS = {"quit", "exit", "bye", "goodbye", "q"}
_NAMED_FOLDER_PATTERN = r"desktop|documents|downloads|pictures|movies|music|tmp|/tmp"

# Split streamed chunks on think-block sentinels so the renderer can style them.
_THINK_SPLIT = re.compile(f"({re.escape(THINK_START)}|{re.escape(THINK_END)})")

def _settings_int(settings: Settings, name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _settings_float(settings: Settings, name: str, default: float) -> float:
    value = getattr(settings, name, default)
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) else default


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
            input_device=_settings_str(settings, "VOICE_INPUT_DEVICE", "") or None,
            sample_rate=_settings_int(settings, "VOICE_SAMPLE_RATE", 16000),
        )
        self.quality_mode = QualityMode.BALANCED
        self._prompt = PromptSession()
        self._shutdown = asyncio.Event()
        # Coordination: set when the session is actively processing
        # (streaming a response, speaking, handling user input).
        # Voice loop yields when this is set.
        self._busy = asyncio.Event()
        self._browser_session = BrowserSession()
        self.approvals = ApprovalManager()
        self._trusted_overwrite_token = secrets.token_urlsafe(32)
        self._tool_registry = self._build_tool_registry()
        self._voice_task: asyncio.Task | None = None
        self._input_tasks: set[asyncio.Task] = set()
        self._render_lock = asyncio.Lock()
        self._model_semaphore = asyncio.Semaphore(max(1, _settings_int(settings, "MODEL_CONCURRENCY", 1)))
        self._pending_overwrite: dict[str, str] | None = None
        self._pending_correction: dict[str, str] | None = None
        self._pending_options: dict | None = None
        self._dictation = DictationState()
        self.job_store = DurableJobStore(_settings_str(settings, "JOB_STORE_PATH", "~/.local/share/eyra/jobs.sqlite3"))
        self.trigger_store = TriggerStore(
            _settings_str(settings, "TRIGGER_STORE_PATH", "~/.local/share/eyra/triggers.sqlite3")
        )
        self.task_manager = BackgroundTaskManager(
            max_concurrent=_settings_int(settings, "MAX_BACKGROUND_TASKS", 2),
            task_timeout_seconds=_settings_int(settings, "TASK_TIMEOUT_SECONDS", 300),
            on_event=self._on_task_event,
            job_store=self.job_store,
            source_frontend="terminal",
        )
        self._web_handle = None

    def _build_tool_registry(self) -> ToolRegistry:
        return build_tool_registry(
            self.settings,
            browser_session=self._browser_session,
            approval_manager=self.approvals,
            trusted_overwrite_token=self._trusted_overwrite_token,
        )

    async def run(self):
        """Main entry point. Runs until quit."""
        self.state.current_status = RuntimeStatus.IDLE
        render_header(self.state, self.settings)
        if _settings_bool(self.settings, "WEB_UI_ENABLED", False):
            self._start_embedded_web_ui()

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
            if self._web_handle is not None:
                self._web_handle.runtime.close()
                self._web_handle.close()
            await self._browser_session.close()
            self.trigger_store.close()
            self.job_store.close()
            print("\n  Goodbye.\n")

    def _start_embedded_web_ui(self) -> None:
        """Start a terminal-owned Web UI frontend sharing this session runtime."""
        if self._web_handle is not None:
            return
        shared = RuntimeSharedState.from_components(
            settings=self.settings,
            preflight=self.preflight,
            conversation=self.state.conversation_messages,
            scorer=self.scorer,
            browser_session=self._browser_session,
            approvals=self.approvals,
            registry=self._tool_registry,
            job_store=self.job_store,
            trigger_store=self.trigger_store,
            task_manager=self.task_manager,
            source_frontend="terminal",
        )
        runtime = WebAssistantRuntime(self.settings, preflight=self.preflight, shared=shared)
        try:
            self._web_handle = start_web_server_in_thread(self.settings, runtime=runtime)
        except OSError as e:
            runtime.close()
            print_status_change(f"Could not start Web UI on {self.settings.WEB_UI_HOST}:{self.settings.WEB_UI_PORT}: {e}")
            return
        host = self.settings.WEB_UI_HOST
        port = self.settings.WEB_UI_PORT
        print_status_change(f"Web UI ready on http://{host}:{port} using shared runtime")
        if self._web_handle.web_session_token and self.settings.WEB_UI_REQUIRE_TOKEN != "false":
            print_status_change(f"Web UI token URL: http://{host}:{port}/?token={self._web_handle.web_session_token}")

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

                self.state.current_status = RuntimeStatus.LISTENING
                try:
                    # This blocks until speech is detected and transcribed,
                    # or until cancelled. The mic remains active during TTS so
                    # a real user barge-in can interrupt speech output.
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

        if command == "/voice-diagnose":
            arg = lower_parts[1] if len(lower_parts) > 1 else ""
            report = await VoiceDiagnostics(
                settings=self.settings,
                wh_bin=self.state.wh_bin or self.preflight.wh_bin,
            ).run(include_physical_barge_in=arg in {"barge-in", "bargein", "physical"})
            print(report.render())
            return True

        if command == "/voice-test":
            if not self.state.speech_enabled:
                print("  Speech output is off. Run /voice on or /unmute after Local Whisper is available.")
                return True
            print("  Voice interruption test started. Speak over Eyra now; TTS should stop and your next input should process.")
            await self.speech.speak(
                "This is Eyra's voice interruption test. Start speaking now. "
                "If interruption is working, this spoken sentence should stop and your new input should be processed."
            )
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
            arg = lower_parts[1] if len(lower_parts) > 1 else ""
            if arg in {"clear-completed", "clear-done", "clear-terminal"}:
                memory_count = self.task_manager.clear_terminal_tasks()
                store_count = self.job_store.clear_terminal_jobs()
                count = max(memory_count, store_count)
                print_status_change(f"Cleared {count} completed job{'s' if count != 1 else ''}")
            else:
                self._print_tasks()
            return True

        if command == "/operations":
            self._print_recent_operations()
            return True

        if command == "/capabilities":
            self._print_capabilities()
            return True

        if command == "/context":
            self._print_context()
            return True

        if command == "/triggers":
            self._print_triggers()
            return True

        if command == "/trigger":
            arg = lower_parts[1] if len(lower_parts) > 1 else ""
            self._handle_trigger_command(arg)
            return True

        if command == "/task":
            arg = lower_parts[1] if len(lower_parts) > 1 else ""
            arg_parts = arg.split(maxsplit=1)
            if len(arg_parts) == 2 and arg_parts[0] == "logs":
                self._print_job_logs(arg_parts[1])
            elif len(arg_parts) == 2 and arg_parts[0] == "artifacts":
                self._print_job_artifacts(arg_parts[1])
            elif len(arg_parts) == 2 and arg_parts[0] == "retry":
                await self._retry_job(arg_parts[1])
            else:
                self._print_task_detail(arg)
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

        if command == "/pause":
            task_id = lower_parts[1] if len(lower_parts) > 1 else ""
            if task_id and self.task_manager.pause_task(task_id):
                print_status_change(f"Paused task {task_id}")
            elif task_id:
                print(f"  No queued task found for: {task_id}")
            else:
                print("  Usage: /pause <id>")
            return True

        if command == "/resume":
            task_id = lower_parts[1] if len(lower_parts) > 1 else ""
            if task_id and self.task_manager.resume_task(task_id):
                print_status_change(f"Resumed task {task_id}")
            elif task_id:
                print(f"  No paused task found for: {task_id}")
            else:
                print("  Usage: /resume <id>")
            return True

        if command == "/approvals":
            pending = self.approvals.list_pending()
            print()
            print("  Pending approvals")
            if not pending:
                print("  None.")
            for approval in pending:
                print(f"  {approval.id}  {approval.tool_name}  {approval.title}")
            print()
            return True

        if command == "/approve":
            approval_id = lower_parts[1] if len(lower_parts) > 1 else ""
            if approval_id and self.approvals.approve(approval_id):
                print_status_change(f"Approved {approval_id}")
            else:
                print(f"  No pending approval found for: {approval_id}")
            return True

        if command == "/reject":
            approval_id = lower_parts[1] if len(lower_parts) > 1 else ""
            if approval_id and self.approvals.reject(approval_id):
                print_status_change(f"Rejected {approval_id}")
            else:
                print(f"  No pending approval found for: {approval_id}")
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
        worker_model = _settings_str(self.settings, "WORKER_MODEL", "") or model_name
        vision_model = _settings_str(self.settings, "VISION_MODEL", "") or model_name
        render_status_card(
            state=self.state,
            quality_mode_value=self.quality_mode.value,
            tool_count=len(self._tool_registry.to_openai_tools()),
            msg_count=len(self.state.conversation_messages),
            model_name=model_name or "",
            task_summary=self._task_summary(),
            extra_rows=[
                ("Worker", worker_model or "default"),
                ("Vision", vision_model or "default"),
                ("Model tools", self._capability_label(worker_model, "tool")),
                ("Vision img", self._capability_label(vision_model, "vision")),
                ("Web UI", "eyra-web" if _settings_bool(self.settings, "WEB_UI_ENABLED", False) else "off"),
                ("Network", "on" if _settings_bool(self.settings, "NETWORK_TOOLS_ENABLED", False) else "off"),
                ("OS tools", "on" if _settings_bool(self.settings, "OS_TOOLS_ENABLED", False) else "off"),
                ("MCP", "on" if _settings_bool(self.settings, "MCP_TOOLS_ENABLED", False) else "off"),
                ("Agents", "on" if _settings_bool(self.settings, "AGENT_TOOLS_ENABLED", False) else "off"),
                ("Realtime", "on" if _settings_bool(self.settings, "REALTIME_VOICE_ENABLED", False) else "off"),
                ("Sandbox", _settings_str(self.settings, "FILESYSTEM_ALLOWED_PATHS", "")),
            ],
        )

    def _task_summary(self) -> str:
        active = len(self.task_manager.active_tasks())
        recent = len(self.task_manager.list_tasks(include_recent=True))
        return f"{active} active, {recent} recent"

    def _capability_label(self, model: str, capability: str) -> str:
        if not model:
            return "unknown"
        if capability == "tool":
            checked = set(getattr(self.preflight, "tool_capability_checked_models", []))
            capable = set(getattr(self.preflight, "tool_capable_models", []))
        else:
            checked = set(getattr(self.preflight, "vision_capability_checked_models", []))
            capable = set(getattr(self.preflight, "vision_capable_models", []))
        if model not in checked:
            return "unknown"
        return "yes" if model in capable else "no"

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

    def _print_coding_jobs(self) -> None:
        rows = [
            task
            for task in self.task_manager.list_tasks(include_recent=True)
            if task.normalized_task_spec.get("task_type") == "coding.agent_job"
            or task.title.lower().startswith("coding job:")
        ]
        print()
        print("  Coding jobs")
        if not rows:
            print("  No coding jobs yet.")
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
            job = self.job_store.get_job(task_id)
            if job is None:
                print(f"  No task found for: {task_id}")
                return
            print()
            print(f"  Job {job.id}: {job.title}")
            print(f"  Status: {job.status.value}")
            print(f"  Request: {job.original_user_input}")
            if job.current_step:
                print(f"  Step: {job.current_step}")
            if job.final_result:
                print(f"\n  Result:\n{self._indent(job.final_result)}")
            if job.error:
                print(f"\n  Error: {job.error}")
            print()
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

    def _print_job_logs(self, job_id: str) -> None:
        logs = self.job_store.list_logs(job_id, limit=20)
        print()
        print(f"  Job logs: {job_id}")
        if not logs:
            print("  No logs found.")
            print()
            return
        for entry in logs:
            print(f"  {entry.level:<7} {entry.message}")
        print()

    def _print_job_artifacts(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        print()
        print(f"  Job artifacts: {job_id}")
        if job is None:
            print("  No job found.")
            print()
            return
        if not job.artifacts:
            print("  No artifacts recorded.")
            print()
            return
        for artifact in job.artifacts:
            if isinstance(artifact, dict):
                summary = ", ".join(f"{key}={value}" for key, value in artifact.items())
            else:
                summary = str(artifact)
            print(f"  - {summary}")
        print()

    async def _retry_job(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        if job is None:
            print(f"  No job found for: {job_id}")
            return
        if job.status not in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.BLOCKED}:
            print(f"  Job {job_id} is {job.status.value}; retry is available for failed, cancelled, or blocked jobs.")
            return
        print_status_change(f"Retrying job {job_id}: {job.title}")
        await self._handle_user_input(job.original_user_input)

    # ── Screen context detection ──────────────────────────────────────────

    def _needs_screen_context(self, text: str) -> bool:
        return needs_screen_context(text)

    # ── User input handling ───────────────────────────────────────────────

    async def _handle_user_input(self, text: str, interaction_style: InteractionStyle = InteractionStyle.TEXT):
        if await self._handle_dictation_input(text):
            return
        self.state.conversation_messages.append({"role": "user", "content": text})
        if await self._handle_local_intent(text):
            return

        quality = self.quality_mode
        if self._needs_screen_context(text):
            if not self._vision_available():
                print(
                    "  Screen analysis needs a vision-capable model. Set VISION_MODEL to a model that can process "
                    "images, or use a main MODEL with vision support."
                )
                return
            quality = QualityMode.BEST
        if _settings_bool(self.settings, "BACKGROUND_TASKS_ENABLED", True) and self._should_background_task(text):
            if self._requires_model_driven_tools(text) and not self._tool_actions_available():
                self._print_model_without_tools()
                return
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

    async def _handle_dictation_input(self, text: str) -> bool:
        command = dictation_command(text)
        if command == "start":
            target = parse_dictation_target(text, self._path_in_named_folder)
            self._dictation.start(target_path=target)
            if target:
                print_status_change(f"Dictation started for {target}")
            else:
                print_status_change("Dictation started")
            return True
        if not self._dictation.active:
            return False
        if command == "cancel":
            self._dictation.clear()
            print_status_change("Dictation cancelled")
            return True
        if command == "end":
            content = self._dictation.text()
            target = self._dictation.target_path
            self._dictation.clear()
            if target:
                result = await self._tool_registry.execute("write_file", json.dumps({"path": target, "content": content}))
                success = result.content.startswith(("Created:", "Updated:"))
                self._record_direct_operation(
                    title="Save dictation",
                    user_request="End dictation.",
                    action_type="dictation.file.write",
                    capability="filesystem.write",
                    target=target,
                    before_state={"path": target, "existed": False},
                    after_state={"path": target, "content_length": len(content.encode())},
                    undo={"type": "file.remove_created_copy", "path": target} if success else {},
                    success=success,
                    error=None if success else result.content,
                )
                if success:
                    print(f"  Dictation saved: {target}")
                else:
                    print(f"  Could not save dictation: {result.content}")
                return True
            print("  Dictation ended")
            print(self._indent(content or "(empty)"))
            return True
        self._dictation.append(text)
        print_status_change("Captured dictation")
        return True

    async def _handle_local_intent(self, text: str) -> bool:
        lowered = text.strip().lower()
        lowered_clean = lowered.rstrip(".!?")
        if lowered_clean in {"stop", "stop speaking", "stop talking"}:
            await self.speech.interrupt()
            print_status_change("Stopped speech")
            return True
        if lowered_clean in {"show status", "status", "read status"}:
            self._print_status()
            return True
        if lowered_clean in {"overwrite it", "overwrite that", "overwrite that file", "replace it", "replace that file"}:
            if self._pending_overwrite is None:
                print("  I do not have a pending file overwrite. Please name the file to replace.")
                return True
            result = await self._tool_registry.execute(
                "write_file",
                json.dumps(
                    {
                        **self._pending_overwrite,
                        "overwrite": True,
                        "trusted_overwrite_token": self._trusted_overwrite_token,
                    }
                ),
            )
            self._pending_overwrite = None
            print(f"  {result.content}")
            return True

        correction_match = re.fullmatch(r"no,?\s+i\s+meant\s+(?P<name>.+?)\.?", text.strip(), re.I)
        if correction_match:
            if await self._handle_direct_correction(correction_match.group("name")):
                return True

        if lowered_clean in {"read the options", "read options", "repeat the options", "repeat options"}:
            self._print_pending_options()
            return True

        option_match = re.fullmatch(
            r"(?:choose|pick|select)\s+(?:number\s+)?(?P<option>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\.?",
            text.strip(),
            re.I,
        )
        if option_match:
            await self._handle_pending_option_choice(option_match.group("option"))
            return True

        if lowered in {"what are you doing?", "what are you doing", "what's going on?", "what's going on"}:
            self._print_tasks()
            return True
        if lowered in {"show tasks", "show my tasks", "list tasks", "what happened with that task?"}:
            self._print_tasks()
            return True
        if re.search(r"\b(what is|what's|show|list).{0,30}\bcoding (agent|job|jobs)\b", lowered):
            self._print_coding_jobs()
            return True
        if lowered in {"what changed?", "what changed", "what did you do?", "what did you do"}:
            self._print_recent_operations()
            return True
        if lowered in {"what is happening?", "what is happening", "what's happening?", "what's happening"}:
            self._print_context()
            return True
        if lowered_clean in {"undo that", "undo it", "undo last action", "undo the last action"}:
            await self._undo_last_reversible_operation()
            return True
        if re.search(
            r"\b(what can you control|what can you do|what permissions do you need|are you local|what would leave my machine)\b",
            lowered,
        ):
            self._print_capabilities()
            return True
        if lowered_clean in {"approve that", "approve it", "yes", "yeah", "yep"}:
            self._resolve_voice_approval(approve=True)
            return True
        if lowered_clean in {"reject that", "reject it", "deny that", "deny it", "no", "nope"}:
            self._resolve_voice_approval(approve=False)
            return True
        if re.search(r"\bcancel (that|it|the task)\b", lowered):
            task = self.task_manager.latest_active_task()
            if task is None:
                print("  No running task to cancel.")
            elif self.task_manager.cancel_task(task.id):
                print_status_change(f"Cancelled task {task.id}")
            return True
        if re.search(r"\bpause (that|it|the task)\b", lowered):
            task = self.task_manager.latest_active_task()
            if task is None:
                print("  No queued task to pause.")
            elif self.task_manager.pause_task(task.id):
                print_status_change(f"Paused task {task.id}")
            else:
                print(f"  Task {task.id} cannot be paused right now.")
            return True
        if re.search(r"\bresume (that|it|the task)\b", lowered):
            task = self.task_manager.latest_active_task()
            if task is None:
                print("  No paused task to resume.")
            elif self.task_manager.resume_task(task.id):
                print_status_change(f"Resumed task {task.id}")
            else:
                print(f"  Task {task.id} is not paused.")
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
        if await self._handle_direct_coding_job_intent(text):
            return True
        if await self._handle_direct_trigger_intent(text):
            return True
        if await self._handle_direct_filesystem_intent(text):
            return True
        return False

    async def _handle_direct_coding_job_intent(self, text: str) -> bool:
        request = parse_coding_job_request(text)
        if request is None:
            return False
        if not _settings_bool(self.settings, "AGENT_TOOLS_ENABLED", False):
            print("  Agent tools are disabled. Enable AGENT_TOOLS_ENABLED=true before starting coding jobs.")
            return True

        agent, instruction = request
        task = self.task_manager.create_task(
            title=f"Coding job: {instruction[:72]}",
            original_request=text,
            worker=lambda task: self._run_coding_job_task(task, agent=agent, instruction=instruction),
            used_tools=True,
            required_filesystem=True,
            normalized_task_spec={
                "task_type": "coding.agent_job",
                "agent": agent,
                "instruction": instruction,
                "approval_required": True,
            },
            risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        )
        print_status_change(f"Coding job {task.id} accepted with {agent}")
        return True

    async def _run_coding_job_task(self, task: BackgroundTask, *, agent: str, instruction: str) -> str:
        tool_name = "run_codex_task" if agent == "codex" else "run_openclaw_agent"
        cwd = _settings_str(self.settings, "FILESYSTEM_DEFAULT_PATH", "")
        task.mark_progress(f"Waiting for approval to run {agent}")
        pending = await self._tool_registry.execute(tool_name, json.dumps({"task": instruction, "cwd": cwd}))
        approval_id = approval_id_from_text(pending.content)
        if not approval_id:
            if pending.content.startswith(f"{agent} is not installed"):
                raise RuntimeError(pending.content)
            return pending.content

        while not task.cancellation_requested:
            approval = self.approvals.get(approval_id)
            if approval is None:
                raise RuntimeError("Coding job approval expired.")
            if approval.rejected:
                raise RuntimeError("Coding job rejected.")
            if approval.approved:
                task.mark_progress(f"Running coding job with {agent}")
                result = await self._tool_registry.execute(
                    tool_name,
                    json.dumps({"task": instruction, "cwd": cwd, "approval_id": approval_id}),
                )
                if "exit_code=0" in result.content:
                    return result.content
                raise RuntimeError(result.content)
            await asyncio.sleep(0.05)
        return "Coding job cancelled."

    async def _handle_direct_trigger_intent(self, text: str) -> bool:
        stripped = " ".join(text.strip().split())
        recurring_match = re.fullmatch(
            r"every\s+(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>seconds?|minutes?|hours?)\s+remind\s+me\s+to\s+(?P<message>.+?)\.?",
            stripped,
            re.I,
        )
        if recurring_match:
            amount = float(recurring_match.group("amount"))
            unit = recurring_match.group("unit").lower()
            multiplier = 3600 if unit.startswith("hour") else 60 if unit.startswith("minute") else 1
            interval_seconds = amount * multiplier
            message = recurring_match.group("message").strip().strip("'\"")
            next_fire_at = time.time() + interval_seconds
            trigger = self.trigger_store.create_recurring_timer_trigger(
                title=f"Recurring reminder: {message}",
                interval_seconds=interval_seconds,
                next_fire_at=next_fire_at,
                action={"type": "notify", "message": message},
                original_request=text,
            )
            task = self.task_manager.create_task(
                title=trigger.title,
                original_request=text,
                worker=lambda task: self._run_recurring_timer_trigger(task, trigger.id),
                used_tools=False,
                normalized_task_spec={
                    "task_type": "trigger.timer.recurring_reminder",
                    "trigger_id": trigger.id,
                    "message": message,
                    "interval_seconds": interval_seconds,
                    "next_fire_at": next_fire_at,
                },
                risk_level=RiskLevel.READ_ONLY,
            )
            print_status_change(f"Recurring reminder {trigger.id} created as task {task.id}")
            return True

        reminder_match = re.fullmatch(
            r"remind\s+me\s+in\s+(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>seconds?|minutes?|hours?)\s+to\s+(?P<message>.+?)\.?",
            stripped,
            re.I,
        )
        if reminder_match:
            amount = float(reminder_match.group("amount"))
            unit = reminder_match.group("unit").lower()
            multiplier = 3600 if unit.startswith("hour") else 60 if unit.startswith("minute") else 1
            message = reminder_match.group("message").strip().strip("'\"")
            fire_at = time.time() + amount * multiplier
            trigger = self.trigger_store.create_timer_trigger(
                title=f"Reminder: {message}",
                fire_at=fire_at,
                action={"type": "notify", "message": message},
                original_request=text,
            )
            task = self.task_manager.create_task(
                title=trigger.title,
                original_request=text,
                worker=lambda task: self._run_timer_trigger(task, trigger.id),
                used_tools=False,
                normalized_task_spec={
                    "task_type": "trigger.timer.reminder",
                    "trigger_id": trigger.id,
                    "message": message,
                    "fire_at": fire_at,
                },
                risk_level=RiskLevel.READ_ONLY,
            )
            print_status_change(f"Reminder {trigger.id} created as task {task.id}")
            return True

        trigger_match = re.fullmatch(
            rf"when\s+(?P<name>.+?)\s+appears\s+in\s+(?:my\s+)?(?P<src>{_NAMED_FOLDER_PATTERN}),?"
            rf"\s+move\s+(?:it|that|the file)\s+to\s+(?:my\s+)?(?P<dest>{_NAMED_FOLDER_PATTERN})\.?",
            stripped,
            re.I,
        )
        if not trigger_match:
            return False

        name = trigger_match.group("name").strip().strip("'\"")
        source = self._path_in_named_folder(trigger_match.group("src"), name)
        destination = self._path_in_named_folder(trigger_match.group("dest"), name)
        trigger = self.trigger_store.create_file_exists_trigger(
            title=f"Move {name} when it appears",
            source_path=source,
            action={"type": "file.move", "destination": destination},
            original_request=text,
        )
        task = self.task_manager.create_task(
            title=trigger.title,
            original_request=text,
            worker=lambda task: self._run_file_exists_trigger(task, trigger.id),
            required_filesystem=True,
            used_tools=True,
        )
        print_status_change(f"Trigger {trigger.id} created as task {task.id}")
        return True

    async def _run_timer_trigger(self, task: BackgroundTask, trigger_id: str) -> str:
        trigger = self.trigger_store.get_trigger(trigger_id)
        if trigger is None:
            return "Trigger was not found."
        fire_at = float(trigger.condition.get("fire_at", 0.0))
        message = str(trigger.action.get("message", "")).strip()
        if trigger.action.get("type") != "notify" or not message or fire_at <= 0:
            error = "Reminder trigger action is not supported."
            self.trigger_store.mark_failed(trigger_id, error)
            raise RuntimeError(error)

        interval_seconds = max(0.01, _settings_float(self.settings, "TRIGGER_CHECK_INTERVAL_SECONDS", 0.5))
        task.mark_progress(f"Waiting for reminder {trigger_id}")
        while time.time() < fire_at:
            if task.cancellation_requested:
                self.trigger_store.mark_cancelled(trigger_id)
                return "Reminder cancelled."
            current = self.trigger_store.get_trigger(trigger_id)
            if current is None:
                raise RuntimeError("Reminder trigger was deleted.")
            if current.status == TriggerStatus.CANCELLED:
                return "Reminder cancelled."
            if current.status == TriggerStatus.PAUSED:
                task.mark_progress(f"Paused reminder {trigger_id}")
                await asyncio.sleep(interval_seconds)
                continue
            await asyncio.sleep(min(interval_seconds, max(0.0, fire_at - time.time())))

        current = self.trigger_store.get_trigger(trigger_id)
        if current is not None and current.status == TriggerStatus.CANCELLED:
            return "Reminder cancelled."
        self.trigger_store.mark_completed(trigger_id)
        task.mark_progress(f"Reminder fired: {message}")
        return f"Reminder: {message}"

    async def _run_recurring_timer_trigger(self, task: BackgroundTask, trigger_id: str) -> str:
        trigger = self.trigger_store.get_trigger(trigger_id)
        if trigger is None:
            return "Trigger was not found."
        message = str(trigger.action.get("message", "")).strip()
        interval_seconds = float(trigger.condition.get("interval_seconds", 0.0))
        next_fire_at = float(trigger.condition.get("next_fire_at", 0.0))
        if trigger.action.get("type") != "notify" or not message or interval_seconds <= 0 or next_fire_at <= 0:
            error = "Recurring reminder trigger action is not supported."
            self.trigger_store.mark_failed(trigger_id, error)
            raise RuntimeError(error)

        check_interval = max(0.01, _settings_float(self.settings, "TRIGGER_CHECK_INTERVAL_SECONDS", 0.5))
        timeout_seconds = max(interval_seconds, _settings_float(self.settings, "TRIGGER_TIMEOUT_SECONDS", 300.0))
        deadline = time.monotonic() + timeout_seconds
        task.mark_progress(f"Waiting for recurring reminder {trigger_id}")
        while time.monotonic() < deadline:
            if task.cancellation_requested:
                self.trigger_store.mark_cancelled(trigger_id)
                return "Recurring reminder cancelled."
            current = self.trigger_store.get_trigger(trigger_id)
            if current is None:
                raise RuntimeError("Recurring reminder trigger was deleted.")
            if current.status == TriggerStatus.CANCELLED:
                return "Recurring reminder cancelled."
            if current.status == TriggerStatus.PAUSED:
                task.mark_progress(f"Paused recurring reminder {trigger_id}")
                await asyncio.sleep(check_interval)
                continue
            next_fire_at = float(current.condition.get("next_fire_at", next_fire_at))
            if time.time() >= next_fire_at:
                fired_at = time.time()
                next_fire_at = fired_at + interval_seconds
                self.trigger_store.record_recurring_fire(trigger_id, last_fire_at=fired_at, next_fire_at=next_fire_at)
                task.mark_progress(f"Recurring reminder fired: {message}")
            await asyncio.sleep(min(check_interval, max(0.0, next_fire_at - time.time())))

        self.trigger_store.mark_cancelled(trigger_id)
        return "Recurring reminder reached its local timeout and stopped."

    async def _run_file_exists_trigger(self, task: BackgroundTask, trigger_id: str) -> str:
        trigger = self.trigger_store.get_trigger(trigger_id)
        if trigger is None:
            return "Trigger was not found."
        source = str(trigger.condition.get("path", ""))
        destination = str(trigger.action.get("destination", ""))
        if trigger.action.get("type") != "file.move" or not source or not destination:
            message = "Trigger action is not supported."
            self.trigger_store.mark_failed(trigger_id, message)
            raise RuntimeError(message)

        timeout_seconds = max(1.0, _settings_float(self.settings, "TRIGGER_TIMEOUT_SECONDS", 300.0))
        interval_seconds = max(0.01, _settings_float(self.settings, "TRIGGER_CHECK_INTERVAL_SECONDS", 0.5))
        deadline = time.monotonic() + timeout_seconds
        task.mark_progress(f"Waiting for {source}")
        try:
            while time.monotonic() < deadline:
                if task.cancellation_requested:
                    self.trigger_store.mark_cancelled(trigger_id)
                    return "Trigger cancelled."
                current = self.trigger_store.get_trigger(trigger_id)
                if current is None:
                    raise RuntimeError("Trigger was deleted.")
                if current.status == TriggerStatus.CANCELLED:
                    return "Trigger cancelled."
                if current.status == TriggerStatus.PAUSED:
                    task.mark_progress(f"Paused trigger {trigger_id}")
                    await asyncio.sleep(interval_seconds)
                    continue
                if Path(source).expanduser().exists():
                    move_result = await self._tool_registry.execute(
                        "move_path",
                        json.dumps({"source": source, "destination": destination, "overwrite": False}),
                    )
                    success = move_result.content.startswith("Moved:")
                    self.job_store.record_operation(
                        job_id=task.id,
                        user_request=trigger.original_request,
                        normalized_action={"type": "trigger.file.move", "trigger_id": trigger.id},
                        capability="filesystem.trigger",
                        target=destination,
                        before_state={"source": source, "destination": destination},
                        after_state={"source": source, "destination": destination},
                        risk_level=RiskLevel.LOW_RISK_CHANGE,
                        success=success,
                        undo={"type": "file.move", "source": destination, "destination": source} if success else {},
                        error=None if success else move_result.content,
                    )
                    if success:
                        self.trigger_store.mark_completed(trigger_id)
                        return move_result.content
                    self.trigger_store.mark_failed(trigger_id, move_result.content)
                    raise RuntimeError(move_result.content)
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            self.trigger_store.mark_cancelled(trigger_id)
            raise

        message = f"Trigger timed out after {int(timeout_seconds)} seconds."
        self.trigger_store.mark_failed(trigger_id, message)
        raise RuntimeError(message)

    async def _handle_direct_filesystem_intent(self, text: str) -> bool:
        """Handle common safe file requests deterministically before asking a model.

        Local file operations should not depend on a small local model guessing the
        right tool call. These patterns cover the common voice-friendly forms while
        leaving ambiguous requests for the normal worker path.
        """
        stripped = " ".join(text.strip().split())
        lowered = stripped.lower()

        open_folder_match = re.fullmatch(
            rf"open\s+(?:my\s+)?(?P<folder>{_NAMED_FOLDER_PATTERN})\.?",
            stripped,
            re.I,
        )
        if open_folder_match:
            folder_path = self._path_in_named_folder(open_folder_match.group("folder"), "")
            result = await self._tool_registry.execute("open_path", json.dumps({"path": folder_path}))
            self._record_direct_operation(
                title=f"Open {open_folder_match.group('folder').title()}",
                user_request=text,
                action_type="file.open",
                capability="filesystem.open",
                target=folder_path.rstrip("/"),
                before_state={"path": folder_path.rstrip("/")},
                after_state={"path": folder_path.rstrip("/"), "opened": result.content.startswith("Opened:")},
                undo={},
                success=result.content.startswith("Opened:"),
                error=None if result.content.startswith("Opened:") else result.content,
            )
            print(f"  {result.content}")
            return True

        rename_match = re.fullmatch(
            rf"rename\s+(?P<name>.+?)\s+(?:in|from)\s+(?:my\s+)?(?P<folder>{_NAMED_FOLDER_PATTERN})"
            r"\s+to\s+(?P<new_name>.+?)\.?",
            stripped,
            re.I,
        )
        if rename_match:
            name = rename_match.group("name").strip().strip("'\"")
            new_name = rename_match.group("new_name").strip().strip("'\"")
            source = self._path_in_named_folder(rename_match.group("folder"), name)
            destination = self._path_in_named_folder(rename_match.group("folder"), new_name)
            result = await self._tool_registry.execute(
                "rename_path",
                json.dumps({"path": source, "new_name": new_name}),
            )
            actual_destination = result.content.split(" -> ", 1)[1] if " -> " in result.content else destination
            self._record_direct_operation(
                title=f"Rename {name}",
                user_request=text,
                action_type="file.rename",
                capability="filesystem.rename",
                target=actual_destination,
                before_state={"source": source, "destination": destination},
                after_state={"source": source, "destination": actual_destination},
                undo={"type": "file.move", "source": actual_destination, "destination": source},
                success=result.content.startswith("Renamed:"),
                error=None if result.content.startswith("Renamed:") else result.content,
            )
            if result.content.startswith("Renamed:"):
                self._pending_correction = None
            else:
                self._pending_correction = {"type": "rename", "folder": rename_match.group("folder"), "new_name": new_name}
            print(f"  {result.content}")
            return True

        duplicate_match = re.fullmatch(
            rf"duplicate\s+(?P<name>.+?)\s+(?:in|from)\s+(?:my\s+)?(?P<folder>{_NAMED_FOLDER_PATTERN})"
            r"(?:\s+as\s+(?P<new_name>.+?))?\.?",
            stripped,
            re.I,
        )
        if duplicate_match:
            name = duplicate_match.group("name").strip().strip("'\"")
            new_name = (duplicate_match.group("new_name") or "").strip().strip("'\"")
            source = self._path_in_named_folder(duplicate_match.group("folder"), name)
            requested_destination = self._path_in_named_folder(duplicate_match.group("folder"), new_name) if new_name else ""
            arguments = {"path": source}
            if requested_destination:
                arguments["destination"] = requested_destination
            result = await self._tool_registry.execute("duplicate_path", json.dumps(arguments))
            actual_destination = result.content.split(" -> ", 1)[1] if " -> " in result.content else requested_destination
            self._record_direct_operation(
                title=f"Duplicate {name}",
                user_request=text,
                action_type="file.duplicate",
                capability="filesystem.duplicate",
                target=actual_destination or source,
                before_state={"source": source, "destination": requested_destination},
                after_state={"source": source, "destination": actual_destination},
                undo={"type": "file.remove_created_copy", "path": actual_destination},
                success=result.content.startswith("Duplicated:"),
                error=None if result.content.startswith("Duplicated:") else result.content,
            )
            if result.content.startswith("Duplicated:"):
                self._pending_correction = None
            else:
                self._pending_correction = {
                    "type": "duplicate",
                    "folder": duplicate_match.group("folder"),
                    "new_name": new_name,
                }
            print(f"  {result.content}")
            return True

        latest_download_match = re.fullmatch(
            rf"move\s+(?:the\s+)?latest\s+downloaded\s+file\s+to\s+(?:my\s+)?(?P<dest>{_NAMED_FOLDER_PATTERN})\.?",
            stripped,
            re.I,
        )
        if latest_download_match:
            source_path = self._latest_file_in_named_folder("downloads")
            if source_path is None:
                print("  I could not find a downloaded file in Downloads.")
                return True
            source = str(source_path)
            destination = self._path_in_named_folder(latest_download_match.group("dest"), source_path.name)
            source_existed_before = source_path.exists()
            destination_existed_before = Path(destination).expanduser().exists()
            result = await self._tool_registry.execute(
                "move_path",
                json.dumps({"source": source, "destination": destination, "overwrite": False}),
            )
            self._record_direct_operation(
                title=f"Move latest download {source_path.name}",
                user_request=text,
                action_type="file.move",
                capability="filesystem.move",
                target=destination,
                before_state={
                    "source": source,
                    "source_exists": source_existed_before,
                    "destination": destination,
                    "destination_exists": destination_existed_before,
                    "reference": "latest downloaded file",
                },
                after_state={
                    "source": source,
                    "destination": destination,
                    "operator_loop": build_file_move_operator_loop(
                        source,
                        destination,
                        result.content,
                        source_existed_before=source_existed_before,
                        destination_existed_before=destination_existed_before,
                    ),
                },
                undo={"type": "file.move", "source": destination, "destination": source},
                success=result.content.startswith("Moved:"),
                error=None if result.content.startswith("Moved:") else result.content,
            )
            print(f"  {result.content}")
            return True

        move_match = re.fullmatch(
            rf"move\s+(?P<name>.+?)\s+from\s+(?:my\s+)?(?P<src>{_NAMED_FOLDER_PATTERN})"
            rf"\s+to\s+(?:my\s+)?(?P<dest>{_NAMED_FOLDER_PATTERN})\.?",
            stripped,
            re.I,
        )
        if move_match:
            name = move_match.group("name").strip().strip("'\"")
            source = self._path_in_named_folder(move_match.group("src"), name)
            destination = self._path_in_named_folder(move_match.group("dest"), name)
            source_existed_before = Path(source).expanduser().exists()
            destination_existed_before = Path(destination).expanduser().exists()
            result = await self._tool_registry.execute(
                "move_path",
                json.dumps(
                    {
                        "source": source,
                        "destination": destination,
                        "overwrite": "overwrite" in lowered,
                        "trusted_overwrite_token": self._trusted_overwrite_token if "overwrite" in lowered else "",
                    }
                ),
            )
            self._record_direct_operation(
                title=f"Move {name}",
                user_request=text,
                action_type="file.move",
                capability="filesystem.move",
                target=destination,
                before_state={
                    "source": source,
                    "source_exists": source_existed_before,
                    "destination": destination,
                    "destination_exists": destination_existed_before,
                },
                after_state={
                    "source": source,
                    "destination": destination,
                    "operator_loop": build_file_move_operator_loop(
                        source,
                        destination,
                        result.content,
                        source_existed_before=source_existed_before,
                        destination_existed_before=destination_existed_before,
                    ),
                },
                undo={"type": "file.move", "source": destination, "destination": source},
                success=result.content.startswith("Moved:"),
                error=None if result.content.startswith("Moved:") else result.content,
            )
            if result.content.startswith("Moved:"):
                self._pending_correction = None
            else:
                self._pending_correction = {"type": "move", "src": move_match.group("src"), "dest": move_match.group("dest")}
            print(f"  {result.content}")
            return True

        trash_match = re.fullmatch(
            rf"(?:remove|delete|trash)\s+(?P<name>.+?)\s+from\s+(?:my\s+)?(?P<folder>{_NAMED_FOLDER_PATTERN})\.?",
            stripped,
            re.I,
        )
        if trash_match:
            name = trash_match.group("name").strip().strip("'\"")
            folder = trash_match.group("folder")
            source = self._path_in_named_folder(folder, name)
            if not Path(source).expanduser().exists():
                candidates = self._find_named_folder_candidates(folder, name)
                if len(candidates) > 1:
                    self._set_pending_options(
                        title=f"Remove {name}",
                        action={"type": "trash", "folder": folder, "name": name, "user_request": text},
                        choices=[{"label": path.name, "path": str(path)} for path in candidates],
                    )
                    return True
                if len(candidates) == 1:
                    source = str(candidates[0])
                    name = candidates[0].name
            await self._run_direct_trash(source=source, name=name, user_request=text, folder=folder)
            return True

        copy_match = re.fullmatch(
            rf"copy\s+(?P<name>.+?)\s+from\s+(?:my\s+)?(?P<src>{_NAMED_FOLDER_PATTERN})"
            rf"\s+to\s+(?:my\s+)?(?P<dest>{_NAMED_FOLDER_PATTERN})\.?",
            stripped,
            re.I,
        )
        if copy_match:
            name = copy_match.group("name").strip().strip("'\"")
            source = self._path_in_named_folder(copy_match.group("src"), name)
            destination = self._path_in_named_folder(copy_match.group("dest"), name)
            result = await self._tool_registry.execute(
                "copy_path",
                json.dumps(
                    {
                        "source": source,
                        "destination": destination,
                        "overwrite": "overwrite" in lowered,
                        "trusted_overwrite_token": self._trusted_overwrite_token if "overwrite" in lowered else "",
                    }
                ),
            )
            self._record_direct_operation(
                title=f"Copy {name}",
                user_request=text,
                action_type="file.copy",
                capability="filesystem.copy",
                target=destination,
                before_state={"source": source, "destination": destination},
                after_state={"source": source, "destination": destination},
                undo={"type": "file.remove_created_copy", "path": destination},
                success=result.content.startswith("Copied:"),
                error=None if result.content.startswith("Copied:") else result.content,
            )
            if result.content.startswith("Copied:"):
                self._pending_correction = None
            else:
                self._pending_correction = {"type": "copy", "src": copy_match.group("src"), "dest": copy_match.group("dest")}
            print(f"  {result.content}")
            return True

        write_match = re.fullmatch(
            r"(?:create|write|save)\s+(?:another\s+)?(?:a\s+)?(?:text\s+)?file\s+"
            rf"(?:named|called)?\s*(?P<name>.+?)\s+in\s+(?:my\s+)?(?P<folder>{_NAMED_FOLDER_PATTERN})"
            r"\s+with\s+(?:the\s+)?content:?\s*(?P<content>.*)",
            stripped,
            re.I,
        )
        if write_match:
            write_name = write_match.group("name").strip().strip("'\"")
            path = self._path_in_named_folder(
                write_match.group("folder"),
                write_name,
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
                json.dumps(
                    {
                        "path": path,
                        "content": content,
                        "overwrite": overwrite,
                        "trusted_overwrite_token": self._trusted_overwrite_token if overwrite else "",
                    }
                ),
            )
            if result.content.startswith("File already exists:"):
                self._pending_overwrite = {"path": path, "content": content}
            self._record_direct_operation(
                title=f"Write {write_name}",
                user_request=text,
                action_type="file.write",
                capability="filesystem.write",
                target=path,
                before_state={"path": path, "existed": overwrite},
                after_state={"path": path, "content_length": len(content.encode())},
                undo={"type": "file.restore_previous_contents", "path": path},
                success=result.content.startswith(("Created:", "Updated:")),
                error=None if result.content.startswith(("Created:", "Updated:")) else result.content,
            )
            print(f"  {result.content}")
            return True

        read_match = re.fullmatch(r"(?:read|open|show)\s+(?P<path>[/~][^\n]+)", stripped, re.I)
        if read_match:
            path = read_match.group("path").strip().strip("'\"")
            result = await self._tool_registry.execute("read_file", json.dumps({"path": path}))
            print(f"  {result.content}")
            return True

        return False

    def _find_named_folder_candidates(self, folder: str, query: str) -> list[Path]:
        folder_path = Path(self._path_in_named_folder(folder, "")).expanduser()
        try:
            children = list(folder_path.iterdir())
        except OSError:
            return []
        needle = query.lower().strip()
        return sorted(
            (child for child in children if needle and needle in child.name.lower()),
            key=lambda child: child.name.lower(),
        )[:10]

    def _latest_file_in_named_folder(self, folder: str) -> Path | None:
        folder_path = Path(self._path_in_named_folder(folder, "")).expanduser()
        try:
            files = [child for child in folder_path.iterdir() if child.is_file()]
        except OSError:
            return None
        if not files:
            return None
        return max(files, key=lambda child: (child.stat().st_mtime, child.name.lower()))

    def _set_pending_options(self, *, title: str, action: dict, choices: list[dict[str, str]]) -> None:
        self._pending_options = {"title": title, "action": action, "choices": choices}
        self._print_pending_options(prefix="I found multiple matches.")

    def _print_pending_options(self, prefix: str | None = None) -> None:
        if not self._pending_options:
            print("  There are no options to choose from.")
            return
        if prefix:
            print(f"  {prefix}")
        for index, choice in enumerate(self._pending_options["choices"], start=1):
            print(f"  {index}. {choice['label']}")

    async def _handle_pending_option_choice(self, option: str) -> None:
        if not self._pending_options:
            print("  There are no options to choose from.")
            return
        numbers = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        index = numbers.get(option.lower(), int(option) if option.isdigit() else 0)
        choices = self._pending_options["choices"]
        if index < 1 or index > len(choices):
            print(f"  Choose a number from 1 to {len(choices)}.")
            return
        pending = self._pending_options
        self._pending_options = None
        choice = choices[index - 1]
        action = pending["action"]
        if action.get("type") == "trash":
            await self._run_direct_trash(
                source=choice["path"],
                name=choice["label"],
                user_request=action.get("user_request", pending["title"]),
                folder=action.get("folder", ""),
            )
            return
        print("  I cannot apply that option automatically.")

    async def _run_direct_trash(self, *, source: str, name: str, user_request: str, folder: str) -> None:
        result = await self._tool_registry.execute("move_to_trash", json.dumps({"path": source}))
        trash_path = result.content.split(" -> ", 1)[1] if " -> " in result.content else ""
        self._record_direct_operation(
            title=f"Trash {name}",
            user_request=user_request,
            action_type="file.trash",
            capability="filesystem.trash",
            target=source,
            before_state={"path": source, "existed": True},
            after_state={"path": source, "trash_path": trash_path},
            undo={"type": "file.restore_from_trash", "trash_path": trash_path, "destination": source},
            success=result.content.startswith("Moved to Trash:"),
            error=None if result.content.startswith("Moved to Trash:") else result.content,
        )
        if result.content.startswith("Moved to Trash:"):
            self._pending_correction = None
        else:
            self._pending_correction = {"type": "trash", "folder": folder}
        print(f"  {result.content}")

    async def _handle_direct_correction(self, name: str) -> bool:
        if self._pending_correction is None:
            print("  I do not have a failed local file action to correct.")
            return True
        corrected_name = name.strip().strip("'\"").removeprefix("the file ").strip()
        correction = self._pending_correction
        self._pending_correction = None
        if correction["type"] == "move":
            corrected = f"Move {corrected_name} from my {correction['src']} to {correction['dest']}."
        elif correction["type"] == "copy":
            corrected = f"Copy {corrected_name} from my {correction['src']} to {correction['dest']}."
        elif correction["type"] == "trash":
            corrected = f"Remove {corrected_name} from my {correction['folder']}."
        elif correction["type"] == "rename":
            corrected = f"Rename {corrected_name} in my {correction['folder']} to {correction['new_name']}."
        elif correction["type"] == "duplicate":
            corrected = f"Duplicate {corrected_name} from my {correction['folder']}"
            if correction.get("new_name"):
                corrected += f" as {correction['new_name']}"
            corrected += "."
        else:
            print("  I cannot correct that action automatically.")
            return True
        print_status_change(f"Corrected target: {corrected_name}")
        return await self._handle_direct_filesystem_intent(corrected)

    def _record_direct_operation(
        self,
        *,
        title: str,
        user_request: str,
        action_type: str,
        capability: str,
        target: str,
        before_state: dict,
        after_state: dict,
        undo: dict,
        success: bool,
        error: str | None = None,
    ) -> None:
        planned = plan_task(user_request)
        normalized_task_spec = (
            planned.to_dict()
            if planned.task_type != "unknown"
            else {
                "task_type": action_type,
                "target_refs": [target],
                "success_criteria": ["Action result reported success", "Operation recorded in local ledger"],
            }
        )
        job = self.job_store.create_job(
            title=title,
            original_user_input=user_request,
            source_frontend="terminal",
            normalized_task_spec=normalized_task_spec,
            risk_level=RiskLevel.LOW_RISK_CHANGE,
            required_capabilities=[capability],
            current_plan=["Resolve direct request", "Run local filesystem action", "Record operation"],
        )
        self.job_store.record_operation(
            job_id=job.id,
            user_request=user_request,
            normalized_action={"type": action_type},
            capability=capability,
            target=target,
            before_state=before_state,
            after_state=after_state,
            risk_level=RiskLevel.LOW_RISK_CHANGE,
            success=success,
            undo=undo,
            error=error,
        )
        self.job_store.update_job(
            job.id,
            status=JobStatus.COMPLETED if success else JobStatus.FAILED,
            final_result="Done." if success else None,
            error=error,
        )

    def _print_recent_operations(self) -> None:
        operations = self.job_store.list_operations(limit=8)
        print()
        print("  Recent changes")
        if not operations:
            print("  No recorded changes yet.")
            print()
            return
        for entry in operations:
            status = "ok" if entry.success else "failed"
            print(f"  {entry.id}  {status:<6} {entry.normalized_action.get('type', 'operation')}  {entry.target}")
            if entry.undo:
                print(f"      undo: {entry.undo.get('type', 'available')}")
        print()

    def _print_capabilities(self) -> None:
        snapshot = build_capability_snapshot(self.settings, preflight=self.preflight, state=self.state)
        print()
        print(self._indent(format_capability_answer(snapshot)))
        print()

    def _print_context(self) -> None:
        snapshot = build_context_snapshot(self.settings, state=self.state, job_store=self.job_store)
        print()
        print(self._indent(format_context_answer(snapshot)))
        print()

    def _print_triggers(self) -> None:
        triggers = self.trigger_store.list_triggers(limit=20)
        print()
        print("  Triggers")
        if not triggers:
            print("  No triggers yet.")
            print()
            return
        for trigger in triggers:
            condition = trigger.condition.get("path", "")
            print(f"  {trigger.id}  {trigger.status.value:<10} {trigger.title}")
            if condition:
                print(f"      when: {condition}")
            if trigger.last_error:
                print(f"      error: {trigger.last_error}")
        print()

    def _handle_trigger_command(self, arg: str) -> None:
        parts = arg.split()
        if len(parts) != 2 or parts[0] not in {"pause", "resume", "cancel"}:
            print("  Usage: /trigger pause|resume|cancel <id>")
            return
        action, trigger_id = parts
        if action == "pause":
            updated = self.trigger_store.mark_paused(trigger_id)
            if updated is None:
                print(f"  No trigger found for: {trigger_id}")
            else:
                print_status_change(f"Paused trigger {trigger_id}")
            return
        if action == "resume":
            updated = self.trigger_store.mark_active(trigger_id)
            if updated is None:
                print(f"  No trigger found for: {trigger_id}")
            else:
                print_status_change(f"Resumed trigger {trigger_id}")
            return
        updated = self.trigger_store.mark_cancelled(trigger_id)
        if updated is None:
            print(f"  No trigger found for: {trigger_id}")
        else:
            print_status_change(f"Cancelled trigger {trigger_id}")

    def _resolve_voice_approval(self, *, approve: bool) -> None:
        pending = self.approvals.list_pending()
        if not pending:
            print("  No pending approvals.")
            return
        if len(pending) > 1:
            print("  Multiple pending approvals. Say the id or use /approve <id> or /reject <id>.")
            for approval in pending:
                print(f"  {approval.id}  {approval.tool_name}  {approval.title}")
            return
        approval = pending[0]
        ok = self.approvals.approve(approval.id) if approve else self.approvals.reject(approval.id)
        if ok:
            print_status_change(f"{'Approved' if approve else 'Rejected'} {approval.id}")
        else:
            print(f"  Could not {'approve' if approve else 'reject'} {approval.id}.")

    async def _undo_last_reversible_operation(self) -> None:
        operations = [entry for entry in self.job_store.list_operations(limit=20) if entry.success and entry.undo]
        if not operations:
            print("  No reversible operation to undo.")
            return
        operation = operations[0]
        undo = operation.undo
        undo_type = undo.get("type")
        if undo_type == "file.move":
            result = await self._tool_registry.execute(
                "move_path",
                json.dumps(
                    {
                        "source": undo.get("source", ""),
                        "destination": undo.get("destination", ""),
                        "overwrite": False,
                    }
                ),
            )
        elif undo_type == "file.restore_from_trash":
            result = await self._tool_registry.execute(
                "restore_from_trash",
                json.dumps(
                    {
                        "trash_path": undo.get("trash_path", ""),
                        "destination": undo.get("destination", ""),
                    }
                ),
            )
        elif undo_type == "file.remove_created_copy":
            result = await self._tool_registry.execute("move_to_trash", json.dumps({"path": undo.get("path", "")}))
        else:
            print(f"  I cannot undo {operation.normalized_action.get('type', 'that operation')} automatically.")
            return
        if result.content.startswith(("Moved:", "Restored:", "Moved to Trash:")):
            print(f"  Undid {operation.normalized_action.get('type', 'operation')}: {result.content}")
            self._record_direct_operation(
                title=f"Undo {operation.normalized_action.get('type', 'operation')}",
                user_request="Undo that.",
                action_type=f"undo.{operation.normalized_action.get('type', 'operation')}",
                capability="filesystem.undo",
                target=operation.target,
                before_state={"operation_id": operation.id},
                after_state={"result": result.content},
                undo={},
                success=True,
            )
        else:
            print(f"  Could not undo {operation.normalized_action.get('type', 'operation')}: {result.content}")

    def _tool_actions_available(self) -> bool:
        model = _settings_str(self.settings, "WORKER_MODEL", "") or _settings_str(self.settings, "MODEL", "")
        checked = set(getattr(self.preflight, "tool_capability_checked_models", []))
        capable = set(getattr(self.preflight, "tool_capable_models", []))
        return model not in checked or model in capable

    def _vision_available(self) -> bool:
        model = _settings_str(self.settings, "VISION_MODEL", "") or _settings_str(self.settings, "MODEL", "")
        checked = set(getattr(self.preflight, "vision_capability_checked_models", []))
        capable = set(getattr(self.preflight, "vision_capable_models", []))
        return model not in checked or model in capable

    @staticmethod
    def _print_model_without_tools() -> None:
        print(
            "  This open-ended local tool task requires a model with native tool calling. Text chat and recognized "
            "controller-owned actions still work with the selected model."
        )

    @staticmethod
    def _path_in_named_folder(folder: str, name: str) -> str:
        folder_key = folder.strip().lower()
        if folder_key in {"tmp", "/tmp"}:
            return f"/tmp/{name}"
        return f"~/{folder_key.title()}/{name}"

    def _should_background_task(self, text: str) -> bool:
        return should_background_task(text)

    def _requires_filesystem(self, text: str) -> bool:
        return requires_filesystem(text)

    def _requires_network(self, text: str) -> bool:
        return requires_network(text)

    def _requires_model_driven_tools(self, text: str) -> bool:
        return requires_model_driven_tools(text)

    def _task_title(self, text: str) -> str:
        return task_title(text)

    async def _run_worker_task(
        self,
        task: BackgroundTask,
        text_content: str,
        quality_mode: QualityMode,
        interaction_style: InteractionStyle,
    ) -> str:
        task.mark_progress("Working")
        direct_screen_result = await self._run_direct_screen_task(task, text_content)
        if direct_screen_result is not None:
            return direct_screen_result
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

    async def _run_direct_screen_task(self, task: BackgroundTask, text_content: str) -> str | None:
        if not self._needs_screen_context(text_content):
            return None
        task.mark_progress("Capturing screenshot locally")
        result = await analyze_screen(
            settings=self.settings,
            prompt=text_content,
            conversation_messages=list(task.related_context) or list(self.state.conversation_messages[-6:]),
            current_goal=self.state.current_goal,
            model_semaphore=self._model_semaphore,
            preflight=self.preflight,
        )
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
        pdf_path = extract_pdf_path(text_content)
        if pdf_path is None:
            return None

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
