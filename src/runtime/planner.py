"""Deterministic local planner for common voice-to-computer tasks."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from runtime.actions import get_action_schema
from runtime.jobs import RiskLevel


@dataclass(frozen=True)
class TaskSpec:
    """Structured task spec used by jobs, approvals, verification, and recovery."""

    user_goal: str
    task_type: str
    target_refs: list[str]
    resolved_targets: list[str]
    success_criteria: list[str]
    required_context: list[str]
    required_capabilities: list[str]
    required_actions: list[str]
    risk_level: RiskLevel
    approval_needed: bool
    missing_information: list[str]
    execution_plan: list[str]
    verification_plan: list[str]
    rollback_plan: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        return data


def _capabilities(*action_types: str) -> list[str]:
    capabilities: list[str] = []
    for action_type in action_types:
        for capability in get_action_schema(action_type).required_capabilities:
            if capability not in capabilities:
                capabilities.append(capability)
    return capabilities


def _risk(*action_types: str) -> RiskLevel:
    levels = {
        RiskLevel.READ_ONLY: 0,
        RiskLevel.LOW_RISK_CHANGE: 1,
        RiskLevel.MEDIUM_RISK_CHANGE: 2,
        RiskLevel.HIGH_RISK_CHANGE: 3,
        RiskLevel.IRREVERSIBLE_OR_EXTERNAL: 4,
    }
    return max((get_action_schema(action_type).risk_level for action_type in action_types), key=lambda level: levels[level])


def plan_task(user_goal: str, context: dict[str, Any] | None = None) -> TaskSpec:
    """Build a local structured task spec for common computer-control requests."""
    goal = " ".join(user_goal.strip().split())
    lowered = goal.lower()
    context = context or {}

    if re.search(r"\bmove\b", lowered) and re.search(r"\bselected\b", lowered) and re.search(r"\bpdf\b", lowered):
        actions = ["observation.accessibility_tree", "file.move"]
        return TaskSpec(
            user_goal=goal,
            task_type="file.move",
            target_refs=["selected Finder item"],
            resolved_targets=[],
            success_criteria=["Source PDF exists", "Source is removed", "Destination exists"],
            required_context=["finder_selection", "filesystem_default_path"],
            required_capabilities=_capabilities(*actions) + ["macos.finder_selection"],
            required_actions=actions,
            risk_level=_risk(*actions),
            approval_needed=False,
            missing_information=[],
            execution_plan=[
                "Read the selected Finder item.",
                "Verify the selected item is a PDF.",
                "Move it to Downloads inside the filesystem sandbox.",
                "Record the operation and undo metadata.",
            ],
            verification_plan=["Verify source is removed.", "Verify destination exists."],
            rollback_plan=["Move the file back to its original path if verification succeeds."],
        )

    remove_ambiguous = re.fullmatch(r"(?:remove|delete|trash)\s+(?:that|this|it|the)\s+file\.?", lowered)
    if remove_ambiguous:
        return TaskSpec(
            user_goal=goal,
            task_type="file.trash",
            target_refs=["that file"],
            resolved_targets=[],
            success_criteria=["The intended file is moved to Trash, not permanently deleted."],
            required_context=["recent_operations", "finder_selection", "screen_text"],
            required_capabilities=_capabilities("file.trash"),
            required_actions=["file.trash"],
            risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
            approval_needed=True,
            missing_information=["Which file should be moved to Trash?"],
            execution_plan=["Ask the user to identify the file before changing anything."],
            verification_plan=["After a file is resolved, verify it is moved to Trash."],
            rollback_plan=["Restore from Trash when the Trash path is known."],
        )

    click_match = re.fullmatch(r"click\s+(?:the\s+)?(?P<label>.+?)(?:\s+button)?\.?", goal, re.I)
    if click_match:
        target = click_match.group("label").strip()
        if not target.lower().endswith("button"):
            target = f"{target} button"
        actions = ["observation.accessibility_tree", "observation.ocr", "ui.click"]
        return TaskSpec(
            user_goal=goal,
            task_type="ui.click",
            target_refs=[target],
            resolved_targets=[],
            success_criteria=["The intended UI element is activated once."],
            required_context=["accessibility_tree", "screen_text", "active_window"],
            required_capabilities=_capabilities(*actions),
            required_actions=actions,
            risk_level=_risk(*actions),
            approval_needed=False,
            missing_information=[],
            execution_plan=[
                "Read the accessibility tree.",
                "Use OCR or screen vision if the target is not in accessibility.",
                "Click only when the target is resolved with high confidence.",
            ],
            verification_plan=["Observe UI after click", "Report if the target could not be verified."],
            rollback_plan=["No generic undo; recover based on the observed post-click state."],
        )

    type_match = re.fullmatch(r"type\s+this\s+into\s+(?:the\s+)?(?P<target>.+?):\s*(?P<text>.+)", goal, re.I)
    if type_match:
        actions = ["observation.accessibility_tree", "ui.type_text"]
        return TaskSpec(
            user_goal=goal,
            task_type="ui.type_text",
            target_refs=[type_match.group("target").strip()],
            resolved_targets=[],
            success_criteria=["Target field contains the requested text.", "The form is not submitted."],
            required_context=["accessibility_tree", "active_window"],
            required_capabilities=_capabilities(*actions),
            required_actions=actions,
            risk_level=_risk(*actions),
            approval_needed=False,
            missing_information=[],
            execution_plan=["Resolve the target field.", "Focus it.", "Type the requested literal text."],
            verification_plan=["Verify visible or accessible field value matches the requested text."],
            rollback_plan=["Clear or restore the field value if the same field remains focused."],
        )

    if re.search(r"\brun\b.*\btests\b", lowered) and re.search(r"\bfix\b.*\bfail", lowered):
        actions = ["coding.fix_tests"]
        return TaskSpec(
            user_goal=goal,
            task_type="coding.fix_tests",
            target_refs=["this project"],
            resolved_targets=[str(context.get("cwd", ""))] if context.get("cwd") else [],
            success_criteria=["Tests were run.", "Failures were fixed or clearly reported.", "Diff was summarized."],
            required_context=["repo.cwd", "git_status", "test_commands"],
            required_capabilities=_capabilities(*actions),
            required_actions=["coding.inspect_repo", "coding.run_tests", "coding.edit_code", "coding.rerun_tests"],
            risk_level=_risk(*actions),
            approval_needed=True,
            missing_information=[],
            execution_plan=[
                "Inspect the repo and test commands.",
                "Run tests.",
                "Fix failures with scoped edits.",
                "Rerun tests and summarize the diff.",
            ],
            verification_plan=["Tests pass or remaining failures are reported with evidence."],
            rollback_plan=["Do not commit or push without explicit approval."],
        )

    return TaskSpec(
        user_goal=goal,
        task_type="unknown",
        target_refs=[],
        resolved_targets=[],
        success_criteria=[],
        required_context=["current_context"],
        required_capabilities=[],
        required_actions=[],
        risk_level=RiskLevel.READ_ONLY,
        approval_needed=False,
        missing_information=["No deterministic plan matched this request."],
        execution_plan=["Use model reasoning with local context and safety rules."],
        verification_plan=["Verify against the user's stated goal before reporting completion."],
        rollback_plan=[],
    )
