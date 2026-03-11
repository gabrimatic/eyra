# message_handler.py

"""
Message Handler

Routes requests through the complexity scorer, selects the appropriate model tier,
and streams responses. Images are provided via tools, not captured here.
Complex-tier requests with an active tool registry use the tool-calling pipeline;
all other requests use plain text streaming.
"""

import logging
from typing import AsyncGenerator

from chat.complexity_scorer import ComplexityLevel, ComplexityScorer
from chat.session_state import InteractionStyle, QualityMode
from clients.ai_client import AIClient
from clients.base_client import BaseAIClient
from tools.registry import ToolRegistry
from utils.image_history import manage_message_history
from utils.mock_client import MockAIClient
from utils.settings import Settings

logger = logging.getLogger(__name__)

# Response shaping system prompts by interaction style
_STYLE_PROMPTS = {
    InteractionStyle.TEXT: (
        "You are Eyra, a helpful live assistant. "
        "You have tools available to interact with the system — use them instead of imagining results. "
        "Respond naturally and concisely."
    ),
    InteractionStyle.VOICE: (
        "Your response will be spoken aloud. Keep it concise and natural. "
        "No markdown, no bullet points, no code blocks. "
        "Two to three sentences max. Speak as if talking to the user directly. "
        "You have tools — use them when needed."
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



def _apply_style_prompt(
    context: list[dict], style: InteractionStyle
) -> list[dict]:
    """Prepend a system prompt for the interaction style if needed."""
    prompt = _STYLE_PROMPTS.get(style)
    if not prompt:
        return context
    return [{"role": "system", "content": prompt}] + context


async def process_task_stream(
    text_content: str,
    complexity_scorer: ComplexityScorer,
    settings: Settings,
    messages: list[dict] | None = None,
    quality_mode: QualityMode = QualityMode.BALANCED,
    interaction_style: InteractionStyle = InteractionStyle.TEXT,
    tool_registry: ToolRegistry | None = None,
) -> AsyncGenerator[str, None]:
    """
    Score complexity, select a model, and stream the response.

    Tools are enabled only when the request scores Complex or quality_mode is BEST.
    Simple and Moderate requests use plain text streaming regardless of the model selected.
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
        if settings.COMPLEXITY_ROUTING_ENABLED:
            response = await complexity_scorer.score_complexity(text_content, messages=messages)
            logger.debug("Complexity: %s (%.2f)", response.classification, response.confidence)
            model_name = select_model(response.classification, settings, quality_mode)
            is_complex = (
                quality_mode == QualityMode.BEST
                or response.classification == ComplexityLevel.COMPLEX
            )
        else:
            model_name = settings.MODEL
            is_complex = True
            logger.debug("Complexity routing disabled, using: %s", model_name)

        logger.debug("Model: %s", model_name)

        client = get_ai_client(model_name, settings)
        context = _apply_style_prompt(manage_message_history(messages), interaction_style)

        if tool_registry:
            async for chunk in client.stream_with_tools(
                context, model_name=model_name, tools=tool_registry, include_costly=is_complex,
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
