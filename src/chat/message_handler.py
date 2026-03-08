# message_handler.py

"""
High-Performance Message Handler

Now refactored to work with in-memory capture & encoding APIs:
- capture_screenshot_and_encode
- capture_selfie_and_encode
No disk I/O is used for images anymore.
"""

from typing import List, Dict, Optional, AsyncGenerator

# Refactored in-memory capture utilities
from chat.capture import (
    capture_screenshot_and_encode,
    capture_selfie_and_encode,
)

from chat.complexity_scorer import ComplexityScorer, ComplexityLevel
from clients.base_client import BaseAIClient
from clients.ollama_client import OllamaClient
from clients.google_client import GoogleAIClient
from utils.mock_client import MockAIClient
from utils.settings import Settings

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
    elif model_name.startswith("gemini"):
        client = GoogleAIClient(settings, model_name=model_name)
    else:
        client = OllamaClient(settings, model_name=model_name)

    _AI_CLIENTS_CACHE[model_name] = client
    return client


def select_model(
    complexity_level: ComplexityLevel,
    task_type: str,
    settings: Settings,
) -> str:
    """
    Select the appropriate model based on complexity and task type.
    If there's no match, fallback to SIMPLE_TEXT_MODEL.
    """
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

    selected = model_mapping.get(complexity_level, settings.SIMPLE_TEXT_MODEL)
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
        print("\n[Process] Starting task processing...")
        print(f"[Process] Task type: {task_type}")

        base64_image = ""
        if task_type == "image":
            print("\n[Image] Capturing image in memory...")
            base64_image = await prepare_image(use_selfie=use_selfie)

            # Format image message consistently
            if messages and messages[-1].get("content") in ["#image", "#selfie"]:
                messages[-1]["content"] = [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": base64_image}}
                ]

        print("\n[Complexity] Analyzing task complexity...")
        response = await complexity_scorer.score_complexity(
            text_content,
            task_type,
            image_base64=base64_image,
        )
        print(f"[Complexity] Determined complexity: {response.classification}")
        print(f"[Complexity] Confidence score: {response.confidence:.2f}")

        model_name = select_model(response.classification, task_type, settings)
        print(f"[Model] Selected model: {model_name}")

        client = get_ai_client(model_name, settings)
        print("[Client] AI client initialized")

        print("\n[Request] Sending request to AI model...")

        if task_type == "text":
            if not text_content:
                return {"content": "Error: Text content required for text task"}
            final = await _collect_response_from_text(client, messages, model_name)
            return {"content": final}

        elif task_type == "image":
            # We use the base64_image we just captured
            final = await _collect_response_from_image(
                client, messages, base64_image, model_name
            )
            return {"content": final}

        else:
            return {"content": f"Unknown task type: {task_type}"}

    except Exception as e:
        print(f"\n[Error] Task processing failed: {e}")
        return {
            "content": "I apologize, but I encountered an error processing your request. Please try again."
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
        print(f"[Error] _collect_response_from_text: {e}")
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
        print(f"[Error] _collect_response_from_image: {e}")
        final_str = "Error occurred while generating image-based response."
    return final_str


async def process_task_stream(
    task_type: str,
    text_content: Optional[str] = None,
    use_selfie: bool = False,
    complexity_scorer: ComplexityScorer = None,
    settings: Settings = None,
    messages: Optional[List[Dict]] = None,
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
        print("\n[Process] Starting task processing (stream mode)...")
        print(f"[Process] Task type: {task_type}")

        base64_image = ""
        if task_type == "image":
            print("\n[Image] Capturing image in memory...")
            base64_image = await prepare_image(use_selfie=use_selfie)

            # Format image message consistently
            if messages and messages[-1].get("content") in ["#image", "#selfie"]:
                messages[-1]["content"] = [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": base64_image}}
                ]

        print("\n[Complexity] Analyzing task complexity...")
        response = await complexity_scorer.score_complexity(
            text_content,
            task_type,
            image_base64=base64_image,
        )
        print(f"[Complexity] Determined complexity: {response.classification}")
        print(f"[Complexity] Confidence score: {response.confidence:.2f}")

        model_name = select_model(response.classification, task_type, settings)
        print(f"[Model] Selected model: {model_name}")

        client = get_ai_client(model_name, settings)
        print("[Client] AI client initialized")

        print("\n[Request] Streaming response from AI model...")
        if task_type == "text":
            if not text_content:
                yield "Error: Text content required for text task."
                return

            if hasattr(client, "generate_completion_stream"):
                async for chunk in client.generate_completion_stream(
                    messages, model_name=model_name
                ):
                    yield chunk
            else:
                resp = await client.generate_completion(messages, model_name=model_name)
                if isinstance(resp, str):
                    yield resp
                elif isinstance(resp, dict) and "content" in resp:
                    yield resp["content"]
                else:
                    yield str(resp)

        elif task_type == "image":
            if hasattr(client, "generate_completion_with_image_stream"):
                async for chunk in client.generate_completion_with_image_stream(
                    messages=messages, image_base64=base64_image, model_name=model_name
                ):
                    yield chunk
            else:
                resp = await client.generate_completion_with_image(
                    messages=messages, image_base64=base64_image, model_name=model_name
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
        print(f"\n[Error] Task processing failed: {str(e)}")
        yield "I encountered an error processing your request. Please try again."


async def close_all_clients():
    """
    Close all cached AI clients to avoid unclosed session warnings or errors.
    """
    for client in _AI_CLIENTS_CACHE.values():
        if hasattr(client, "close") and callable(client.close):
            await client.close()
    _AI_CLIENTS_CACHE.clear()
