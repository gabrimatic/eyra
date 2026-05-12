"""Typed local computer-action schemas for Eyra's operator runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from runtime.jobs import RiskLevel


class ActionCategory(str, Enum):
    """High-level groups for local computer operations."""

    OBSERVATION = "observation"
    FILE = "file"
    APP_WINDOW = "app_window"
    UI = "ui"
    BROWSER = "browser"
    SYSTEM = "system"
    CODING = "coding"
    TRIGGER = "trigger"


@dataclass(frozen=True)
class ActionSchema:
    """Capability, risk, approval, verification, and undo metadata for one action."""

    action_type: str
    category: ActionCategory
    risk_level: RiskLevel
    required_capabilities: tuple[str, ...]
    approval_required: bool
    timeout_seconds: int
    cancellable: bool
    verification_behavior: tuple[str, ...]
    undo_behavior: str


_ACTION_SCHEMAS: dict[str, ActionSchema] = {
    "observation.screen": ActionSchema(
        action_type="observation.screen",
        category=ActionCategory.OBSERVATION,
        risk_level=RiskLevel.READ_ONLY,
        required_capabilities=("screen.capture",),
        approval_required=False,
        timeout_seconds=15,
        cancellable=True,
        verification_behavior=("capture_returned_image",),
        undo_behavior="No undo needed for read-only observation.",
    ),
    "observation.ocr": ActionSchema(
        action_type="observation.ocr",
        category=ActionCategory.OBSERVATION,
        risk_level=RiskLevel.READ_ONLY,
        required_capabilities=("screen.capture", "screen.ocr"),
        approval_required=False,
        timeout_seconds=20,
        cancellable=True,
        verification_behavior=("ocr_returned_text_or_clear_empty_result",),
        undo_behavior="No undo needed for read-only observation.",
    ),
    "observation.accessibility_tree": ActionSchema(
        action_type="observation.accessibility_tree",
        category=ActionCategory.OBSERVATION,
        risk_level=RiskLevel.READ_ONLY,
        required_capabilities=("accessibility.read",),
        approval_required=False,
        timeout_seconds=10,
        cancellable=True,
        verification_behavior=("accessibility_snapshot_returned_or_permission_error",),
        undo_behavior="No undo needed for read-only observation.",
    ),
    "file.move": ActionSchema(
        action_type="file.move",
        category=ActionCategory.FILE,
        risk_level=RiskLevel.LOW_RISK_CHANGE,
        required_capabilities=("filesystem.read", "filesystem.move"),
        approval_required=False,
        timeout_seconds=30,
        cancellable=True,
        verification_behavior=("source_removed", "destination_exists"),
        undo_behavior="Move the file back to its original path.",
    ),
    "file.trash": ActionSchema(
        action_type="file.trash",
        category=ActionCategory.FILE,
        risk_level=RiskLevel.LOW_RISK_CHANGE,
        required_capabilities=("filesystem.trash",),
        approval_required=False,
        timeout_seconds=30,
        cancellable=True,
        verification_behavior=("source_missing", "trash_item_exists"),
        undo_behavior="Restore the item from Trash when the Trash path is known.",
    ),
    "ui.click": ActionSchema(
        action_type="ui.click",
        category=ActionCategory.UI,
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        required_capabilities=("accessibility.read", "screen.ocr", "ui.coordinate_control"),
        approval_required=False,
        timeout_seconds=10,
        cancellable=True,
        verification_behavior=("observe_after_action", "target_state_changed_or_still_safe"),
        undo_behavior="No generic undo; recover by observing state and applying the opposite UI action when safe.",
    ),
    "ui.type_text": ActionSchema(
        action_type="ui.type_text",
        category=ActionCategory.UI,
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        required_capabilities=("accessibility.read", "ui.text_input"),
        approval_required=False,
        timeout_seconds=20,
        cancellable=True,
        verification_behavior=("focused_field_contains_expected_text",),
        undo_behavior="Select and remove inserted text when the target field is still focused and safe.",
    ),
    "ui.scroll": ActionSchema(
        action_type="ui.scroll",
        category=ActionCategory.UI,
        risk_level=RiskLevel.LOW_RISK_CHANGE,
        required_capabilities=("ui.coordinate_control",),
        approval_required=True,
        timeout_seconds=10,
        cancellable=True,
        verification_behavior=("observe_after_action",),
        undo_behavior="Scroll in the opposite direction when the target view is unchanged and safe.",
    ),
    "ui.drag": ActionSchema(
        action_type="ui.drag",
        category=ActionCategory.UI,
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        required_capabilities=("ui.coordinate_control",),
        approval_required=True,
        timeout_seconds=10,
        cancellable=True,
        verification_behavior=("observe_after_action",),
        undo_behavior="No generic undo; recover based on the observed post-drag state.",
    ),
    "app_window.window_action": ActionSchema(
        action_type="app_window.window_action",
        category=ActionCategory.APP_WINDOW,
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        required_capabilities=("accessibility.read", "app.window_control"),
        approval_required=True,
        timeout_seconds=10,
        cancellable=True,
        verification_behavior=("window_state_changed_or_permission_error",),
        undo_behavior="Some window actions can be reversed with the opposite window action when the same window remains available.",
    ),
    "coding.fix_tests": ActionSchema(
        action_type="coding.fix_tests",
        category=ActionCategory.CODING,
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        required_capabilities=("filesystem.read", "filesystem.write", "coding.run_tests"),
        approval_required=True,
        timeout_seconds=300,
        cancellable=True,
        verification_behavior=("tests_rerun", "diff_summarized"),
        undo_behavior="Keep diffs reviewable; commit, push, and destructive git operations require explicit approval.",
    ),
}


def get_action_schema(action_type: str) -> ActionSchema:
    """Return the schema for a known action type."""
    try:
        return _ACTION_SCHEMAS[action_type]
    except KeyError:
        raise ValueError(f"Unknown action type: {action_type}") from None


def action_schema_dict(action_type: str) -> dict:
    """Return a JSON-serializable action schema."""
    schema = get_action_schema(action_type)
    return {
        "action_type": schema.action_type,
        "category": schema.category.value,
        "risk_level": schema.risk_level.value,
        "required_capabilities": list(schema.required_capabilities),
        "approval_required": schema.approval_required,
        "timeout_seconds": schema.timeout_seconds,
        "cancellable": schema.cancellable,
        "verification_behavior": list(schema.verification_behavior),
        "undo_behavior": schema.undo_behavior,
    }
