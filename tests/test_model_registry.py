"""Tests for local routing model selection."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityLevel
from chat.session_state import QualityMode
from runtime.models import PreflightResult
from runtime.routing.model_registry import ModelRegistry, worker_model_settings
from runtime.routing.types import Capability, CapabilityState
from utils.settings import Settings


class TestModelRegistry:
    def test_verified_tool_capable_model_selected_for_tool_route(self):
        settings = Settings(MODEL="main", SIMPLE_MODEL="simple", MODERATE_MODEL="moderate")
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=["simple", "moderate", "main"],
            tool_capability_checked_models=["simple", "moderate", "main"],
            tool_capable_models=["main"],
        )

        model, reason = ModelRegistry(settings, preflight).select_model(
            quality_mode=QualityMode.BALANCED,
            effort_level=ComplexityLevel.SIMPLE,
            required_capabilities=frozenset({Capability.TEXT, Capability.NATIVE_TOOLS}),
        )

        assert model == "main"
        assert "satisfies" in reason

    def test_verified_non_tool_model_not_selected_for_required_tools(self):
        settings = Settings(MODEL="main", SIMPLE_MODEL="simple", MODERATE_MODEL="moderate")
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=["simple", "moderate", "main"],
            tool_capability_checked_models=["simple", "moderate", "main"],
            tool_capable_models=[],
        )

        model, reason = ModelRegistry(settings, preflight).select_model(
            quality_mode=QualityMode.BALANCED,
            effort_level=ComplexityLevel.SIMPLE,
            required_capabilities=frozenset({Capability.TEXT, Capability.NATIVE_TOOLS}),
        )

        assert model is None
        assert "No configured model" in reason

    def test_vision_capable_model_selected_for_screen_route(self):
        settings = Settings(MODEL="main", VISION_MODEL="vision")
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=["main", "vision"],
            vision_capability_checked_models=["main", "vision"],
            vision_capable_models=["vision"],
        )

        model, _ = ModelRegistry(settings, preflight).select_model(
            quality_mode=QualityMode.BEST,
            effort_level=ComplexityLevel.COMPLEX,
            required_capabilities=frozenset({Capability.TEXT, Capability.VISION, Capability.SCREEN_CAPTURE}),
        )

        assert model == "vision"

    def test_missing_model_is_skipped(self):
        settings = Settings(MODEL="main", SIMPLE_MODEL="missing", MODERATE_MODEL="moderate")
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=["moderate", "main"],
            models_missing=["missing"],
        )

        model, _ = ModelRegistry(settings, preflight).select_model(
            quality_mode=QualityMode.BALANCED,
            effort_level=ComplexityLevel.SIMPLE,
            required_capabilities=frozenset({Capability.TEXT}),
        )

        assert model == "moderate"

    def test_unknown_capability_is_not_reported_as_verified(self):
        caps = ModelRegistry(Settings(MODEL="main"), PreflightResult(models_ready=["main"])).capabilities("main")

        assert caps.supports_tools == CapabilityState.UNKNOWN
        assert caps.supports_vision == CapabilityState.UNKNOWN

    def test_worker_model_settings_pins_all_legacy_tiers(self):
        settings = worker_model_settings(Settings(MODEL="main", SIMPLE_MODEL="simple", MODERATE_MODEL="moderate", WORKER_MODEL="worker"))

        assert settings.MODEL == "worker"
        assert settings.SIMPLE_MODEL == "worker"
        assert settings.MODERATE_MODEL == "worker"
