"""Small built-in Web UI for phone and browser access."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import queue
import re
import secrets
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import InteractionStyle, QualityMode
from runtime.capabilities import build_capability_snapshot, format_capability_answer
from runtime.coding_jobs import approval_id_from_text, parse_coding_job_request
from runtime.connectors.registry import ConnectorRegistry
from runtime.connectors.types import ConnectorJobSpec
from runtime.context import build_context_snapshot
from runtime.dictation import DictationState, dictation_command, parse_dictation_target
from runtime.history import ProtocolHistory, SemanticHistory, semantic_history_to_protocol_context
from runtime.intents import (
    extract_pdf_path,
    needs_screen_context,
    requires_filesystem,
    requires_network,
    should_background_task,
    task_title,
)
from runtime.jobs import DurableJobStore, RiskLevel
from runtime.models import PreflightResult
from runtime.preflight import PreflightManager
from runtime.routing.model_registry import worker_model_settings
from runtime.routing.router import RuntimeRouter
from runtime.routing.trace import trace_to_dict
from runtime.routing.types import RequestEnvelope, RequestSource, RoutingDecision
from runtime.shared import RuntimeSharedState
from runtime.tasks import BackgroundTask, BackgroundTaskManager, TaskStatus
from runtime.tooling import build_tool_registry
from runtime.triggers import TriggerStatus, TriggerStore
from runtime.vision import analyze_screen, vision_model_name
from tools.approval import ApprovalManager
from tools.browser import BrowserSession
from tools.system_info import format_system_info_for_query
from utils.settings import Settings

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SAFE_REALTIME_TOOLS = {"get_current_time", "discover_capabilities"}
_WEB_TOKEN_QUERY_RE = re.compile(r"(?i)(token=)[^&\s]+")
_WEB_SECRET_RE = re.compile(r"(?i)(api[_-]?key|secret|password|token)([=:]\s*)([^\s,}]+)")
_WEB_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_WEB_HOME_RE = re.compile(r"/Users/[^/\s]+")
_WEB_TEMP_RE = re.compile(r"(?:/private)?/var/folders/[^\s,}\"']+|/tmp/[^\s,}\"']+")


class EyraThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP server that avoids reverse-DNS lookup on 0.0.0.0 binds."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


@dataclass
class WebServerHandle:
    """Owned background Web UI server."""

    server: EyraThreadingHTTPServer
    thread: threading.Thread
    runtime: "WebAssistantRuntime"
    web_session_token: str
    realtime_tool_token: str

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def build_health_payload(
    settings: Settings,
    runtime_scope: str = "standalone",
    preflight: PreflightResult | None = None,
) -> dict[str, Any]:
    capabilities = build_capability_snapshot(settings, preflight=preflight)
    return {
        "status": "ok",
        "offlineByDefault": True,
        "runtime": {
            "scope": runtime_scope,
            "sharedState": runtime_scope == "shared",
        },
        "capabilities": {
            "localFirst": capabilities["localFirst"],
            "models": {
                "providerLocal": capabilities["models"]["providerLocal"],
                "backendReady": capabilities["models"]["backendReady"],
                "mainToolCalling": capabilities["models"]["mainToolCalling"],
                "visionImages": capabilities["models"]["visionImages"],
            },
            "privacy": {
                "leavesMachineByDefault": capabilities["privacy"]["leavesMachineByDefault"],
                "remotePaths": list(capabilities["privacy"]["remotePaths"]),
            },
        },
        "web": {
            "enabled": settings.WEB_UI_ENABLED,
            "host": settings.WEB_UI_HOST,
            "port": settings.WEB_UI_PORT,
            "authRequired": web_auth_required(settings),
        },
        "model": {
            "main": settings.MODEL,
            "worker": settings.WORKER_MODEL or settings.MODEL,
            "vision": vision_model_name(settings),
        },
        "voice": {
            "localWhisper": settings.LIVE_LISTENING_ENABLED or settings.LIVE_SPEECH_ENABLED,
            "realtime": settings.REALTIME_VOICE_ENABLED,
            "realtimeModel": settings.REALTIME_MODEL,
            "realtimeTools": settings.REALTIME_TOOLS_ENABLED,
        },
        "tools": {
            "network": settings.NETWORK_TOOLS_ENABLED,
            "os": settings.OS_TOOLS_ENABLED,
            "agents": settings.AGENT_TOOLS_ENABLED or settings.EXTERNAL_AGENT_TOOLS_ENABLED,
            "mcp": settings.MCP_TOOLS_ENABLED,
            "connectors": settings.CONNECTORS_ENABLED,
        },
    }


def build_capabilities_payload(
    settings: Settings,
    runtime_scope: str = "standalone",
    preflight: PreflightResult | None = None,
) -> dict[str, Any]:
    """Authenticated redacted capability payload for Web UI clients."""
    return {
        "runtime": {"scope": runtime_scope, "sharedState": runtime_scope == "shared"},
        "capabilities": _redact_api_value(build_capability_snapshot(settings, preflight=preflight)),
    }


def web_auth_required(settings: Settings) -> bool:
    mode = settings.WEB_UI_REQUIRE_TOKEN.strip().lower()
    if mode == "true":
        return True
    if mode == "false":
        if settings.WEB_UI_HOST not in _LOCAL_HOSTS:
            return True
        return False
    return True


def validate_request_size(settings: Settings, length: int) -> bool:
    return 0 <= length <= max(1, int(settings.WEB_UI_MAX_REQUEST_BYTES))


def _redact_api_value(value):
    if isinstance(value, dict):
        return {key: _redact_api_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_api_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_api_value(item) for item in value]
    if isinstance(value, str):
        redacted = _WEB_TOKEN_QUERY_RE.sub(r"\1[REDACTED]", value)
        redacted = _WEB_SECRET_RE.sub(r"\1\2[REDACTED]", redacted)
        redacted = _WEB_OPENAI_KEY_RE.sub("[REDACTED_KEY]", redacted)
        redacted = _WEB_HOME_RE.sub("~/[user]", redacted)
        redacted = _WEB_TEMP_RE.sub("~/[temp]", redacted)
        return redacted
    return value


def _task_payload(task: BackgroundTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "request": _redact_api_value(task.original_request),
        "status": task.status.value,
        "progress": _redact_api_value(task.progress_summary),
        "result": _redact_api_value(task.final_result),
        "error": _redact_api_value(task.error),
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "needsUserInput": task.needs_user_input,
        "requiredNetwork": task.required_network,
        "requiredFilesystem": task.required_filesystem,
        "requiredVision": task.required_vision,
    }


def _trigger_payload(trigger) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "title": trigger.title,
        "kind": trigger.kind,
        "status": trigger.status.value,
        "condition": trigger.condition,
        "action": trigger.action,
        "createdAt": trigger.created_at,
        "updatedAt": trigger.updated_at,
        "completedAt": trigger.completed_at,
        "error": trigger.last_error,
    }


class WebAssistantRuntime:
    """Assistant runtime for the built-in Web UI."""

    def __init__(
        self,
        settings: Settings,
        preflight: PreflightResult | None = None,
        shared: RuntimeSharedState | None = None,
    ):
        self.settings = settings
        self.runtime_scope = "shared" if shared is not None else "standalone"
        self.shared = shared
        self._owns_components = shared is None
        self.input_lock = asyncio.Lock()
        self.dictation = DictationState()
        if shared is not None:
            self.scorer = shared.scorer
            self.protocol_history = shared.protocol_history
            self.semantic_history = shared.semantic_history
            self.conversation = self.protocol_history.messages
            self.browser_session = shared.browser_session
            self.approvals = shared.approvals
            self.registry = shared.registry
            self.job_store = shared.job_store
            self.trigger_store = shared.trigger_store
            self.task_manager = shared.task_manager
            self.connector_registry = shared.connector_registry
        else:
            self.scorer = ComplexityScorer()
            self.protocol_history = ProtocolHistory()
            self.semantic_history = SemanticHistory()
            self.conversation = self.protocol_history.messages
            self.browser_session = BrowserSession()
            self.approvals = ApprovalManager()
            self.registry = build_tool_registry(
                settings,
                browser_session=self.browser_session,
                approval_manager=self.approvals,
            )
            self.job_store = DurableJobStore(settings.JOB_STORE_PATH)
            self.trigger_store = TriggerStore(settings.TRIGGER_STORE_PATH)
            self.task_manager = BackgroundTaskManager(
                max_concurrent=max(1, int(settings.MAX_BACKGROUND_TASKS)),
                task_timeout_seconds=max(1, int(settings.TASK_TIMEOUT_SECONDS)),
                on_event=self._on_task_event,
                job_store=self.job_store,
                source_frontend="web",
            )
            self.connector_registry = ConnectorRegistry.from_settings(
                settings,
                approvals=self.approvals,
                job_store=self.job_store,
            )
        self.router = RuntimeRouter(self.scorer)
        self.last_route_trace = shared.last_route_trace if shared is not None else None
        self.model_semaphore = asyncio.Semaphore(max(1, int(settings.MODEL_CONCURRENCY)))
        self._task_event_subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._task_event_lock = threading.Lock()
        if shared is not None:
            self.task_manager.add_event_listener(self._on_task_event)
        self.preflight = preflight or PreflightResult(
            backend_reachable=True,
            models_ready=settings.all_model_names,
            screen_capture_available=bool(shutil.which("screencapture")),
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="eyra-web-runtime", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_sync(self, coro, timeout: float = 30.0):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def handle_message(self, text: str, voice_mode: str = "text") -> dict[str, Any]:
        async with self.input_lock:
            return await self._handle_message_locked(text, voice_mode)

    async def _handle_message_locked(self, text: str, voice_mode: str = "text") -> dict[str, Any]:
        text = " ".join(text.strip().split())
        if not text:
            return {"reply": "Message is empty."}
        if requires_network(text) and not self.settings.NETWORK_TOOLS_ENABLED:
            interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
            await self._route_decision(text, voice_mode=voice_mode, interaction=interaction, is_worker=False)
            return {
                "reply": (
                    "Network tools are disabled. Enable NETWORK_TOOLS_ENABLED=true before asking Eyra to browse, "
                    "summarize websites, or check weather."
                )
            }
        if needs_screen_context(text) and not self._vision_available():
            interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
            await self._route_decision(text, voice_mode=voice_mode, interaction=interaction, is_worker=False)
            return {
                "reply": (
                    "Screen analysis needs a vision-capable model. Set VISION_MODEL to a model that can process "
                    "images, or use a main MODEL with vision support."
                )
            }
        if re.search(
            r"\b(what can you control|what can you do|what permissions do you need|are you local|what would leave my machine)\b",
            text,
            re.I,
        ):
            snapshot = build_capability_snapshot(self.settings, preflight=self.preflight)
            return {"reply": format_capability_answer(snapshot)}
        if re.search(
            r"\b(system information|system info|macos version|mac os version|disk space|storage|memory|ram|uptime|battery)\b",
            text,
            re.I,
        ):
            result = await self.registry.execute("get_system_info", "{}")
            return {"reply": format_system_info_for_query(result.content, text)}
        connector_result = await self._handle_connector_input(text)
        if connector_result is not None:
            return connector_result
        dictation_result = await self._handle_dictation_input(text)
        if dictation_result is not None:
            return dictation_result
        user_message = {"role": "user", "content": text}
        self._append_protocol_message(user_message)
        conversation_snapshot = list(self.conversation)
        coding_result = await self._handle_direct_coding_job_intent(text)
        if coding_result is not None:
            return coding_result
        trigger_result = await self._handle_direct_trigger_intent(text)
        if trigger_result is not None:
            return trigger_result
        if self.settings.BACKGROUND_TASKS_ENABLED and should_background_task(text):
            interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
            route_preview = await self._route_decision(
                text,
                voice_mode=voice_mode,
                interaction=interaction,
                is_worker=True,
            )
            if route_preview.selected_model is None:
                return {"reply": route_preview.fallback_plan.on_model_missing}
            if route_preview.require_tools and not route_preview.tool_policy.allowed_tool_names:
                return {"reply": route_preview.fallback_plan.on_capability_missing}
            task = self.task_manager.create_task(
                title=task_title(text),
                original_request=text,
                worker=lambda task: self._run_worker_task(task, text, voice_mode, routing_decision=route_preview),
                related_context=self.semantic_history.recent(6),
                used_tools=True,
                required_network=requires_network(text),
                required_filesystem=requires_filesystem(text),
                required_vision=needs_screen_context(text),
            )
            return {"reply": f"Task {task.id} accepted: {task.title}", "taskId": task.id}
        reply = await self._chat(text, voice_mode, messages=conversation_snapshot, user_message=user_message)
        return {"reply": reply}

    async def _handle_dictation_input(self, text: str) -> dict[str, Any] | None:
        command = dictation_command(text)
        if command == "start":
            target = parse_dictation_target(text, self._path_in_named_folder)
            self.dictation.start(target_path=target)
            reply = f"Dictation started for {target}" if target else "Dictation started."
            return {"reply": reply}
        if not self.dictation.active:
            return None
        if command == "cancel":
            self.dictation.clear()
            return {"reply": "Dictation cancelled."}
        if command == "end":
            content = self.dictation.text()
            target = self.dictation.target_path
            self.dictation.clear()
            if target:
                result = await self.registry.execute("write_file", json.dumps({"path": target, "content": content}))
                if result.content.startswith(("Created:", "Updated:")):
                    return {"reply": f"Dictation saved: {target}"}
                return {"reply": f"Could not save dictation: {result.content}"}
            return {"reply": f"Dictation ended.\n{content or '(empty)'}"}
        self.dictation.append(text)
        return {"reply": "Captured dictation."}

    async def _handle_connector_input(self, text: str) -> dict[str, Any] | None:
        lowered = text.lower()
        if re.search(r"\b(what connectors do i have|list connectors|show connectors)\b", lowered):
            return {"reply": json.dumps(self.connector_registry.capability_snapshot(), indent=2, sort_keys=True)}
        connector_id = self._extract_connector_id(text)
        if connector_id is None:
            return None
        if re.search(r"\b(can you use|connector status|show connector|what is)\b", lowered):
            return {"reply": json.dumps(self.connector_registry.snapshot_for(connector_id), indent=2, sort_keys=True)}
        if re.search(r"\bcancel\b", lowered):
            cancelled = self.connector_registry.cancel(connector_id)
            return {"reply": f"Cancelled connector job for {connector_id}." if cancelled else f"No running connector job found for {connector_id}."}
        task_match = re.search(
            r"\b(?:connect this task to|ask|tell|use)\s+[a-z][a-z0-9_-]{1,63}\s+(?:to\s+)?(?P<task>.+)$",
            text,
            re.I,
        )
        task_text = task_match.group("task").strip() if task_match else text
        return await self.run_connector(connector_id, task_text)

    def _extract_connector_id(self, text: str) -> str | None:
        configured = {item["id"].lower() for item in self.connector_registry.list_connectors()}
        for connector_id in configured:
            if re.search(rf"\b{re.escape(connector_id)}\b", text, re.I):
                return connector_id
        if "connector" not in text.lower():
            return None
        match = re.search(r"\b(?:connector|use|ask|tell|cancel|connect(?: this task)? to)\s+([a-z][a-z0-9_-]{1,63})\b", text, re.I)
        return match.group(1).lower() if match else None

    def _vision_available(self) -> bool:
        model = self.settings.VISION_MODEL or self.settings.MODEL
        checked = set(self.preflight.vision_capability_checked_models)
        capable = set(self.preflight.vision_capable_models)
        return model not in checked or model in capable

    async def _chat(
        self,
        text: str,
        voice_mode: str = "text",
        messages: list[dict] | None = None,
        user_message: dict | None = None,
    ) -> str:
        interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
        routing_decision = await self._route_decision(
            text,
            voice_mode=voice_mode,
            interaction=interaction,
            is_worker=False,
        )
        chunks: list[str] = []
        async with self.model_semaphore:
            async for chunk in process_task_stream(
                text_content=text,
                complexity_scorer=self.scorer,
                settings=self.settings,
                messages=list(messages) if messages is not None else list(self.conversation),
                quality_mode=QualityMode.BALANCED,
                interaction_style=interaction,
                tool_registry=self.registry,
                routing_decision=routing_decision,
            ):
                chunks.append(chunk)
        reply = "".join(chunks).strip() or "No response."
        assistant_message = {"role": "assistant", "content": reply}
        insert_at = self._conversation_insert_index_after(user_message)
        if insert_at is None:
            self._append_protocol_message(assistant_message)
        else:
            self._insert_protocol_message(insert_at, assistant_message)
        return reply

    def _append_protocol_message(self, message: dict[str, Any]) -> None:
        self.protocol_history.append(message)
        self.semantic_history.append_from_protocol(message)

    def _insert_protocol_message(self, index: int, message: dict[str, Any]) -> None:
        self.protocol_history.insert(index, message)
        self.semantic_history.rebuild_from_protocol(self.protocol_history.messages)

    def _conversation_insert_index_after(self, message: dict | None) -> int | None:
        if message is None:
            return None
        for index, item in enumerate(self.conversation):
            if item is message:
                return index + 1
        return None

    async def _run_worker_task(
        self,
        task: BackgroundTask,
        text: str,
        voice_mode: str,
        routing_decision: RoutingDecision | None = None,
    ) -> str:
        task.mark_progress("Working")
        interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
        if routing_decision is None:
            routing_decision = await self._route_decision(
                text,
                voice_mode=voice_mode,
                interaction=interaction,
                is_worker=True,
            )
        if needs_screen_context(text):
            task.mark_progress("Capturing screenshot locally")
            return await analyze_screen(
                settings=self.settings,
                prompt=text,
                conversation_messages=semantic_history_to_protocol_context(task.related_context),
                current_goal=None,
                model_semaphore=self.model_semaphore,
                preflight=self.preflight,
            )
        pdf_result = await self._run_direct_pdf_task(task, text, voice_mode)
        if pdf_result is not None:
            return pdf_result
        chunks: list[str] = []
        worker_settings = worker_model_settings(self.settings)
        async with self.model_semaphore:
            async for chunk in process_task_stream(
                text_content=text,
                complexity_scorer=self.scorer,
                settings=worker_settings,
                messages=semantic_history_to_protocol_context(task.related_context) or [{"role": "user", "content": text}],
                quality_mode=QualityMode.BALANCED,
                interaction_style=interaction,
                tool_registry=self.registry,
                require_tools=True,
                routing_decision=routing_decision,
            ):
                chunks.append(chunk)
                if len("".join(chunks)) > 120 and task.progress_summary == "Working":
                    task.mark_progress("Preparing final answer")
        return "".join(chunks).strip() or "Task finished."

    def _source_for_voice_mode(self, voice_mode: str) -> RequestSource:
        if voice_mode == "local":
            return RequestSource.LOCAL_VOICE
        if voice_mode == "realtime":
            return RequestSource.REALTIME_VOICE
        return RequestSource.WEB

    async def _route_decision(
        self,
        text: str,
        *,
        voice_mode: str,
        interaction: InteractionStyle,
        is_worker: bool,
    ) -> RoutingDecision:
        envelope = RequestEnvelope(
            text=text,
            source=self._source_for_voice_mode(voice_mode),
            interaction_style=interaction,
            quality_mode=QualityMode.BALANCED,
            messages=list(self.conversation),
            current_goal=None,
            is_worker=is_worker,
            settings=self.settings,
            preflight=self.preflight,
        )
        decision = await self.router.route(
            envelope,
            tool_registry=self.registry,
            connector_registry=self.connector_registry,
        )
        self.last_route_trace = decision.trace
        if self.shared is not None:
            self.shared.last_route_trace = decision.trace
        return decision

    async def _handle_direct_coding_job_intent(self, text: str) -> dict[str, Any] | None:
        request = parse_coding_job_request(text)
        if request is None:
            return None
        if not (self.settings.AGENT_TOOLS_ENABLED or self.settings.EXTERNAL_AGENT_TOOLS_ENABLED):
            return {
                "reply": (
                    "Agent tools are disabled. Enable AGENT_TOOLS_ENABLED=true or "
                    "EXTERNAL_AGENT_TOOLS_ENABLED=true before starting coding jobs."
                )
            }

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
        return {"reply": f"Coding job {task.id} accepted with {agent}", "taskId": task.id}

    async def _run_coding_job_task(self, task: BackgroundTask, *, agent: str, instruction: str) -> str:
        tool_name = "run_codex_task" if agent == "codex" else "run_openclaw_agent"
        task.mark_progress(f"Waiting for approval to run {agent}")
        pending = await self.registry.execute(
            tool_name,
            json.dumps({"task": instruction, "cwd": self.settings.FILESYSTEM_DEFAULT_PATH}),
        )
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
                result = await self.registry.execute(
                    tool_name,
                    json.dumps(
                        {
                            "task": instruction,
                            "cwd": self.settings.FILESYSTEM_DEFAULT_PATH,
                            "approval_id": approval_id,
                        }
                    ),
                )
                if "exit_code=0" in result.content:
                    return result.content
                raise RuntimeError(result.content)
            await asyncio.sleep(0.05)
        return "Coding job cancelled."

    async def _handle_direct_trigger_intent(self, text: str) -> dict[str, Any] | None:
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
            return {
                "reply": f"Recurring reminder {trigger.id} created as task {task.id}",
                "taskId": task.id,
                "triggerId": trigger.id,
            }

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
            return {"reply": f"Reminder {trigger.id} created as task {task.id}", "taskId": task.id, "triggerId": trigger.id}

        trigger_match = re.fullmatch(
            r"when\s+(?P<name>.+?)\s+appears\s+in\s+(?:my\s+)?(?P<src>desktop|documents|downloads|tmp|/tmp),?"
            r"\s+move\s+(?:it|that|the file)\s+to\s+(?:my\s+)?(?P<dest>desktop|documents|downloads|tmp|/tmp)\.?",
            stripped,
            re.I,
        )
        if not trigger_match:
            return None

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
        return {"reply": f"Trigger {trigger.id} created as task {task.id}", "taskId": task.id, "triggerId": trigger.id}

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

        interval_seconds = max(0.01, float(self.settings.TRIGGER_CHECK_INTERVAL_SECONDS))
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

        check_interval = max(0.01, float(self.settings.TRIGGER_CHECK_INTERVAL_SECONDS))
        timeout_seconds = max(interval_seconds, float(self.settings.TRIGGER_TIMEOUT_SECONDS))
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

        timeout_seconds = max(1.0, float(self.settings.TRIGGER_TIMEOUT_SECONDS))
        interval_seconds = max(0.01, float(self.settings.TRIGGER_CHECK_INTERVAL_SECONDS))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        task.mark_progress(f"Waiting for {source}")
        try:
            while asyncio.get_running_loop().time() < deadline:
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
                    moved = await self.registry.execute(
                        "move_path",
                        json.dumps({"source": source, "destination": destination, "overwrite": False}),
                    )
                    success = moved.content.startswith("Moved:")
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
                        error=None if success else moved.content,
                    )
                    if success:
                        self.trigger_store.mark_completed(trigger_id)
                        return moved.content
                    self.trigger_store.mark_failed(trigger_id, moved.content)
                    raise RuntimeError(moved.content)
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            self.trigger_store.mark_cancelled(trigger_id)
            raise

        message = f"Trigger timed out after {int(timeout_seconds)} seconds."
        self.trigger_store.mark_failed(trigger_id, message)
        raise RuntimeError(message)

    @staticmethod
    def _path_in_named_folder(folder: str, name: str) -> str:
        folder_key = folder.strip().lower()
        if folder_key in {"tmp", "/tmp"}:
            return f"/tmp/{name}"
        return f"~/{folder_key.title()}/{name}"

    async def _run_direct_pdf_task(self, task: BackgroundTask, text: str, voice_mode: str) -> str | None:
        if "pdf" not in text.lower():
            return None
        pdf_path = extract_pdf_path(text)
        if pdf_path is None:
            return None

        task.mark_progress("Reading PDF locally")
        extracted = await self.registry.execute("read_pdf", json.dumps({"path": pdf_path, "max_chars": 50000}))
        if "No extractable text found" in extracted.content or extracted.content.startswith(
            ("Access denied:", "Not a file:", "Not a PDF file:", "Could not read PDF")
        ):
            return extracted.content

        task.mark_progress("Summarizing extracted PDF text")
        interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
        worker_settings = worker_model_settings(self.settings)
        prompt = (
            "Summarize the PDF for the user's request. Be concise, factual, and do not ask for a follow-up. "
            "If the user asked for a focus area, answer that focus directly.\n\n"
            f"User request: {text}\n\n"
            f"Extracted local PDF text:\n{extracted.content[:50000]}"
        )
        chunks: list[str] = []
        async with self.model_semaphore:
            async for chunk in process_task_stream(
                text_content=prompt,
                complexity_scorer=self.scorer,
                settings=worker_settings,
                messages=[{"role": "user", "content": prompt}],
                quality_mode=QualityMode.BALANCED,
                interaction_style=interaction,
                tool_registry=None,
                require_tools=False,
            ):
                chunks.append(chunk)
                if len("".join(chunks)) > 120 and task.progress_summary == "Summarizing extracted PDF text":
                    task.mark_progress("Preparing final answer")
        return "".join(chunks).strip() or "The PDF text was extracted locally, but there was not enough readable text to summarize."

    async def route_last(self) -> dict[str, Any]:
        trace = self.last_route_trace
        if trace is None and self.shared is not None:
            trace = self.shared.last_route_trace
        return {"route": trace_to_dict(trace) if trace is not None else None}

    async def support_diagnostics(self) -> dict[str, Any]:
        from runtime.cli import _doctor

        result = await _doctor(self.settings)
        return {"ok": result.ok, "message": result.message, **result.data}

    async def capabilities(self) -> dict[str, Any]:
        return build_capabilities_payload(
            self.settings,
            runtime_scope=self.runtime_scope,
            preflight=self.preflight,
        )

    async def context_snapshot(self) -> dict[str, Any]:
        state = self._context_state()
        return {"context": _redact_api_value(build_context_snapshot(self.settings, state=state, job_store=self.job_store))}

    async def connectors(self) -> dict[str, Any]:
        return self.connector_registry.capability_snapshot()

    async def connector_detail(self, connector_id: str) -> dict[str, Any]:
        return {"connector": self.connector_registry.snapshot_for(connector_id)}

    async def test_connector(self, connector_id: str, approval_id: str = "") -> dict[str, Any]:
        result = await self.connector_registry.test(connector_id, approval_id=approval_id)
        return {"connectorId": result.connector_id, "state": result.state.value, "reason": result.reason, "checks": list(result.checks)}

    async def run_connector(self, connector_id: str, task_text: str) -> dict[str, Any]:
        route_preview = await self._route_decision(
            f"ask connector {connector_id}",
            voice_mode="text",
            interaction=InteractionStyle.TEXT,
            is_worker=True,
        )
        job = self.job_store.create_job(
            title=f"Connector job: {connector_id}",
            original_user_input=f"connector:{connector_id}",
            source_frontend="web",
            normalized_task_spec={"task_type": "connector.job", "connector_id": connector_id},
            risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
            required_capabilities=[cap.value for cap in route_preview.required_capabilities],
        )

        async def worker(task: BackgroundTask) -> str:
            result = await self.connector_registry.run(
                ConnectorJobSpec(
                    connector_id=connector_id,
                    task=task_text,
                    cwd=self.settings.FILESYSTEM_DEFAULT_PATH,
                    source="web",
                    job_id=job.id,
                )
            )
            if result.status == "approval_required":
                task.mark_progress(f"Waiting for connector approval {result.approval_id}")
                while not task.cancellation_requested:
                    approval = self.approvals.get(result.approval_id)
                    if approval is None:
                        raise RuntimeError("Connector approval expired.")
                    if approval.rejected:
                        raise RuntimeError("Connector job rejected.")
                    if approval.approved:
                        rerun = await self.connector_registry.run(
                            ConnectorJobSpec(
                                connector_id=connector_id,
                                task=task_text,
                                cwd=self.settings.FILESYSTEM_DEFAULT_PATH,
                                source="web",
                                job_id=job.id,
                                approval_id=result.approval_id,
                            )
                        )
                        if rerun.status == "completed":
                            return rerun.output
                        if rerun.status == "cancelled":
                            raise asyncio.CancelledError
                        raise RuntimeError(rerun.output)
                    await asyncio.sleep(0.05)
                self.connector_registry.cancel(job.id)
                return "Connector job cancelled."
            if result.status == "completed":
                return result.output
            if result.status == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError(result.output)

        task = self.task_manager.create_task(
            title=f"Connector job: {connector_id}",
            original_request=f"connector:{connector_id}",
            worker=worker,
            used_tools=True,
            required_filesystem=True,
            normalized_task_spec={"task_type": "connector.job", "connector_id": connector_id, "job_id": job.id},
            risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        )
        return {"reply": f"Connector job {task.id} accepted with {connector_id}", "taskId": task.id, "jobId": job.id}

    async def cancel_connector(self, connector_id: str) -> dict[str, Any]:
        return {"cancelled": self.connector_registry.cancel(connector_id)}

    async def list_tasks(self) -> dict[str, Any]:
        return {"tasks": [_task_payload(task) for task in self.task_manager.list_tasks(include_recent=True)]}

    async def list_triggers(self) -> dict[str, Any]:
        return {"triggers": [_trigger_payload(trigger) for trigger in self.trigger_store.list_triggers()]}

    async def update_trigger(self, trigger_id: str, action: str) -> dict[str, Any]:
        if action == "pause":
            trigger = self.trigger_store.mark_paused(trigger_id)
        elif action == "resume":
            trigger = self.trigger_store.mark_active(trigger_id)
        elif action == "cancel":
            trigger = self.trigger_store.mark_cancelled(trigger_id)
        else:
            return {"error": "Unsupported trigger action.", "status": "bad_request"}
        if trigger is None:
            return {"error": "No trigger found.", "status": "missing"}
        return {"trigger": _trigger_payload(trigger)}

    async def task_detail(self, task_id: str) -> dict[str, Any]:
        task = self.task_manager.get_task(task_id)
        if task is None:
            job = self.job_store.get_job(task_id)
            if job is None:
                return {"error": "No task found."}
            return {
                "job": {
                    "id": job.id,
                    "title": job.title,
                    "status": job.status.value,
                    "sourceFrontend": job.source_frontend,
                    "request": _redact_api_value(job.original_user_input),
                    "createdAt": job.created_at,
                    "updatedAt": job.updated_at,
                    "completedAt": job.completed_at,
                    "normalizedTaskSpec": _redact_api_value(job.normalized_task_spec),
                    "currentPlan": job.current_plan,
                    "currentStep": job.current_step,
                    "artifacts": _redact_api_value(job.artifacts),
                    "approvals": list(job.approvals),
                    "cancellationRequested": job.cancellation_requested,
                    "rollback": _redact_api_value(job.rollback),
                    "riskLevel": job.risk_level.value,
                    "requiredCapabilities": job.required_capabilities,
                    "usedCapabilities": job.used_capabilities,
                    "affectedTargets": _redact_api_value(job.affected_targets),
                    "result": _redact_api_value(job.final_result),
                    "error": _redact_api_value(job.error),
                }
            }
        return {"task": _task_payload(task)}

    async def job_logs(self, job_id: str) -> dict[str, Any]:
        return {
            "logs": [
                {
                    "id": entry.id,
                    "jobId": entry.job_id,
                    "timestamp": entry.timestamp,
                    "level": entry.level,
                    "message": _redact_api_value(entry.message),
                    "data": _redact_api_value(entry.data),
                }
                for entry in self.job_store.list_logs(job_id, limit=50)
            ]
        }

    async def job_artifacts(self, job_id: str) -> dict[str, Any]:
        job = self.job_store.get_job(job_id)
        if job is None:
            return {"error": "No job found.", "status": "missing"}
        return {"artifacts": _redact_api_value(job.artifacts)}

    async def clear_completed_tasks(self) -> dict[str, Any]:
        memory_count = self.task_manager.clear_terminal_tasks()
        store_count = self.job_store.clear_terminal_jobs()
        return {"cleared": max(memory_count, store_count)}

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.task_manager.get_task(task_id)
        if task is None:
            return {"error": "No task found.", "status": "missing"}
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return {"status": task.status.value}
        self.task_manager.cancel_task(task_id)
        await self.task_manager.wait_for_task(task_id)
        return {"status": task.status.value}

    async def list_approvals(self) -> dict[str, Any]:
        return {
            "approvals": [
                {
                    "id": approval.id,
                    "tool": approval.tool_name,
                    "title": approval.title,
                    "details": _redact_api_value(approval.details),
                    "expiresAt": approval.expires_at,
                }
                for approval in self.approvals.list_pending()
            ]
        }

    async def approve(self, approval_id: str) -> dict[str, Any]:
        return {"approved": self.approvals.approve(approval_id)}

    async def reject(self, approval_id: str) -> dict[str, Any]:
        return {"rejected": self.approvals.reject(approval_id)}

    def _context_state(self):
        from runtime.models import LiveRuntimeState

        state = LiveRuntimeState()
        state.protocol_history = self.protocol_history
        state.semantic_history = self.semantic_history
        return state

    def subscribe_task_events(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=50)
        with self._task_event_lock:
            self._task_event_subscribers.add(subscriber)
        return subscriber

    def unsubscribe_task_events(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._task_event_lock:
            self._task_event_subscribers.discard(subscriber)

    def _on_task_event(self, task: BackgroundTask, event: str) -> None:
        payload = {"event": "task", "type": event, "task": _task_payload(task)}
        with self._task_event_lock:
            subscribers = list(self._task_event_subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    subscriber.get_nowait()
                with contextlib.suppress(queue.Full):
                    subscriber.put_nowait(payload)

    async def shutdown(self) -> None:
        if self._owns_components:
            await self.task_manager.shutdown()
            await self.browser_session.close()
            self.trigger_store.close()
            self.job_store.close()

    def close(self) -> None:
        try:
            if not self._owns_components:
                self.task_manager.remove_event_listener(self._on_task_event)
            self.run_sync(self.shutdown(), timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
            if not self._loop.is_closed():
                self._loop.close()


def render_index_html(settings: Settings) -> str:
    realtime_label = "Realtime" if settings.REALTIME_VOICE_ENABLED else "Realtime off"
    local_label = "Local Whisper"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Eyra</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b0f14;
      --panel: #101820;
      --panel-2: #16212b;
      --text: #edf6f9;
      --muted: #94a8b4;
      --line: #263746;
      --accent: #55d6be;
      --accent-2: #f6bd60;
      --danger: #f28482;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 20% -10%, rgba(85, 214, 190, 0.16), transparent 30%),
        linear-gradient(180deg, #0b0f14 0%, #0f151c 100%);
      color: var(--text);
    }}
    main {{
      width: min(920px, 100%);
      min-height: 100vh;
      margin: 0 auto;
      display: grid;
      grid-template-rows: auto 1fr auto;
      padding: 18px;
      gap: 14px;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 14px;
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0;
    }}
    .status {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
      color: var(--muted);
      font-size: 13px;
    }}
    .pill {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      padding: 6px 10px;
      white-space: nowrap;
    }}
    #messages {{
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 4px 0;
    }}
    .msg {{
      max-width: 86%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .user {{
      align-self: flex-end;
      background: #13352f;
      border-color: rgba(85, 214, 190, 0.32);
    }}
    .eyra {{
      align-self: flex-start;
      background: var(--panel);
    }}
    .error {{
      border-color: rgba(242, 132, 130, 0.65);
      color: #ffd7d7;
    }}
    #tasks {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    #connectors, #approvals {{
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .task, .connector, .approval {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 8px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      background: rgba(16, 24, 32, 0.74);
    }}
    .task button {{
      min-height: 34px;
      padding: 0 10px;
    }}
    form {{
      display: grid;
      grid-template-columns: 154px minmax(0, 1fr) 56px 92px;
      gap: 10px;
      align-items: end;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }}
    textarea {{
      width: 100%;
      min-height: 48px;
      max-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      padding: 12px;
      font: inherit;
    }}
    button, select {{
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
      padding: 0 14px;
    }}
    #micButton {{
      padding: 0;
      width: 56px;
    }}
    button.primary {{
      background: var(--accent);
      color: #06211d;
      border-color: var(--accent);
      font-weight: 700;
    }}
    #micButton.active {{
      border-color: var(--accent-2);
      color: var(--accent-2);
    }}
    @media (max-width: 640px) {{
      main {{ padding: 12px; }}
      header {{ align-items: flex-start; flex-direction: column; }}
      .status {{ justify-content: flex-start; }}
      .msg {{ max-width: 94%; }}
      form {{ grid-template-columns: 1fr auto; }}
      select {{ grid-column: 1 / -1; }}
      textarea {{ grid-column: 1 / 2; }}
      #micButton {{ grid-column: 2 / 3; }}
      button.primary {{ grid-column: 1 / -1; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Eyra</h1>
      <div class="status">
        <span class="pill">{escape(local_label)}</span>
        <span class="pill">{escape(realtime_label)}</span>
        <span class="pill">Network {'on' if settings.NETWORK_TOOLS_ENABLED else 'off'}</span>
        <span class="pill">OS tools {'on' if settings.OS_TOOLS_ENABLED else 'off'}</span>
        <span class="pill">MCP {'on' if settings.MCP_TOOLS_ENABLED else 'off'}</span>
        <span class="pill">Connectors {'on' if settings.CONNECTORS_ENABLED else 'off'}</span>
      </div>
    </header>
    <section id="messages" aria-live="polite"></section>
    <section id="connectors" aria-live="polite"></section>
    <section id="approvals" aria-live="polite"></section>
    <section id="tasks" aria-live="polite"></section>
    <form id="chatForm">
      <select id="voiceMode" aria-label="Voice mode">
        <option value="text">Text</option>
        <option value="local">Local Whisper</option>
        <option value="realtime">Realtime</option>
      </select>
      <textarea id="prompt" name="prompt" placeholder="Ask Eyra about this Mac..." autocomplete="off"></textarea>
      <button id="micButton" type="button" title="Voice input">Mic</button>
      <button class="primary" type="submit">Send</button>
    </form>
  </main>
  <script>
    const messages = document.getElementById('messages');
    const form = document.getElementById('chatForm');
    const prompt = document.getElementById('prompt');
    const micButton = document.getElementById('micButton');
    const voiceMode = document.getElementById('voiceMode');
    const tasks = document.getElementById('tasks');
    const connectors = document.getElementById('connectors');
    const approvals = document.getElementById('approvals');
    let currentTasks = [];

    function addMessage(role, text, extraClass = '') {{
      const el = document.createElement('div');
      el.className = `msg ${{role}} ${{extraClass}}`;
      el.textContent = text;
      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
      return el;
    }}

    async function send(text) {{
      addMessage('user', text);
      const reply = addMessage('eyra', 'Thinking...');
      try {{
        const response = await fetch('/api/chat', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
          body: JSON.stringify({{ text, voiceMode: voiceMode.value }}),
        }});
        const data = await response.json();
        reply.textContent = data.reply || data.error || '';
        if (!response.ok) reply.classList.add('error');
        loadTasks();
        loadConnectors();
        loadApprovals();
      }} catch (error) {{
        reply.textContent = 'Could not reach Eyra on this machine.';
        reply.classList.add('error');
      }}
    }}

    function renderTasks(rows) {{
      currentTasks = rows || [];
      tasks.replaceChildren();
      for (const task of currentTasks.slice(0, 8)) {{
        const row = document.createElement('div');
        row.className = 'task';
        const label = document.createElement('div');
        label.textContent = `${{task.id}} · ${{task.status}} · ${{task.title}}`;
        row.appendChild(label);
        if (['queued', 'running'].includes(task.status)) {{
          const button = document.createElement('button');
          button.textContent = 'Cancel';
          button.onclick = async () => {{
            await fetch('/api/cancel', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
              body: JSON.stringify({{ taskId: task.id }}),
            }});
            loadTasks();
          }};
          row.appendChild(button);
        }}
        tasks.appendChild(row);
      }}
    }}

    function applyTaskEvent(task) {{
      if (!task || !task.id) return;
      const index = currentTasks.findIndex((item) => item.id === task.id);
      if (index >= 0) {{
        currentTasks[index] = task;
      }} else {{
        currentTasks.unshift(task);
      }}
      renderTasks(currentTasks);
    }}

    async function loadTasks() {{
      try {{
        const response = await fetch('/api/tasks', {{ headers: {{ 'X-Eyra-Web-Token': webToken }} }});
        if (!response.ok) return;
        const data = await response.json();
        renderTasks(data.tasks || []);
      }} catch (_) {{}}
    }}

    function renderConnectors(rows) {{
      connectors.replaceChildren();
      for (const connector of (rows || []).slice(0, 6)) {{
        const row = document.createElement('div');
        row.className = 'connector';
        const label = document.createElement('div');
        label.textContent = `${{connector.id}} · ${{connector.enabled ? 'on' : 'off'}} · ${{connector.acceptanceState}} · ${{connector.riskTier}}`;
        row.appendChild(label);
        const test = document.createElement('button');
        test.textContent = 'Test';
        test.onclick = async () => {{
          await fetch('/api/connector/test', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
            body: JSON.stringify({{ connectorId: connector.id }}),
          }});
          loadConnectors();
        }};
        row.appendChild(test);
        const cancel = document.createElement('button');
        cancel.textContent = 'Cancel';
        cancel.onclick = async () => {{
          await fetch('/api/connector/cancel', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
            body: JSON.stringify({{ connectorId: connector.id }}),
          }});
          loadTasks();
        }};
        row.appendChild(cancel);
        connectors.appendChild(row);
      }}
    }}

    async function loadConnectors() {{
      try {{
        const response = await fetch('/api/connectors', {{ headers: {{ 'X-Eyra-Web-Token': webToken }} }});
        if (!response.ok) return;
        const data = await response.json();
        renderConnectors(data.connectors || []);
      }} catch (_) {{}}
    }}

    function renderApprovals(rows) {{
      approvals.replaceChildren();
      for (const approval of (rows || []).slice(0, 6)) {{
        const row = document.createElement('div');
        row.className = 'approval';
        const label = document.createElement('div');
        label.textContent = `${{approval.id}} · ${{approval.tool}} · ${{approval.title}}`;
        row.appendChild(label);
        for (const action of ['approve', 'reject']) {{
          const button = document.createElement('button');
          button.textContent = action === 'approve' ? 'Approve' : 'Reject';
          button.onclick = async () => {{
            await fetch(`/api/${{action}}`, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
              body: JSON.stringify({{ approvalId: approval.id }}),
            }});
            loadApprovals();
          }};
          row.appendChild(button);
        }}
        approvals.appendChild(row);
      }}
    }}

    async function loadApprovals() {{
      try {{
        const response = await fetch('/api/approvals', {{ headers: {{ 'X-Eyra-Web-Token': webToken }} }});
        if (!response.ok) return;
        const data = await response.json();
        renderApprovals(data.approvals || []);
      }} catch (_) {{}}
    }}

    form.addEventListener('submit', (event) => {{
      event.preventDefault();
      const text = prompt.value.trim();
      if (!text) return;
      prompt.value = '';
      send(text);
    }});

    let recorder = null;
    let chunks = [];
    let realtime = null;
    const webToken = new URLSearchParams(window.location.search).get('token') || sessionStorage.getItem('eyraWebToken') || '';
    if (webToken) sessionStorage.setItem('eyraWebToken', webToken);

    function connectTaskEvents() {{
      if (!window.EventSource) return;
      const params = new URLSearchParams();
      if (webToken) params.set('token', webToken);
      const source = new EventSource(`/api/events?${{params.toString()}}`);
      source.addEventListener('snapshot', (event) => {{
        try {{
          const data = JSON.parse(event.data);
          renderTasks(data.tasks || []);
        }} catch (_) {{}}
      }});
      source.addEventListener('task', (event) => {{
        try {{
          const data = JSON.parse(event.data);
          applyTaskEvent(data.task);
        }} catch (_) {{}}
      }});
    }}

    async function sendLocalAudio(blob) {{
      const reply = addMessage('eyra', 'Listening...');
      try {{
        const response = await fetch('/api/local-voice-turn', {{
          method: 'POST',
          headers: {{ 'Content-Type': blob.type || 'application/octet-stream', 'X-Eyra-Web-Token': webToken }},
          body: blob,
        }});
        const data = await response.json();
        reply.textContent = data.reply || data.error || '';
        if (data.transcript) addMessage('user', data.transcript);
        if (data.reply) speakLocal(data.reply);
        if (!response.ok) reply.classList.add('error');
      }} catch (_) {{
        reply.textContent = 'Local voice failed on this machine.';
        reply.classList.add('error');
      }}
    }}

    async function speakLocal(text) {{
      try {{
        await fetch('/api/local-speak', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
          body: JSON.stringify({{ text }}),
        }});
      }} catch (_) {{}}
    }}

    async function toggleLocalRecording() {{
      if (recorder && recorder.state === 'recording') {{
        recorder.stop();
        micButton.classList.remove('active');
        micButton.textContent = 'Mic';
        return;
      }}
      if (!navigator.mediaDevices || !window.MediaRecorder) {{
        addMessage('eyra', 'This browser cannot record audio for Local Whisper.', 'error');
        return;
      }}
      const stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
      chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (event) => {{
        if (event.data && event.data.size) chunks.push(event.data);
      }};
      recorder.onstop = () => {{
        stream.getTracks().forEach((track) => track.stop());
        sendLocalAudio(new Blob(chunks, {{ type: recorder.mimeType || 'audio/webm' }}));
      }};
      recorder.start();
      micButton.classList.add('active');
      micButton.textContent = 'Stop';
    }}

    async function callRealtimeTool(event, dc) {{
      const response = await fetch('/api/realtime-tool-call', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'X-Eyra-Web-Token': webToken,
          'X-Eyra-Realtime-Tool-Token': realtime?.toolToken || '',
        }},
        body: JSON.stringify({{ name: event.name, arguments: event.arguments || '{{}}' }}),
      }});
      const data = await response.json();
      dc.send(JSON.stringify({{
        type: 'conversation.item.create',
        item: {{
          type: 'function_call_output',
          call_id: event.call_id,
          output: data.output || data.error || ''
        }}
      }}));
      dc.send(JSON.stringify({{ type: 'response.create' }}));
    }}

    async function toggleRealtime() {{
      if (realtime) {{
        realtime.stream.getTracks().forEach((track) => track.stop());
        realtime.pc.close();
        realtime = null;
        micButton.classList.remove('active');
        micButton.textContent = 'Mic';
        addMessage('eyra', 'Realtime voice stopped.');
        return;
      }}
      if (!navigator.mediaDevices || !window.RTCPeerConnection) {{
        addMessage('eyra', 'This browser does not support Realtime voice.', 'error');
        return;
      }}
      const tokenResponse = await fetch('/api/realtime-session', {{
        method: 'POST',
        headers: {{ 'X-Eyra-Web-Token': webToken }},
      }});
      const tokenData = await tokenResponse.json();
      const ephemeralKey = tokenData.value || tokenData.client_secret?.value || tokenData.client_secret;
      const toolToken = tokenData.eyra_tool_token || '';
      if (!tokenResponse.ok || !ephemeralKey) {{
        addMessage('eyra', tokenData.error || 'Realtime setup failed.', 'error');
        return;
      }}
      const pc = new RTCPeerConnection();
      const stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      const audio = document.createElement('audio');
      audio.autoplay = true;
      pc.ontrack = (event) => {{ audio.srcObject = event.streams[0]; }};
      const dc = pc.createDataChannel('oai-events');
      dc.addEventListener('message', (message) => {{
        const event = JSON.parse(message.data);
        if (event.type === 'response.audio_transcript.done' && event.transcript) {{
          addMessage('eyra', event.transcript);
        }}
        if (event.type === 'response.function_call_arguments.done') {{
          callRealtimeTool(event, dc);
        }}
      }});
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const sdpResponse = await fetch('https://api.openai.com/v1/realtime/calls', {{
        method: 'POST',
        body: offer.sdp,
        headers: {{
          Authorization: `Bearer ${{ephemeralKey}}`,
          'Content-Type': 'application/sdp',
        }},
      }});
      if (!sdpResponse.ok) {{
        stream.getTracks().forEach((track) => track.stop());
        addMessage('eyra', 'Realtime SDP exchange failed.', 'error');
        return;
      }}
      await pc.setRemoteDescription({{ type: 'answer', sdp: await sdpResponse.text() }});
      realtime = {{ pc, stream, dc, audio, toolToken }};
      micButton.classList.add('active');
      micButton.textContent = 'Stop';
      addMessage('eyra', 'Realtime voice connected.');
    }}

    micButton.addEventListener('click', async () => {{
      if (voiceMode.value === 'realtime') {{
        try {{
          await toggleRealtime();
        }} catch (error) {{
          addMessage('eyra', 'Realtime voice failed to start.', 'error');
        }}
        return;
      }}
      try {{
        await toggleLocalRecording();
      }} catch (_) {{
        addMessage('eyra', 'Local voice failed to start.', 'error');
      }}
    }});
    connectTaskEvents();
    loadTasks();
    loadConnectors();
    loadApprovals();
  </script>
</body>
</html>"""


class _EyraWebHandler(BaseHTTPRequestHandler):
    settings: Settings
    runtime: WebAssistantRuntime
    web_session_token: str
    realtime_tool_token: str

    def log_message(self, *_):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send(200, render_index_html(self.settings), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/health":
            self._send_json(
                200,
                build_health_payload(
                    self.settings,
                    runtime_scope=self.runtime.runtime_scope,
                    preflight=self.runtime.preflight,
                ),
            )
            return
        if parsed.path == "/favicon.ico":
            self._send(204, "", "image/x-icon")
            return
        if parsed.path == "/api/events":
            if not self._authorized(allow_query_token=True):
                return
            self._send_event_stream()
            return
        if parsed.path == "/api/tasks":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.list_tasks()))
            return
        if parsed.path == "/api/capabilities":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.capabilities()))
            return
        if parsed.path == "/api/context":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.context_snapshot()))
            return
        if parsed.path == "/api/connectors":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.connectors()))
            return
        connector_match = re.fullmatch(r"/api/connector/([^/]+)", parsed.path)
        if connector_match:
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.connector_detail(connector_match.group(1))))
            return
        if parsed.path == "/api/route/last":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.route_last()))
            return
        if parsed.path == "/api/doctor":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.support_diagnostics()))
            return
        job_logs_match = re.fullmatch(r"/api/job/([^/]+)/logs", parsed.path)
        if job_logs_match:
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.job_logs(job_logs_match.group(1))))
            return
        job_artifacts_match = re.fullmatch(r"/api/job/([^/]+)/artifacts", parsed.path)
        if job_artifacts_match:
            if not self._authorized():
                return
            payload = self.runtime.run_sync(self.runtime.job_artifacts(job_artifacts_match.group(1)))
            self._send_json(200 if "artifacts" in payload else 404, payload)
            return
        if parsed.path == "/api/triggers":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.list_triggers()))
            return
        if parsed.path == "/api/approvals":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.list_approvals()))
            return
        if parsed.path.startswith("/api/task/"):
            if not self._authorized():
                return
            task_id = parsed.path.rsplit("/", 1)[-1]
            payload = self.runtime.run_sync(self.runtime.task_detail(task_id))
            self._send_json(200 if "task" in payload or "job" in payload else 404, payload)
            return
        self._send_json(404, {"error": "Not found."})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {
            "/api/chat",
            "/api/local-voice-turn",
            "/api/local-speak",
            "/api/realtime-session",
            "/api/realtime-tool-call",
            "/api/cancel",
            "/api/tasks/clear-completed",
            "/api/trigger",
            "/api/approve",
            "/api/reject",
            "/api/connector/test",
            "/api/connector/run",
            "/api/connector/cancel",
        } and not self._authorized():
            return
        if parsed.path in {
            "/api/chat",
            "/api/local-voice-turn",
            "/api/local-speak",
            "/api/realtime-session",
            "/api/realtime-tool-call",
            "/api/cancel",
            "/api/tasks/clear-completed",
            "/api/trigger",
            "/api/approve",
            "/api/reject",
            "/api/connector/test",
            "/api/connector/run",
            "/api/connector/cancel",
        } and self._reject_if_too_large(
            max_bytes=25 * 1024 * 1024 if parsed.path == "/api/local-voice-turn" else 1_000_000
        ):
            return
        if parsed.path == "/api/chat":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "Message is empty."})
                return
            try:
                result = self.runtime.run_sync(
                    self.runtime.handle_message(text, str(payload.get("voiceMode", "text"))),
                    timeout=30,
                )
            except Exception:
                self._send_json(500, {"error": "Eyra could not answer that request. Check the terminal logs."})
                return
            self._send_json(200, result)
            return
        if parsed.path == "/api/cancel":
            payload = self._read_json()
            task_id = str(payload.get("taskId", "")).strip()
            if not task_id:
                self._send_json(400, {"error": "taskId is required."})
                return
            result = self.runtime.run_sync(self.runtime.cancel_task(task_id))
            self._send_json(200 if result.get("status") != "missing" else 404, result)
            return
        if parsed.path == "/api/tasks/clear-completed":
            self._send_json(200, self.runtime.run_sync(self.runtime.clear_completed_tasks()))
            return
        if parsed.path == "/api/trigger":
            payload = self._read_json()
            trigger_id = str(payload.get("triggerId", "")).strip()
            action = str(payload.get("action", "")).strip().lower()
            if not trigger_id or not action:
                self._send_json(400, {"error": "triggerId and action are required."})
                return
            result = self.runtime.run_sync(self.runtime.update_trigger(trigger_id, action))
            status = result.get("status")
            if status == "missing":
                self._send_json(404, result)
            elif status == "bad_request":
                self._send_json(400, result)
            else:
                self._send_json(200, result)
            return
        if parsed.path == "/api/approve":
            payload = self._read_json()
            approval_id = str(payload.get("approvalId", "")).strip()
            if not approval_id:
                self._send_json(400, {"error": "approvalId is required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.approve(approval_id)))
            return
        if parsed.path == "/api/reject":
            payload = self._read_json()
            approval_id = str(payload.get("approvalId", "")).strip()
            if not approval_id:
                self._send_json(400, {"error": "approvalId is required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.reject(approval_id)))
            return
        if parsed.path == "/api/connector/test":
            payload = self._read_json()
            connector_id = str(payload.get("connectorId", "")).strip()
            approval_id = str(payload.get("approvalId", "")).strip()
            if not connector_id:
                self._send_json(400, {"error": "connectorId is required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.test_connector(connector_id, approval_id), timeout=30))
            return
        if parsed.path == "/api/connector/run":
            payload = self._read_json()
            connector_id = str(payload.get("connectorId", "")).strip()
            task_text = str(payload.get("task", "")).strip()
            if not connector_id or not task_text:
                self._send_json(400, {"error": "connectorId and task are required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.run_connector(connector_id, task_text)))
            return
        if parsed.path == "/api/connector/cancel":
            payload = self._read_json()
            connector_id = str(payload.get("connectorId", "")).strip()
            if not connector_id:
                self._send_json(400, {"error": "connectorId is required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.cancel_connector(connector_id)))
            return
        if parsed.path == "/api/local-voice-turn":
            payload = self._read_bytes(max_bytes=25 * 1024 * 1024)
            if not payload:
                self._send_json(400, {"error": "No audio was received."})
                return
            transcript = transcribe_local_audio(payload)
            if transcript.startswith("Local Whisper error:"):
                self._send_json(500, {"error": transcript})
                return
            try:
                result = self.runtime.run_sync(self.runtime.handle_message(transcript, "local"), timeout=30)
            except Exception:
                self._send_json(500, {"error": "Eyra could not answer that voice request.", "transcript": transcript})
                return
            result["transcript"] = transcript
            self._send_json(200, result)
            return
        if parsed.path == "/api/local-speak":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "No text was provided."})
                return
            message = speak_local_text(text)
            status = 200 if message == "Local speech started." else 500
            self._send_json(status, {"status": message})
            return
        if parsed.path == "/api/realtime-session":
            status, payload = create_realtime_session_payload(self.settings)
            if status == 200 and self.settings.REALTIME_TOOLS_ENABLED:
                payload["eyra_tool_token"] = self.realtime_tool_token
            self._send_json(status, payload)
            return
        if parsed.path == "/api/realtime-tool-call":
            web_token = self.headers.get("X-Eyra-Web-Token", "")
            token = self.headers.get("X-Eyra-Realtime-Tool-Token", "")
            if not validate_web_session_token(web_token, self.web_session_token) or not validate_realtime_tool_token(
                self.settings,
                token,
                self.realtime_tool_token,
            ):
                self._send_json(403, {"error": "Realtime tool calls are disabled or unauthorized."})
                return
            payload = self._read_json()
            output = self.runtime.run_sync(call_realtime_tool(self.settings, payload))
            self._send_json(200, {"output": output})
            return
        self._send_json(404, {"error": "Not found."})

    def do_PUT(self):
        self._send_json(405, {"error": "Method not allowed."})

    def do_DELETE(self):
        self._send_json(405, {"error": "Method not allowed."})

    def _authorized(self, allow_query_token: bool = False) -> bool:
        if not self._origin_allowed():
            self._send_json(403, {"error": "Cross-origin Web UI request refused."})
            return False
        if not web_auth_required(self.settings):
            return True
        provided = self.headers.get("X-Eyra-Web-Token", "")
        if allow_query_token and not provided:
            parsed = urllib.parse.urlparse(self.path)
            provided = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        if validate_web_session_token(provided, self.web_session_token):
            return True
        self._send_json(401, {"error": "Web UI session token is required."})
        return False

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        parsed = urllib.parse.urlparse(origin)
        origin_host = (parsed.hostname or "").lower()
        host_header = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        configured_host = self.settings.WEB_UI_HOST.strip("[]").lower()
        allowed_hosts = {host for host in _LOCAL_HOSTS | {configured_host, host_header} if host}
        return parsed.scheme in {"http", "https"} and origin_host in allowed_hosts

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_bytes()
        try:
            return json.loads(raw.decode())
        except json.JSONDecodeError:
            return {}

    def _read_bytes(self, max_bytes: int = 1_000_000) -> bytes:
        length = int(self.headers.get("content-length", "0"))
        limit = min(max_bytes, max(1, int(self.settings.WEB_UI_MAX_REQUEST_BYTES)))
        if length <= 0 or length > limit:
            return b""
        return self.rfile.read(length)

    def _reject_if_too_large(self, max_bytes: int = 1_000_000) -> bool:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid content length."})
            return True
        limit = min(max_bytes, max(1, int(self.settings.WEB_UI_MAX_REQUEST_BYTES)))
        if length > limit:
            self._send_json(413, {"error": f"Request body is too large. Limit is {limit} bytes."})
            return True
        return False

    def _send_event_stream(self) -> None:
        subscriber = self.runtime.subscribe_task_events()
        try:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "keep-alive")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("x-frame-options", "DENY")
            self.end_headers()

            def send_event(name: str, payload: dict[str, Any]) -> None:
                raw = f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
                self.wfile.write(raw)
                self.wfile.flush()

            send_event("snapshot", self.runtime.run_sync(self.runtime.list_tasks()))
            while True:
                try:
                    payload = subscriber.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                send_event(str(payload.get("event", "message")), payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.runtime.unsubscribe_task_events(subscriber)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload), "application/json; charset=utf-8")

    def _send(self, status: int, body: str, content_type: str) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.send_header("referrer-policy", "no-referrer")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("x-frame-options", "DENY")
        self.send_header("permissions-policy", "geolocation=(), camera=(), microphone=(self)")
        self.send_header(
            "content-security-policy",
            "default-src 'self'; "
            "base-uri 'none'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self' https://api.openai.com; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'",
        )
        self.end_headers()
        self.wfile.write(raw)


def create_realtime_session_payload(settings: Settings) -> tuple[int, dict[str, Any]]:
    if not settings.REALTIME_VOICE_ENABLED:
        return 400, {"error": "Realtime voice is disabled. Set REALTIME_VOICE_ENABLED=true to use online voice."}
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return 400, {"error": "OPENAI_API_KEY is not configured for Realtime voice."}
    session: dict[str, Any] = {
        "type": "realtime",
        "model": settings.REALTIME_MODEL,
        "instructions": (
            "You are Eyra, a local-first macOS assistant. Realtime voice is an online mode. "
            "Keep spoken replies short and clear."
        ),
        "audio": {"output": {"voice": settings.REALTIME_VOICE}},
    }
    tools = realtime_tools(settings)
    if tools:
        session["tools"] = tools
        session["tool_choice"] = "auto"
    body = json.dumps(
        {
            "session": session,
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/realtime/client_secrets",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Safety-Identifier": "eyra-local-session",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": "Realtime session request failed.", "detail": e.read().decode(errors="replace")}
    except OSError as e:
        return 502, {"error": f"Could not reach OpenAI Realtime: {e}"}


def validate_realtime_tool_token(settings: Settings, provided: str, expected: str) -> bool:
    if not settings.REALTIME_VOICE_ENABLED or not settings.REALTIME_TOOLS_ENABLED:
        return False
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def validate_web_session_token(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def realtime_tools(settings: Settings) -> list[dict[str, Any]]:
    if not settings.REALTIME_TOOLS_ENABLED:
        return []
    configured = {name.strip() for name in settings.REALTIME_ALLOWED_TOOLS.split(",") if name.strip()}
    allowed = (configured or _SAFE_REALTIME_TOOLS) & _SAFE_REALTIME_TOOLS
    tools = []
    for tool in build_tool_registry(settings).to_openai_tools(include_costly=False):
        fn = tool.get("function", {})
        if fn.get("name") not in allowed:
            continue
        tools.append(
            {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


async def call_realtime_tool(settings: Settings, payload: dict[str, Any]) -> str:
    name = str(payload.get("name", ""))
    allowed = {
        tool.get("name")
        for tool in realtime_tools(settings)
        if isinstance(tool, dict) and tool.get("type") == "function"
    }
    if name not in allowed:
        return f"Realtime tool is not allowed: {name}"
    raw_arguments = payload.get("arguments", "{}")
    if isinstance(raw_arguments, str):
        arguments = raw_arguments
    else:
        arguments = json.dumps(raw_arguments)
    result = await build_tool_registry(settings).execute(name, arguments)
    return result.content


def transcribe_local_audio(audio: bytes) -> str:
    wh = resolve_wh_bin()
    if not wh:
        return "Local Whisper error: wh is not installed or not on PATH."
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp:
            temp.write(audio)
            temp_path = temp.name
        completed = subprocess.run(
            [wh, "transcribe", temp_path, "--raw"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            return "Local Whisper error: " + (completed.stderr.strip() or completed.stdout.strip() or "transcription failed")
        transcript = completed.stdout.strip()
        return transcript or "Local Whisper error: no speech was detected."
    except Exception as e:
        return f"Local Whisper error: {e}"
    finally:
        if temp_path:
            try:
                import os

                os.unlink(temp_path)
            except OSError:
                pass


def resolve_wh_bin() -> str | None:
    candidates = [
        shutil.which("wh"),
        "/opt/homebrew/bin/wh",
        "/usr/local/bin/wh",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    return None


def speak_local_text(text: str) -> str:
    wh = resolve_wh_bin()
    if not wh:
        return "Local Whisper error: wh is not installed or not on PATH."
    try:
        completed = subprocess.run(
            [wh, "whisper", text[:500]],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as e:
        return f"Local Whisper error: {e}"
    if completed.returncode != 0:
        return "Local Whisper error: " + (completed.stderr.strip() or "speech failed")
    return "Local speech started."


def _web_preflight_problem(preflight: PreflightResult) -> str | None:
    if not preflight.backend_reachable:
        return "AI provider is not ready. Start Ollama, or run `eyra setup` to choose a provider."
    if preflight.models_missing:
        return "Model needs setup: " + ", ".join(preflight.models_missing) + ". Run `eyra setup` for guided repair."
    return None


def _build_handler(
    *,
    settings: Settings,
    runtime: WebAssistantRuntime,
    web_session_token: str,
    realtime_tool_token: str,
):
    return type(
        "EyraWebHandler",
        (_EyraWebHandler,),
        {
            "settings": settings,
            "runtime": runtime,
            "web_session_token": web_session_token,
            "realtime_tool_token": realtime_tool_token,
        },
    )


def start_web_server_in_thread(
    settings: Settings,
    *,
    runtime: WebAssistantRuntime,
    web_session_token: str = "",
    realtime_tool_token: str = "",
) -> WebServerHandle:
    """Start the Web UI in a background thread with the provided runtime."""
    web_session_token = web_session_token or settings.WEB_UI_TOKEN.strip() or secrets.token_urlsafe(32)
    realtime_tool_token = realtime_tool_token or secrets.token_urlsafe(32)
    handler = _build_handler(
        settings=settings,
        runtime=runtime,
        web_session_token=web_session_token,
        realtime_tool_token=realtime_tool_token,
    )
    server = EyraThreadingHTTPServer((settings.WEB_UI_HOST, settings.WEB_UI_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, name="eyra-web-ui", daemon=True)
    thread.start()
    return WebServerHandle(
        server=server,
        thread=thread,
        runtime=runtime,
        web_session_token=web_session_token,
        realtime_tool_token=realtime_tool_token,
    )


def run_web_server(settings: Settings, preflight: PreflightResult | None = None) -> None:
    preflight = preflight or asyncio.run(PreflightManager(settings).run())
    problem = _web_preflight_problem(preflight)
    if problem:
        print(problem)
        return

    web_session_token = settings.WEB_UI_TOKEN.strip() or secrets.token_urlsafe(32)
    realtime_tool_token = secrets.token_urlsafe(32)
    runtime = WebAssistantRuntime(settings, preflight=preflight)
    handler = _build_handler(
        settings=settings,
        runtime=runtime,
        web_session_token=web_session_token,
        realtime_tool_token=realtime_tool_token,
    )
    try:
        server = EyraThreadingHTTPServer((settings.WEB_UI_HOST, settings.WEB_UI_PORT), handler)
    except OSError as e:
        runtime.close()
        print(f"Could not start Eyra web UI on {settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}: {e}")
        return
    print(f"Eyra web UI: http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}")
    print(f"Eyra web UI runtime: {runtime.runtime_scope}")
    if web_auth_required(settings):
        print(f"Eyra web UI token URL: http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}/?token={web_session_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEyra web UI stopped.")
    finally:
        server.server_close()
        runtime.close()


def run() -> None:
    from runtime.startup import maybe_run_startup_selector

    maybe_run_startup_selector()
    run_web_server(Settings.load_from_env())


if __name__ == "__main__":
    run()
