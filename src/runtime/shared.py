"""Shared runtime ownership for terminal, Web UI, voice, and Realtime frontends."""

from __future__ import annotations

from dataclasses import dataclass

from chat.complexity_scorer import ComplexityScorer
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
    conversation: list[dict[str, str]]
    scorer: ComplexityScorer
    browser_session: BrowserSession
    approvals: ApprovalManager
    registry: ToolRegistry
    job_store: DurableJobStore
    trigger_store: TriggerStore
    task_manager: BackgroundTaskManager
    source_frontend: str = "terminal"

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
        return cls(
            settings=settings,
            preflight=preflight,
            conversation=[],
            scorer=ComplexityScorer(),
            browser_session=browser_session,
            approvals=approvals,
            registry=registry,
            job_store=job_store,
            trigger_store=trigger_store,
            task_manager=task_manager,
            source_frontend=source_frontend,
        )

    @classmethod
    def from_components(
        cls,
        *,
        settings: Settings,
        preflight: PreflightResult,
        conversation: list[dict[str, str]],
        scorer: ComplexityScorer,
        browser_session: BrowserSession,
        approvals: ApprovalManager,
        registry: ToolRegistry,
        job_store: DurableJobStore,
        trigger_store: TriggerStore,
        task_manager: BackgroundTaskManager,
        source_frontend: str = "terminal",
    ) -> "RuntimeSharedState":
        """Wrap already-owned runtime objects without taking cleanup ownership."""
        return cls(
            settings=settings,
            preflight=preflight,
            conversation=conversation,
            scorer=scorer,
            browser_session=browser_session,
            approvals=approvals,
            registry=registry,
            job_store=job_store,
            trigger_store=trigger_store,
            task_manager=task_manager,
            source_frontend=source_frontend,
        )

    async def shutdown(self) -> None:
        await self.task_manager.shutdown()
        await self.browser_session.close()
        self.trigger_store.close()
        self.job_store.close()

    def close(self) -> None:
        self.trigger_store.close()
        self.job_store.close()
