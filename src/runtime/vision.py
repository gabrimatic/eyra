"""Controller-owned screen capture and vision-model analysis."""

from __future__ import annotations

import asyncio

from chat.capture import capture_screenshot_and_encode
from chat.message_handler import _apply_style_prompt, get_ai_client
from chat.session_state import InteractionStyle
from runtime.models import PreflightResult
from utils.image_history import manage_message_history
from utils.settings import Settings


def vision_model_name(settings: Settings) -> str:
    return settings.VISION_MODEL or settings.MODEL


def vision_model_available(settings: Settings, preflight: PreflightResult) -> bool:
    model = vision_model_name(settings)
    checked = set(getattr(preflight, "vision_capability_checked_models", []))
    capable = set(getattr(preflight, "vision_capable_models", []))
    return model not in checked or model in capable


async def analyze_screen(
    *,
    settings: Settings,
    prompt: str,
    conversation_messages: list[dict],
    current_goal: str | None,
    model_semaphore: asyncio.Semaphore | None,
    preflight: PreflightResult,
) -> str:
    """Capture the screen locally, then ask a vision model about the image.

    The screenshot is captured by Eyra's controller code, not by model tool
    calling. This lets a non-tool vision model answer screen questions while
    keeping screenshots in memory.
    """
    if not preflight.screen_capture_available:
        return "Screen capture is not available on this Mac. Grant Screen Recording permission, then try again."
    if not vision_model_available(settings, preflight):
        return (
            "Screen analysis needs a vision-capable model. Set VISION_MODEL to a model that can process images, "
            "or use a main MODEL with vision support."
        )

    image_base64 = await capture_screenshot_and_encode()
    if not image_base64:
        return "I could not capture the screen. Check macOS Screen Recording permission for this terminal."

    model_name = vision_model_name(settings)
    vision_settings = Settings(**{**settings.__dict__, "MODEL": model_name})
    user_prompt = (
        "Answer the user's screen question from the attached screenshot. "
        "Do not guess beyond visible evidence. If text is unreadable, say so clearly.\n\n"
        f"User request: {prompt}"
    )
    messages = _apply_style_prompt(
        manage_message_history(conversation_messages + [{"role": "user", "content": user_prompt}]),
        InteractionStyle.TEXT,
        current_goal=current_goal,
    )
    client = get_ai_client(model_name, vision_settings)

    async def _collect() -> str:
        result = ""
        async for chunk in client.generate_completion_with_image_stream(
            messages,
            image_base64,
            model_name=model_name,
        ):
            result += chunk
        return result.strip()

    if model_semaphore is None:
        return await _collect()
    async with model_semaphore:
        return await _collect()
