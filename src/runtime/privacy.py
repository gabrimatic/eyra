"""Local-first privacy boundary decisions for model, network, and voice paths."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from utils.settings import Settings

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}


@dataclass(frozen=True)
class PrivacyBoundaryDecision:
    """Decision for whether a specific action sends data off-device."""

    action: str
    data_classes: list[str]
    leaves_machine: bool
    allowed: bool
    destination: str
    requires_user_opt_in: bool
    explanation: str


def _is_local_base_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"", "http", "https"} and host in _LOCAL_HOSTS


def evaluate_privacy_boundary(
    settings: Settings,
    *,
    action: str,
    data_classes: list[str] | tuple[str, ...],
) -> PrivacyBoundaryDecision:
    """Evaluate the privacy boundary for one concrete runtime action."""
    data = list(data_classes)
    if action.startswith("network."):
        return PrivacyBoundaryDecision(
            action=action,
            data_classes=data,
            leaves_machine=True,
            allowed=settings.NETWORK_TOOLS_ENABLED,
            destination="network_tool",
            requires_user_opt_in=True,
            explanation=(
                "Network tools send the requested query or URL to the remote site. "
                "Enable NETWORK_TOOLS_ENABLED=true before using this path."
            )
            if not settings.NETWORK_TOOLS_ENABLED
            else "Network tools are enabled, so the requested query or URL may leave the machine.",
        )

    if action.startswith("realtime."):
        return PrivacyBoundaryDecision(
            action=action,
            data_classes=data,
            leaves_machine=True,
            allowed=settings.REALTIME_VOICE_ENABLED,
            destination="openai_realtime",
            requires_user_opt_in=True,
            explanation=(
                "Realtime voice is an online opt-in path. Browser audio, text, and allowed tool results may be sent "
                "to OpenAI Realtime only when REALTIME_VOICE_ENABLED=true."
            ),
        )

    if action.startswith("model."):
        provider_local = _is_local_base_url(settings.API_BASE_URL)
        return PrivacyBoundaryDecision(
            action=action,
            data_classes=data,
            leaves_machine=not provider_local,
            allowed=True,
            destination="local_model_provider" if provider_local else "model_provider",
            requires_user_opt_in=not provider_local,
            explanation=(
                "The configured model provider is local, so prompts and tool context stay on this machine."
                if provider_local
                else "The configured API_BASE_URL is a remote model provider, so prompts, tool results, and listed "
                "local data classes may leave the machine because that provider was explicitly configured."
            ),
        )

    return PrivacyBoundaryDecision(
        action=action,
        data_classes=data,
        leaves_machine=False,
        allowed=True,
        destination="local_runtime",
        requires_user_opt_in=False,
        explanation="This action is handled by local runtime code and does not leave the machine by itself.",
    )


def privacy_decision_dict(decision: PrivacyBoundaryDecision) -> dict:
    """Return a JSON-serializable privacy decision."""
    return {
        "action": decision.action,
        "dataClasses": decision.data_classes,
        "leavesMachine": decision.leaves_machine,
        "allowed": decision.allowed,
        "destination": decision.destination,
        "requiresUserOptIn": decision.requires_user_opt_in,
        "explanation": decision.explanation,
    }
