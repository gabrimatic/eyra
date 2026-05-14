"""Types for Eyra's local-first routing policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from chat.complexity_scorer import ComplexityLevel
from chat.session_state import InteractionStyle, QualityMode
from runtime.models import PreflightResult
from utils.settings import Settings


class RequestSource(str, Enum):
    TERMINAL = "terminal"
    WEB = "web"
    LOCAL_VOICE = "local_voice"
    REALTIME_VOICE = "realtime_voice"
    WORKER = "worker"
    TEST = "test"


class ExecutionClass(str, Enum):
    LOCAL_COMMAND = "local_command"
    DIRECT_ACTION = "direct_action"
    TEXT_CHAT = "text_chat"
    TOOL_ASSISTED_CHAT = "tool_assisted_chat"
    BACKGROUND_TASK = "background_task"
    SCREEN_ANALYSIS = "screen_analysis"
    PDF_ANALYSIS = "pdf_analysis"
    FILESYSTEM_ACTION = "filesystem_action"
    BROWSER_TASK = "browser_task"
    CODING_AGENT_TASK = "coding_agent_task"
    REALTIME_VOICE_TURN = "realtime_voice_turn"


class Capability(str, Enum):
    TEXT = "text"
    NATIVE_TOOLS = "native_tools"
    VISION = "vision"
    SCREEN_CAPTURE = "screen_capture"
    PDF_READ = "pdf_read"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    CLIPBOARD_READ = "clipboard_read"
    NETWORK = "network"
    BROWSER_CONTROL = "browser_control"
    OS_AUTOMATION = "os_automation"
    SHELL = "shell"
    MCP = "mcp"
    AGENT_READ = "agent_read"
    AGENT_DELEGATION = "agent_delegation"
    LONG_CONTEXT = "long_context"
    STRUCTURED_OUTPUT = "structured_output"
    VOICE_RESPONSE = "voice_response"


class RiskTier(str, Enum):
    NONE = "none"
    LOW_READ_ONLY = "low_read_only"
    PRIVATE_READ = "private_read"
    LOCAL_WRITE = "local_write"
    DESTRUCTIVE = "destructive"
    NETWORKED = "networked"
    REMOTE_DISCLOSURE = "remote_disclosure"
    OS_CONTROL = "os_control"
    SHELL_EXECUTION = "shell_execution"
    DELEGATED_AGENT = "delegated_agent"


class CapabilityState(str, Enum):
    VERIFIED_TRUE = "verified_true"
    VERIFIED_FALSE = "verified_false"
    ASSUMED_TRUE = "assumed_true"
    UNKNOWN = "unknown"
    RUNTIME_FAILED = "runtime_failed"


@dataclass(frozen=True)
class RequestEnvelope:
    text: str
    source: RequestSource
    interaction_style: InteractionStyle
    quality_mode: QualityMode
    messages: list[dict]
    current_goal: str | None
    is_worker: bool
    settings: Settings
    preflight: PreflightResult


@dataclass(frozen=True)
class EffortEstimate:
    level: ComplexityLevel
    heuristic_confidence: float
    confidence_kind: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelCapabilities:
    name: str
    provider_local: bool
    available: bool
    supports_text: CapabilityState
    supports_tools: CapabilityState
    supports_vision: CapabilityState
    source: str


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    capabilities: frozenset[Capability]
    risk_tier: RiskTier
    latency_cost: str
    reads_private_data: bool = False
    mutates_state: bool = False
    destructive: bool = False
    network_access: bool = False
    requires_approval: bool = False
    allowed_execution_classes: frozenset[ExecutionClass] = frozenset()


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed_tool_names: frozenset[str]
    denied_tool_reasons: dict[str, str]


@dataclass(frozen=True)
class FallbackPlan:
    on_model_missing: str
    on_tools_unsupported: str
    on_capability_missing: str


@dataclass(frozen=True)
class RoutingTrace:
    source: RequestSource
    quality_mode: QualityMode
    execution_class: ExecutionClass
    effort_level: ComplexityLevel
    selected_model: str | None
    selected_model_reason: str
    required_capabilities: frozenset[Capability]
    allowed_tools: tuple[str, ...]
    denied_tools: dict[str, str]
    risk_tier: RiskTier
    privacy_summary: str
    fallback_plan: FallbackPlan


@dataclass(frozen=True)
class RoutingDecision:
    execution_class: ExecutionClass
    effort: EffortEstimate
    selected_model: str | None
    selected_model_reason: str
    required_capabilities: frozenset[Capability]
    risk_tier: RiskTier
    tool_policy: ToolPolicyDecision
    require_tools: bool
    fallback_plan: FallbackPlan
    trace: RoutingTrace
