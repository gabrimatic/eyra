"""Local model capability registry for routing decisions."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlparse

from chat.complexity_scorer import ComplexityLevel
from chat.session_state import QualityMode
from runtime.models import PreflightResult
from runtime.routing.types import Capability, CapabilityState, ModelCapabilities
from utils.settings import Settings

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}


def provider_is_local(api_base_url: str) -> bool:
    """Return True when the configured model endpoint is local."""
    parsed = urlparse(api_base_url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"", "http", "https"} and host in _LOCAL_HOSTS


def worker_model_settings(settings: Settings) -> Settings:
    """Return settings for a generic worker call with WORKER_MODEL pinned across tiers."""
    worker_model = settings.WORKER_MODEL.strip()
    if not worker_model:
        return settings
    return replace(
        settings,
        MODEL=worker_model,
        SIMPLE_MODEL=worker_model,
        MODERATE_MODEL=worker_model,
    )


class ModelRegistry:
    """Model selection over configured models and preflight facts."""

    def __init__(self, settings: Settings, preflight: PreflightResult):
        self.settings = settings
        self.preflight = preflight
        self.provider_local = provider_is_local(settings.API_BASE_URL)

    def capabilities(self, name: str, *, source: str = "configured") -> ModelCapabilities:
        ready = set(self.preflight.models_ready)
        missing = set(self.preflight.models_missing)
        available = bool(name) and name not in missing and (not ready or name in ready)
        return ModelCapabilities(
            name=name,
            provider_local=self.provider_local,
            available=available,
            supports_text=CapabilityState.ASSUMED_TRUE if available else CapabilityState.VERIFIED_FALSE,
            supports_tools=self._capability_state(
                name,
                checked=self.preflight.tool_capability_checked_models,
                capable=self.preflight.tool_capable_models,
            ),
            supports_vision=self._capability_state(
                name,
                checked=self.preflight.vision_capability_checked_models,
                capable=self.preflight.vision_capable_models,
            ),
            source=source,
        )

    def select_model(
        self,
        *,
        quality_mode: QualityMode,
        effort_level: ComplexityLevel,
        required_capabilities: frozenset[Capability],
        is_worker: bool = False,
    ) -> tuple[str | None, str]:
        """Choose the smallest configured model that satisfies the route."""
        candidates = self._candidates(
            quality_mode=quality_mode,
            effort_level=effort_level,
            is_worker=is_worker,
            required_capabilities=required_capabilities,
        )
        for name, reason in candidates:
            caps = self.capabilities(name, source=reason)
            if self._satisfies(caps, required_capabilities):
                boundary = "remote provider" if not caps.provider_local else "local provider"
                return name, f"{reason}; satisfies required capabilities via {boundary}"
        return None, "No configured model satisfies required capabilities."

    def _candidates(
        self,
        *,
        quality_mode: QualityMode,
        effort_level: ComplexityLevel,
        is_worker: bool,
        required_capabilities: frozenset[Capability],
    ) -> list[tuple[str, str]]:
        names: list[tuple[str, str]] = []
        worker = self.settings.WORKER_MODEL.strip()
        if is_worker and worker:
            names.append((worker, "WORKER_MODEL preferred for worker task"))
        if quality_mode == QualityMode.FAST:
            names.extend([
                (self.settings.SIMPLE_MODEL, "FAST mode SIMPLE_MODEL"),
                (self.settings.MODERATE_MODEL, "FAST fallback MODERATE_MODEL"),
                (self.settings.MODEL, "FAST fallback MODEL"),
            ])
        elif quality_mode == QualityMode.BEST:
            names.extend([
                (self.settings.MODEL, "BEST mode MODEL"),
                (self.settings.VISION_MODEL or self.settings.MODEL, "BEST vision fallback"),
                (self.settings.MODERATE_MODEL, "BEST fallback MODERATE_MODEL"),
                (self.settings.SIMPLE_MODEL, "BEST fallback SIMPLE_MODEL"),
            ])
        elif effort_level == ComplexityLevel.SIMPLE:
            names.extend([
                (self.settings.SIMPLE_MODEL, "Balanced simple effort SIMPLE_MODEL"),
                (self.settings.MODERATE_MODEL, "Balanced simple fallback MODERATE_MODEL"),
                (self.settings.MODEL, "Balanced simple fallback MODEL"),
            ])
        elif effort_level == ComplexityLevel.MODERATE:
            names.extend([
                (self.settings.MODERATE_MODEL, "Balanced moderate effort MODERATE_MODEL"),
                (self.settings.MODEL, "Balanced moderate fallback MODEL"),
                (self.settings.SIMPLE_MODEL, "Balanced moderate fallback SIMPLE_MODEL"),
            ])
        else:
            names.extend([
                (self.settings.MODEL, "Balanced complex effort MODEL"),
                (self.settings.MODERATE_MODEL, "Balanced complex fallback MODERATE_MODEL"),
                (self.settings.SIMPLE_MODEL, "Balanced complex fallback SIMPLE_MODEL"),
            ])
        if Capability.VISION in required_capabilities:
            names.insert(0, (self.settings.VISION_MODEL or self.settings.MODEL, "Vision route model"))
        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, reason in names:
            if name and name not in seen:
                deduped.append((name, reason))
                seen.add(name)
        return deduped

    def _satisfies(self, caps: ModelCapabilities, required: frozenset[Capability]) -> bool:
        if not caps.available or caps.supports_text == CapabilityState.VERIFIED_FALSE:
            return False
        if Capability.NATIVE_TOOLS in required and caps.supports_tools == CapabilityState.VERIFIED_FALSE:
            return False
        if Capability.VISION in required and caps.supports_vision == CapabilityState.VERIFIED_FALSE:
            return False
        return True

    @staticmethod
    def _capability_state(name: str, *, checked: list[str], capable: list[str]) -> CapabilityState:
        if not name:
            return CapabilityState.UNKNOWN
        checked_set = set(checked)
        if name not in checked_set:
            return CapabilityState.UNKNOWN
        return CapabilityState.VERIFIED_TRUE if name in set(capable) else CapabilityState.VERIFIED_FALSE
