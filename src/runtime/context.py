"""On-demand local context snapshot for grounding user references."""

from __future__ import annotations

import os

from runtime.jobs import DurableJobStore
from runtime.models import LiveRuntimeState
from utils.semantic_history import build_semantic_history, redact_semantic_text
from utils.settings import Settings


def build_context_snapshot(
    settings: Settings,
    *,
    state: LiveRuntimeState,
    job_store: DurableJobStore,
    cwd: str | None = None,
) -> dict:
    """Collect local, non-continuous context for reference resolution."""
    jobs = job_store.list_jobs(limit=5)
    operations = job_store.list_operations(limit=5)
    return {
        "currentGoal": state.current_goal,
        "cwd": redact_semantic_text(cwd or os.getcwd()),
        "filesystemDefaultPath": redact_semantic_text(settings.FILESYSTEM_DEFAULT_PATH),
        "recentMessages": state.semantic_history.recent(6)
        or build_semantic_history(state.conversation_messages, max_messages=6),
        "recentJobs": [
            {
                "id": job.id,
                "title": redact_semantic_text(job.title),
                "status": job.status.value,
                "source": job.source_frontend,
                "updatedAt": job.updated_at,
            }
            for job in jobs
        ],
        "recentOperations": [
            {
                "id": operation.id,
                "jobId": operation.job_id,
                "action": operation.normalized_action.get("type", "operation"),
                "target": redact_semantic_text(operation.target),
                "success": operation.success,
                "undo": _redact_mapping(operation.undo),
            }
            for operation in operations
        ],
        "privacy": {
            "localOnly": True,
            "continuousWatching": False,
        },
    }


def format_context_answer(snapshot: dict) -> str:
    """Return compact text for terminal and voice-readable context checks."""
    lines = ["Current context"]
    lines.append(f"Goal: {snapshot['currentGoal'] or 'none'}")
    lines.append(f"Working directory: {snapshot['cwd']}")
    lines.append(f"Filesystem default: {snapshot['filesystemDefaultPath']}")

    jobs = snapshot["recentJobs"]
    if jobs:
        lines.append("Recent jobs:")
        for job in jobs[:3]:
            lines.append(f"- {job['id']} {job['status']} {job['title']}")
    else:
        lines.append("Recent jobs: none")

    operations = snapshot["recentOperations"]
    if operations:
        lines.append("Recent changes:")
        for operation in operations[:3]:
            status = "ok" if operation["success"] else "failed"
            lines.append(f"- {operation['action']} {status} {operation['target']}")
    else:
        lines.append("Recent changes: none")

    return "\n".join(lines)


def _redact_mapping(value: dict) -> dict:
    return {str(key): redact_semantic_text(str(item)) for key, item in value.items()}
