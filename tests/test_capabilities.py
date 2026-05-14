"""Tests for runtime capability and privacy boundary reporting."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.capabilities import build_capability_snapshot, format_capability_answer
from runtime.models import LiveRuntimeState, PreflightResult
from runtime.privacy import evaluate_privacy_boundary
from utils.settings import Settings


def test_default_capability_snapshot_is_local_first():
    settings = Settings()
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=[settings.MODEL],
        wh_available=True,
        wh_bin="/usr/local/bin/wh",
        screen_capture_available=True,
        tool_capable_models=[settings.MODEL],
        tool_capability_checked_models=[settings.MODEL],
        vision_capable_models=[settings.MODEL],
        vision_capability_checked_models=[settings.MODEL],
    )
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)

    snapshot = build_capability_snapshot(settings, preflight=preflight, state=state)

    assert snapshot["localFirst"] is True
    assert snapshot["models"]["providerLocal"] is True
    assert snapshot["voice"]["handsFreeMode"] is False
    assert snapshot["voice"]["localWhisper"]["ready"] is True
    assert snapshot["screen"]["captureReady"] is True
    assert snapshot["tools"]["filesystem"]["enabled"] is True
    assert snapshot["tools"]["filesystem"]["permanentDeleteRequiresApproval"] is True
    assert snapshot["tools"]["network"]["enabled"] is False
    assert snapshot["tools"]["os"]["enabled"] is False
    assert snapshot["tools"]["agents"]["enabled"] is False
    assert "codex" in snapshot["tools"]["agents"]["external"]["agents"]
    assert snapshot["privacy"]["leavesMachineByDefault"] is False
    assert snapshot["privacy"]["remotePaths"] == []
    assert snapshot["privacy"]["boundaries"][0]["action"] == "model.screen_summary"
    assert snapshot["privacy"]["boundaries"][0]["leavesMachine"] is False


def test_remote_and_realtime_paths_are_named_explicitly():
    settings = Settings(
        API_BASE_URL="https://api.example.com/v1",
        NETWORK_TOOLS_ENABLED=True,
        REALTIME_VOICE_ENABLED=True,
        REALTIME_TOOLS_ENABLED=True,
    )

    snapshot = build_capability_snapshot(settings)

    assert snapshot["models"]["providerLocal"] is False
    assert snapshot["privacy"]["leavesMachineByDefault"] is True
    assert "model_provider" in snapshot["privacy"]["remotePaths"]
    assert "network_tools" in snapshot["privacy"]["remotePaths"]
    assert "realtime_voice" in snapshot["privacy"]["remotePaths"]
    assert "realtime_tools" in snapshot["privacy"]["remotePaths"]


def test_capability_answer_includes_control_and_privacy_language():
    text = format_capability_answer(
        build_capability_snapshot(
            Settings(OS_TOOLS_ENABLED=True, NETWORK_TOOLS_ENABLED=False, REALTIME_VOICE_ENABLED=False)
        )
    )

    assert "Local-first default: yes" in text
    assert "Filesystem: on" in text
    assert "Permanent delete: approval required" in text
    assert "OS tools: on" in text
    assert "Network tools: off" in text
    assert "Leaves machine by default: no" in text


def test_privacy_boundary_keeps_local_model_data_on_machine():
    decision = evaluate_privacy_boundary(
        Settings(),
        action="model.screen_summary",
        data_classes=["prompt", "screenshot"],
    )

    assert decision.leaves_machine is False
    assert decision.allowed is True
    assert decision.destination == "local_model_provider"
    assert decision.requires_user_opt_in is False


def test_privacy_boundary_names_remote_model_screenshot_data():
    decision = evaluate_privacy_boundary(
        Settings(API_BASE_URL="https://api.example.com/v1"),
        action="model.screen_summary",
        data_classes=["prompt", "screenshot"],
    )

    assert decision.leaves_machine is True
    assert decision.allowed is True
    assert decision.destination == "model_provider"
    assert decision.data_classes == ["prompt", "screenshot"]
    assert "remote model provider" in decision.explanation


def test_privacy_boundary_blocks_network_tool_when_disabled():
    decision = evaluate_privacy_boundary(
        Settings(NETWORK_TOOLS_ENABLED=False),
        action="network.web_search",
        data_classes=["search_query"],
    )

    assert decision.allowed is False
    assert decision.leaves_machine is True
    assert decision.destination == "network_tool"
    assert "NETWORK_TOOLS_ENABLED=true" in decision.explanation


def test_privacy_boundary_marks_realtime_audio_as_online_opt_in():
    decision = evaluate_privacy_boundary(
        Settings(REALTIME_VOICE_ENABLED=True),
        action="realtime.voice_turn",
        data_classes=["microphone_audio", "transcript"],
    )

    assert decision.allowed is True
    assert decision.leaves_machine is True
    assert decision.destination == "openai_realtime"
    assert decision.requires_user_opt_in is True
