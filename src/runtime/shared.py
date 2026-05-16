"""Shared runtime ownership for terminal, Web UI, voice, and Realtime frontends."""

from __future__ import annotations

from dataclasses import dataclass

from chat.complexity_scorer import ComplexityScorer
from runtime.connectors.registry import ConnectorRegistry
from runtime.history import ProtocolHistory, SemanticHistory
from runtime.jobs import DurableJobStore
from runtime.models import PreflightResult
from runtime.tasks import BackgroundTaskManager, TaskEventCallback
from runtime.tooling import build_tool_registry
from runtime.triggers import TriggerStore
from tools.approval import ApprovalManager
from tools.browser import BrowserSession
from tools.registry import ToolRegistry
from utils.settings import Settings


@dataclass
class RuntimeSharedState:
    """Terminal-owned runtime objects that can be exposed to Web UI frontends."""

    settings: Settings
    preflight: PreflightResult
    protocol_history: ProtocolHistory
    semantic_history: SemanticHistory
    scorer: ComplexityScorer
    browser_session: BrowserSession
    approvals: ApprovalManager
    registry: ToolRegistry
    job_store: DurableJobStore
    trigger_store: TriggerStore
    task_manager: BackgroundTaskManager
    connector_registry: ConnectorRegistry
    source_frontend: str = "terminal"
    last_route_trace: object | None = None

    @property
    def conversation(self) -> list[dict]:
        """Deprecated raw protocol alias for model execution only."""
        return self.protocol_history.messages

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        preflight: PreflightResult,
        source_frontend: str = "terminal",
        on_task_event: TaskEventCallback | None = None,
    ) -> "RuntimeSharedState":
        """Create a complete local shared runtime owner."""
        browser_session = BrowserSession()
        approvals = ApprovalManager()
        registry = build_tool_registry(settings, browser_session=browser_session, approval_manager=approvals)
        job_store = DurableJobStore(settings.JOB_STORE_PATH)
        trigger_store = TriggerStore(settings.TRIGGER_STORE_PATH)
        task_manager = BackgroundTaskManager(
            max_concurrent=max(1, int(settings.MAX_BACKGROUND_TASKS)),
            task_timeout_seconds=max(1, int(settings.TASK_TIMEOUT_SECONDS)),
            on_event=on_task_event,
            job_store=job_store,
            source_frontend=source_frontend,
        )
        connector_registry = ConnectorRegistry.from_settings(settings, approvals=approvals, job_store=job_store)
        return cls(
            settings=settings,
            preflight=preflight,
            protocol_history=ProtocolHistory(),
            semantic_history=SemanticHistory(),
            scorer=ComplexityScorer(),
            browser_session=browser_session,
            approvals=approvals,
            registry=registry,
            job_store=job_store,
            trigger_store=trigger_store,
            task_manager=task_manager,
            connector_registry=connector_registry,
            source_frontend=source_frontend,
        )

    @classmethod
    def from_components(
        cls,
        *,
        settings: Settings,
        preflight: PreflightResult,
        scorer: ComplexityScorer,
        browser_session: BrowserSession,
        approvals: ApprovalManager,
        registry: ToolRegistry,
        job_store: DurableJobStore,
        trigger_store: TriggerStore,
        task_manager: BackgroundTaskManager,
        connector_registry: ConnectorRegistry,
        protocol_history: ProtocolHistory | None = None,
        semantic_history: SemanticHistory | None = None,
        conversation: list[dict[str, str]] | None = None,
        source_frontend: str = "terminal",
        last_route_trace: object | None = None,
    ) -> "RuntimeSharedState":
        """Wrap already-owned runtime objects without taking cleanup ownership."""
        return cls(
            settings=settings,
            preflight=preflight,
            protocol_history=protocol_history if protocol_history is not None else ProtocolHistory(list(conversation or [])),
            semantic_history=semantic_history if semantic_history is not None else SemanticHistory(),
            scorer=scorer,
            browser_session=browser_session,
            approvals=approvals,
            registry=registry,
            job_store=job_store,
            trigger_store=trigger_store,
            task_manager=task_manager,
            connector_registry=connector_registry,
            source_frontend=source_frontend,
            last_route_trace=last_route_trace,
        )

    async def shutdown(self) -> None:
        await self.task_manager.shutdown()
        await self.browser_session.close()
        self.trigger_store.close()
        self.job_store.close()

    def close(self) -> None:
        self.trigger_store.close()
        self.job_store.close()
