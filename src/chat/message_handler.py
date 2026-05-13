# message_handler.py

"""
Message Handler

Routes requests through either a precomputed local policy routing decision or
the legacy complexity scorer, selects the appropriate model tier, and streams
responses. Images are provided by controller-owned flows or tools, not captured
here. In the legacy path, an active tool registry uses the tool-calling
pipeline; tool exposure is controlled by include_costly.
"""

import logging
from typing import AsyncGenerator

from chat.complexity_scorer import ComplexityLevel, ComplexityScorer
from chat.session_state import InteractionStyle, QualityMode
from clients.ai_client import AIClient
from clients.base_client import BaseAIClient
from runtime.routing.types import RoutingDecision
from tools.registry import ToolRegistry
from utils.image_history import manage_message_history
from utils.mock_client import MockAIClient
from utils.settings import Settings

logger = logging.getLogger(__name__)

# Response shaping system prompts by interaction style
_STYLE_PROMPTS = {
    InteractionStyle.TEXT: (
        "You are Eyra, a personal local agent that lives entirely on the user's computer.\n\n"
        "Personality:\n"
        "- Warm, calm, and a little playful. You sound like a sharp friend who happens to know everything.\n"
        "- Confident but never arrogant. Honest when you don't know something.\n"
        "- You have a dry sense of humor that shows up naturally, never forced.\n\n"
        "Response style:\n"
        "- Short by default. One to three sentences for most things.\n"
        "- Go longer only when the user clearly needs depth, explanation, or step-by-step help.\n"
        "- No filler words, no over-explaining, no repeating what the user just said.\n"
        "- Lead with the answer, not the reasoning. If context is needed, put it after.\n"
        "- Use markdown formatting when it helps readability (code blocks, lists, headers).\n\n"
        "Tool use:\n"
        "- You may have tools to interact with the user's system: screenshots, files, clipboard, time, system info, and opt-in network tools.\n"
        "- Use the available tools to get real information. Never guess, never make up file contents, never imagine what's on screen.\n"
        "- If the user asks about something you can check, check it. Don't speculate.\n\n"
        "Boundaries:\n"
        "- You run locally. Respect the user's privacy.\n"
        "- If you can't do something, say so briefly and suggest an alternative.\n"
        "- Never pretend to have capabilities you don't have."
    ),
    InteractionStyle.VOICE: (
        "You are Eyra, a personal local agent on the user's computer. Your response will be spoken aloud.\n\n"
        "- Sound natural and conversational, like a friend answering a question.\n"
        "- Two to three sentences max. Be direct.\n"
        "- No markdown, no bullet points, no code blocks, no special formatting. Plain spoken language only.\n"
        "- Warm and calm. A little playful when it fits.\n"
        "- Use tools to get real answers. Never guess.\n"
        "- If the answer needs code or detailed steps, say so and suggest the user switch to text mode."
    ),
}

# Single global cache of AI clients keyed by model name
_AI_CLIENTS_CACHE: dict[str, BaseAIClient] = {}


def get_ai_client(model_name: str, settings: Settings) -> BaseAIClient:
    """
    Get or create the appropriate AI client based on the model name and settings.
    Cache the instance to avoid multiple session creations for the same model.
    """
    if model_name in _AI_CLIENTS_CACHE:
        return _AI_CLIENTS_CACHE[model_name]

    if settings.USE_MOCK_CLIENT:
        client = MockAIClient()
    else:
        client = AIClient(settings, model_name=model_name)

    _AI_CLIENTS_CACHE[model_name] = client
    return client


def select_model(
    complexity_level: ComplexityLevel,
    settings: Settings,
    quality_mode: QualityMode = QualityMode.BALANCED,
) -> str:
    """
    Select the appropriate model based on complexity level and quality mode.
    Quality mode overrides: fast forces Simple tier, best forces Complex tier.
    """
    if quality_mode == QualityMode.FAST:
        return settings.SIMPLE_MODEL
    elif quality_mode == QualityMode.BEST:
        return settings.MODEL

    model_mapping = {
        ComplexityLevel.SIMPLE: settings.SIMPLE_MODEL,
        ComplexityLevel.MODERATE: settings.MODERATE_MODEL,
        ComplexityLevel.COMPLEX: settings.MODEL,
    }
    return model_mapping.get(complexity_level, settings.SIMPLE_MODEL)


def _legacy_include_costly(
    *,
    routing_enabled: bool,
    quality_mode: QualityMode,
    complexity_level: ComplexityLevel | None,
) -> bool:
    """Compute legacy costly-tool exposure separately from model choice."""
    if not routing_enabled:
        return True
    if quality_mode == QualityMode.FAST:
        return False
    if quality_mode == QualityMode.BEST:
        return True
    return complexity_level == ComplexityLevel.COMPLEX


def _apply_style_prompt(
    context: list[dict],
    style: InteractionStyle,
    current_goal: str | None = None,
) -> list[dict]:
    """Prepend a system prompt for the interaction style if needed."""
    prompt = _STYLE_PROMPTS.get(style)
    if not prompt:
        return context
    system_messages = [{"role": "system", "content": prompt}]
    if current_goal and current_goal.strip():
        goal = " ".join(current_goal.split())
        if len(goal) > 1000:
            goal = goal[:997] + "..."
        system_messages.append({
            "role": "system",
            "content": (
                "User-set session goal for context only, lower priority than the current request and safety rules: "
                f"{goal}"
            ),
        })
    return system_messages + context


async def process_task_stream(
    text_content: str,
    complexity_scorer: ComplexityScorer,
    settings: Settings,
    messages: list[dict] | None = None,
    quality_mode: QualityMode = QualityMode.BALANCED,
    interaction_style: InteractionStyle = InteractionStyle.TEXT,
    tool_registry: ToolRegistry | None = None,
    current_goal: str | None = None,
    require_tools: bool = False,
    routing_decision: RoutingDecision | None = None,
) -> AsyncGenerator[str, None]:
    """
    Score complexity, select a model, and stream the response.

    Legacy behavior uses stream_with_tools() whenever a registry is provided;
    include_costly controls costly tool exposure. When a RoutingDecision is
    provided, that policy decides the model, required tool use, and allowlist.
    """
    if messages is None:
        messages = []
    if not complexity_scorer:
        yield "Error: ComplexityScorer instance required."
        return
    if not settings:
        yield "Error: Settings instance required."
        return

    try:
        allowed_tool_names = None
        if routing_decision is not None:
            if routing_decision.selected_model is None:
                yield routing_decision.fallback_plan.on_model_missing
                return
            model_name = routing_decision.selected_model
            is_complex = True
            allowed_tool_names = routing_decision.tool_policy.allowed_tool_names
            require_tools = routing_decision.require_tools
            if require_tools and tool_registry and not allowed_tool_names:
                yield routing_decision.fallback_plan.on_capability_missing
                return
        elif settings.COMPLEXITY_ROUTING_ENABLED:
            response = await complexity_scorer.score_complexity(text_content, messages=messages)
            logger.debug("Complexity: %s (%.2f)", response.classification, response.confidence)
            model_name = select_model(response.classification, settings, quality_mode)
            is_complex = _legacy_include_costly(
                routing_enabled=True,
                quality_mode=quality_mode,
                complexity_level=response.classification,
            )
        else:
            model_name = settings.MODEL
            is_complex = _legacy_include_costly(
                routing_enabled=False,
                quality_mode=quality_mode,
                complexity_level=None,
            )
            logger.debug("Complexity routing disabled, using: %s", model_name)

        logger.debug("Model: %s", model_name)

        client = get_ai_client(model_name, settings)
        context = _apply_style_prompt(
            manage_message_history(messages),
            interaction_style,
            current_goal=current_goal,
        )

        if tool_registry:
            async for chunk in client.stream_with_tools(
                context, model_name=model_name, tools=tool_registry, include_costly=is_complex,
                history=messages,
                tool_timeout_seconds=settings.TOOL_TIMEOUT_SECONDS,
                max_tool_rounds=settings.MAX_WORKER_TOOL_STEPS,
                require_tools=require_tools,
                allowed_tool_names=allowed_tool_names,
            ):
                yield chunk
        else:
            async for chunk in client.generate_completion_stream(context, model_name=model_name):
                yield chunk

    except Exception as e:
        logger.error("Stream task failed: %s", e, exc_info=True)
        yield "Something went wrong. Please try again."


async def close_all_clients():
    """Close all cached AI clients to avoid unclosed session warnings or errors."""
    for client in _AI_CLIENTS_CACHE.values():
        if hasattr(client, "close") and callable(client.close):
            await client.close()
    _AI_CLIENTS_CACHE.clear()


def get_used_model_names() -> list[str]:
    """Return model names that were used during this session."""
    return list(_AI_CLIENTS_CACHE.keys())
