"""Server-side approvals for risky local actions."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable


def approval_fingerprint(action: dict[str, Any]) -> str:
    """Stable fingerprint for the exact action the user approved."""
    raw = json.dumps(action, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class PendingApproval:
    id: str
    tool_name: str
    title: str
    details: dict[str, Any]
    fingerprint: str
    created_at: float
    expires_at: float
    approved: bool = False
    rejected: bool = False
    consumed: bool = False
    approved_at: float | None = None
    rejected_at: float | None = None
    consumed_at: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at


class ApprovalManager:
    """Action-specific, single-use approval store."""

    def __init__(self, ttl_seconds: int = 300, clock: Callable[[], float] | None = None):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock or time.time
        self._pending: dict[str, PendingApproval] = {}

    def request(self, tool_name: str, title: str, details: dict[str, Any]) -> PendingApproval:
        fingerprint = approval_fingerprint({"tool": tool_name, "title": title, "details": details})
        now = self._clock()
        approval = PendingApproval(
            id="appr-" + secrets.token_urlsafe(8),
            tool_name=tool_name,
            title=title,
            details=details,
            fingerprint=fingerprint,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._pending[approval.id] = approval
        return approval

    def list_pending(self) -> list[PendingApproval]:
        self._expire_old()
        return [
            approval
            for approval in self._pending.values()
            if not approval.consumed and not approval.rejected and not self._is_expired(approval)
        ]

    def get(self, approval_id: str) -> PendingApproval | None:
        self._expire_old()
        return self._pending.get(approval_id)

    def approve(self, approval_id: str) -> bool:
        approval = self.get(approval_id)
        if approval is None or approval.rejected or approval.consumed or self._is_expired(approval):
            return False
        approval.approved = True
        approval.approved_at = self._clock()
        return True

    def reject(self, approval_id: str) -> bool:
        approval = self.get(approval_id)
        if approval is None or approval.consumed or self._is_expired(approval):
            return False
        approval.rejected = True
        approval.rejected_at = self._clock()
        return True

    def consume(self, approval_id: str, tool_name: str, title: str, details: dict[str, Any]) -> bool:
        approval = self.get(approval_id)
        if approval is None or not approval.approved or approval.rejected or approval.consumed or self._is_expired(approval):
            return False
        fingerprint = approval_fingerprint({"tool": tool_name, "title": title, "details": details})
        if not secrets.compare_digest(approval.fingerprint, fingerprint):
            approval.notes.append("fingerprint mismatch")
            return False
        approval.consumed = True
        approval.consumed_at = self._clock()
        return True

    def _expire_old(self) -> None:
        now = self._clock()
        for approval in self._pending.values():
            if now > approval.expires_at and not approval.consumed:
                approval.notes.append("expired")

    def _is_expired(self, approval: PendingApproval) -> bool:
        return self._clock() > approval.expires_at


GLOBAL_APPROVAL_MANAGER = ApprovalManager()


def approval_required_message(approval: PendingApproval) -> str:
    return (
        f"Approval required for {approval.title}. Run /approve {approval.id} to allow this exact action "
        f"or /reject {approval.id} to deny it. Approval expires automatically."
    )
