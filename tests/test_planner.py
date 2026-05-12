"""Tests for Eyra's local computer-action planner."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.actions import get_action_schema
from runtime.jobs import RiskLevel
from runtime.planner import plan_task


def test_action_schema_describes_ui_click_risk_capabilities_and_verification():
    schema = get_action_schema("ui.click")

    assert schema.action_type == "ui.click"
    assert schema.risk_level == RiskLevel.MEDIUM_RISK_CHANGE
    assert "accessibility.read" in schema.required_capabilities
    assert "ui.coordinate_control" in schema.required_capabilities
    assert "observe_after_action" in schema.verification_behavior
    assert schema.cancellable is True


def test_action_schema_describes_window_action_approval_and_verification():
    schema = get_action_schema("app_window.window_action")

    assert schema.risk_level == RiskLevel.MEDIUM_RISK_CHANGE
    assert schema.approval_required is True
    assert "app.window_control" in schema.required_capabilities
    assert "window_state_changed_or_permission_error" in schema.verification_behavior


def test_plans_selected_pdf_move_with_context_and_undo():
    spec = plan_task("Move the selected PDF to Downloads.")

    assert spec.task_type == "file.move"
    assert spec.target_refs == ["selected Finder item"]
    assert "finder_selection" in spec.required_context
    assert "filesystem.move" in spec.required_capabilities
    assert spec.risk_level == RiskLevel.LOW_RISK_CHANGE
    assert spec.approval_needed is False
    assert "destination exists" in " ".join(spec.verification_plan)
    assert spec.rollback_plan == ["Move the file back to its original path if verification succeeds."]


def test_plans_click_ok_from_screen_grounding_layers():
    spec = plan_task("Click the OK button.")

    assert spec.task_type == "ui.click"
    assert spec.target_refs == ["OK button"]
    assert spec.required_context[:2] == ["accessibility_tree", "screen_text"]
    assert "ui.click" in spec.required_actions
    assert spec.risk_level == RiskLevel.MEDIUM_RISK_CHANGE
    assert spec.approval_needed is False
    assert "Observe UI after click" in spec.verification_plan


def test_planner_refuses_to_guess_ambiguous_file_reference():
    spec = plan_task("Remove that file.")

    assert spec.task_type == "file.trash"
    assert spec.target_refs == ["that file"]
    assert spec.approval_needed is True
    assert spec.missing_information == ["Which file should be moved to Trash?"]
    assert spec.execution_plan == ["Ask the user to identify the file before changing anything."]


def test_plans_coding_job_with_approval_boundaries():
    spec = plan_task("Run the tests in this project and fix what fails.")

    assert spec.task_type == "coding.fix_tests"
    assert "repo.cwd" in spec.required_context
    assert "coding.run_tests" in spec.required_actions
    assert "coding.edit_code" in spec.required_actions
    assert spec.risk_level == RiskLevel.MEDIUM_RISK_CHANGE
    assert spec.approval_needed is True
    assert "Do not commit or push without explicit approval." in spec.rollback_plan
