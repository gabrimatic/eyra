# message_handler.py

"""
High-Performance Message Handler

Now refactored to work with in-memory capture & encoding APIs:
- capture_screenshot_and_encode
- capture_selfie_and_encode
No disk I/O is used for images anymore.
"""

import logging
from typing import List, Dict, Optional, AsyncGenerator

# Refactored in-memory capture utilities
from chat.capture import (
    capture_screenshot_and_encode,
    capture_selfie_and_encode,
)

from chat.complexity_scorer import ComplexityScorer, ComplexityLevel
from chat.session_state import QualityMode, InteractionStyle
from clients.base_client import BaseAIClient
from clients.ai_client import AIClient
from utils.mock_client import MockAIClient
from utils.settings import Settings
from utils.image_history import manage_message_history

logger = logging.getLogger(__name__)

# Response shaping system prompts by interaction style
_STYLE_PROMPTS = {
    InteractionStyle.TEXT: (
        "You are Eyra, a helpful live assistant. "
        "Respond naturally and concisely to the user. "
        "Do not describe screens or interfaces unless specifically asked."
    ),
    InteractionStyle.WATCH: (
        "You are observing a screen for the user. Be brief and delta-focused. "
        "Describe only what changed or what is notable. One to two sentences max. "
        "Do not repeat information the user already knows."
    ),
    InteractionStyle.VOICE: (
        "Your response will be spoken aloud. Keep it concise and natural. "
        "No markdown, no bullet points, no code blocks. "
        "Two to three sentences max. Speak as if talking to the user directly."
    ),
}

# Single global cache of AI clients keyed by model name
_AI_CLIENTS_CACHE: Dict[str, BaseAIClient] = {}


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
    task_type: str,
    settings: Settings,
    quality_mode: QualityMode = QualityMode.BALANCED,
) -> str:
    """
    Select the appropriate model based on complexity, task type, and quality mode.
    Quality mode overrides: fast forces Simple tier, best forces Complex tier.
    """
    # Quality mode overrides
    if quality_mode == QualityMode.FAST:
        if task_type == "image":
            return settings.SIMPLE_IMAGE_MODEL
        return settings.SIMPLE_TEXT_MODEL
    elif quality_mode == QualityMode.BEST:
        return settings.COMPLEX_MODEL

    # Normal routing
    if task_type == "image":
        model_mapping = {
            ComplexityLevel.SIMPLE: settings.SIMPLE_IMAGE_MODEL,
            ComplexityLevel.MODERATE: settings.MODERATE_IMAGE_MODEL,
            ComplexityLevel.COMPLEX: settings.COMPLEX_MODEL,
        }
    else:
        model_mapping = {
            ComplexityLevel.SIMPLE: settings.SIMPLE_TEXT_MODEL,
            ComplexityLevel.MODERATE: settings.MODERATE_TEXT_MODEL,
            ComplexityLevel.COMPLEX: settings.COMPLEX_MODEL,
        }

    fallback = settings.SIMPLE_IMAGE_MODEL if task_type == "image" else settings.SIMPLE_TEXT_MODEL
    selected = model_mapping.get(complexity_level, fallback)
    if not selected:
        raise ValueError(f"No model found for complexity level: {complexity_level}")
    return selected


def display_history(messages: List[Dict]) -> None:
    """
    Display the chat history in a readable format.
    """
    print("\nChat History:")
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            text = next(
                (
                    item.get("text", "")
                    for item in content
                    if item.get("type") == "text"
                ),
                "",
            )
            base64_data = next(
                (
                    item.get("image_url", {}).get("url", "")
                    for item in content
                    if item.get("type") == "image_url"
                ),
                "",
            )
            if base64_data:
                base64_preview = (
                    base64_data[:50] + "..." if len(base64_data) > 50 else base64_data
                )
                print(f"{role}: {text} [Image: {base64_preview}]")
            else:
                print(f"{role}: {text}")
        else:
            print(f"{role}: {content}")
    print()


def _apply_style_prompt(
    context: List[Dict], style: InteractionStyle
) -> List[Dict]:
    """Prepend a system prompt for the interaction style if needed."""
    prompt = _STYLE_PROMPTS.get(style)
    if not prompt:
        return context
    return [{"role": "system", "content": prompt}] + context


async def prepare_image(use_selfie: bool = False) -> str:
    """
    Capture an image in memory (screenshot or selfie),
    then return the base64-encoded string.
    Raises ValueError on failure.
    """
    try:
        if use_selfie:
            b64_image = await capture_selfie_and_encode()
        else:
            b64_image = await capture_screenshot_and_encode()
    except Exception as e:
        raise ValueError(f"Failed to capture/encode image: {e}") from e

    if not b64_image:
        raise ValueError("Captured image is empty or failed to encode.")
    return b64_image


async def process_task(
    task_type: str,
    text_content: Optional[str] = None,
    use_selfie: bool = False,
    complexity_scorer: ComplexityScorer = None,
    settings: Settings = None,
    messages: Optional[List[Dict]] = None,
    quality_mode: QualityMode = QualityMode.BALANCED,
    interaction_style: InteractionStyle = InteractionStyle.TEXT,
) -> Dict:
    """
    Process a task with automatic model selection (based on complexity).
    - task_type: 'text' or 'image'
    - text_content: used for text tasks
    - use_selfie: if True, capture from webcam; else screenshot
    - complexity_scorer: needed for complexity classification
    - settings: to select the correct model
    - messages: conversation context

    Returns a full (non-streaming) response in a Dict: {"content": "..."}
    """
    if messages is None:
        messages = []
    if not complexity_scorer:
        return {"content": "Error: ComplexityScorer instance required."}
    if not settings:
        return {"content": "Error: Settings instance required."}

    try:
        logger.debug("Starting task processing: %s", task_type)

        base64_image = ""
        if task_type == "image":
            base64_image = await prepare_image(use_selfie=use_selfie)

        response = await complexity_scorer.score_complexity(
            text_content,
            task_type,
            image_base64=base64_image,
            messages=messages,
        )
        logger.debug("Complexity: %s (%.2f)", response.classification, response.confidence)

        model_name = select_model(response.classification, task_type, settings, quality_mode)
        logger.debug("Model: %s", model_name)

        client = get_ai_client(model_name, settings)

        context = _apply_style_prompt(manage_message_history(messages), interaction_style)

        if task_type == "text":
            if not text_content:
                return {"content": "Error: Text content required for text task"}
            final = await _collect_response_from_text(client, context, model_name)
            return {"content": final}

        elif task_type == "image":
            final = await _collect_response_from_image(
                client, context, base64_image, model_name
            )
            return {"content": final}

        else:
            return {"content": f"Unknown task type: {task_type}"}

    except Exception as e:
        logger.error("Task processing failed: %s", e, exc_info=True)
        return {
            "content": "Something went wrong. Please try again."
        }


async def _collect_response_from_text(
    client: BaseAIClient,
    messages: List[Dict],
    model_name: str,
) -> str:
    """
    Helper method to get a *non-streaming* final text response from the AI client.
    """
    final_str = ""
    try:
        if hasattr(client, "generate_completion_stream"):
            async for chunk in client.generate_completion_stream(
                messages, model_name=model_name
            ):
                final_str += chunk
        else:
            resp = await client.generate_completion(messages, model_name=model_name)
            final_str = (
                resp if isinstance(resp, str) else resp.get("content", str(resp))
            )
    except Exception as e:
        logger.error("Text response error: %s", e)
        final_str = "Error occurred while generating text response."
    return final_str


async def _collect_response_from_image(
    client: BaseAIClient,
    messages: List[Dict],
    base64_image: str,
    model_name: str,
) -> str:
    """
    Helper method to get a *non-streaming* final image-based response from the AI client.
    """
    final_str = ""
    try:
        if hasattr(client, "generate_completion_with_image_stream"):
            async for chunk in client.generate_completion_with_image_stream(
                messages=messages, image_base64=base64_image, model_name=model_name
            ):
                final_str += chunk
        else:
            resp = await client.generate_completion_with_image(
                messages=messages, image_base64=base64_image, model_name=model_name
            )
            if isinstance(resp, str):
                final_str = resp
            elif isinstance(resp, dict) and "content" in resp:
                final_str = resp["content"]
            else:
                final_str = str(resp)
    except Exception as e:
        logger.error("Image response error: %s", e)
        final_str = "Error occurred while generating image-based response."
    return final_str


async def process_task_stream(
    task_type: str,
    text_content: Optional[str] = None,
    use_selfie: bool = False,
    complexity_scorer: ComplexityScorer = None,
    settings: Settings = None,
    messages: Optional[List[Dict]] = None,
    quality_mode: QualityMode = QualityMode.BALANCED,
    interaction_style: InteractionStyle = InteractionStyle.TEXT,
    base64_image: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Same as process_task, but yields a streaming response for real-time output.

    We do the in-memory capture for images:
      - base64_image = await prepare_image(use_selfie)
    Then pass it to the model client with generate_completion_stream or equivalent.
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
        logger.debug("Starting stream task: %s", task_type)

        if base64_image is None:
            base64_image = ""
        if task_type == "image" and not base64_image:
            base64_image = await prepare_image(use_selfie=use_selfie)

        response = await complexity_scorer.score_complexity(
            text_content,
            task_type,
            image_base64=base64_image,
            messages=messages,
        )
        logger.debug("Complexity: %s (%.2f)", response.classification, response.confidence)

        model_name = select_model(response.classification, task_type, settings, quality_mode)
        logger.debug("Model: %s", model_name)

        client = get_ai_client(model_name, settings)

        context = _apply_style_prompt(manage_message_history(messages), interaction_style)
        if task_type == "text":
            if not text_content:
                yield "Error: Text content required for text task."
                return

            if hasattr(client, "generate_completion_stream"):
                async for chunk in client.generate_completion_stream(
                    context, model_name=model_name
                ):
                    yield chunk
            else:
                resp = await client.generate_completion(context, model_name=model_name)
                if isinstance(resp, str):
                    yield resp
                elif isinstance(resp, dict) and "content" in resp:
                    yield resp["content"]
                else:
                    yield str(resp)

        elif task_type == "image":
            if hasattr(client, "generate_completion_with_image_stream"):
                async for chunk in client.generate_completion_with_image_stream(
                    messages=context, image_base64=base64_image, model_name=model_name
                ):
                    yield chunk
            else:
                resp = await client.generate_completion_with_image(
                    messages=context, image_base64=base64_image, model_name=model_name
                )
                if isinstance(resp, str):
                    yield resp
                elif isinstance(resp, dict) and "content" in resp:
                    yield resp["content"]
                else:
                    yield str(resp)
        else:
            yield f"Unknown task type: {task_type}"

    except Exception as e:
        logger.error("Stream task failed: %s", e, exc_info=True)
        yield "Something went wrong. Please try again."


async def close_all_clients():
    """
    Close all cached AI clients to avoid unclosed session warnings or errors.
    """
    for client in _AI_CLIENTS_CACHE.values():
        if hasattr(client, "close") and callable(client.close):
            await client.close()
    _AI_CLIENTS_CACHE.clear()
