"""In-memory screenshot capture and base64 encoding via mss + Pillow."""

import base64
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

import mss
from PIL import Image

from utils.sound_player import play_sound

logger = logging.getLogger(__name__)


def _validate_pil_image(img: Image.Image) -> bool:
    """
    Validate that 'img' is a non-empty, valid PIL Image.

    Returns:
        bool: True if 'img' is valid; False otherwise.
    """
    if not img:
        logger.error("Validation failed: Image object is None.")
        return False

    if not isinstance(img, Image.Image):
        logger.error("Validation failed: Provided object is not a PIL Image.")
        return False

    width, height = img.size
    if width == 0 or height == 0:
        logger.warning("Validation failed: Image has zero width or height.")
        return False

    return True


def _encode_pil_image_in_memory(
    img: Image.Image, max_size: tuple[int, int] = (1024, 728), quality: int = 85
) -> str:
    """
    1) Optionally resizes 'img' if it's larger than 'max_size'.
    2) Converts to "RGB" if needed.
    3) Removes all metadata.
    4) Saves to an in-memory buffer (JPEG) with the specified 'quality'.
    5) Returns the base64-encoded string of that buffer.

    Args:
        img (Image.Image): The PIL Image to encode.
        max_size (tuple[int, int]): (width, height) limit for in-memory resize.
        quality (int): JPEG compression quality (1-100).

    Raises:
        ValueError: If 'img' is invalid or if the in-memory buffer is empty.

    Returns:
        str: The base64-encoded JPEG data.
    """
    if not _validate_pil_image(img):
        raise ValueError("Invalid in-memory image; cannot encode.")

    logger.debug(f"Original capture size: {img.size}, mode: {img.mode}")

    # Resize if needed
    if img.width > max_size[0] or img.height > max_size[1]:
        logger.debug("Resizing image in-memory before encoding.")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

    # Convert to RGB if needed
    if img.mode in ("RGBA", "P", "CMYK", "L"):
        img = img.convert("RGB")

    # Remove metadata
    img.info = {}

    # Save to a BytesIO buffer
    buf = BytesIO()
    img.save(
        buf,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=2,  # 4:2:0 subsampling
    )
    buf.seek(0)

    data = buf.read()
    if not data:
        raise ValueError("No data found in the in-memory buffer after saving.")

    encoded = base64.b64encode(data).decode("utf-8")
    if not encoded:
        raise ValueError("Base64-encoded string is empty.")

    logger.debug(f"Encoded image size in base64: {len(encoded)} characters")
    return encoded


async def capture_screenshot_in_memory() -> Image.Image:
    """
    Capture the primary monitor's screenshot directly into a PIL Image
    without writing to disk (using 'mss').

    Returns:
        PIL.Image: The screenshot as an in-memory PIL Image.
    """
    import asyncio

    await play_sound("camera")

    def _grab():
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            raw_screenshot = sct.grab(monitor)
            return Image.frombytes(
                "RGB", (raw_screenshot.width, raw_screenshot.height), raw_screenshot.rgb
            )

    img = await asyncio.to_thread(_grab)
    logger.info("Screenshot captured in memory.")
    return img


async def _capture_and_encode(
    capture_func: Callable[[], Awaitable[Image.Image]],
    max_size: tuple[int, int],
    quality: int,
) -> str:
    """
    Private helper to unify the flow of capture + encode.
    """
    pil_image = await capture_func()
    return _encode_pil_image_in_memory(pil_image, max_size, quality)


async def capture_screenshot_and_encode(
    max_size: tuple[int, int] = (1024, 728), quality: int = 99
) -> str:
    """
    Capture a screenshot entirely in memory, then encode it as a base64 JPEG.

    Args:
        max_size (Tuple[int,int]): Resize limit for final image if needed.
        quality (int): JPEG compression quality.

    Returns:
        str: Base64-encoded JPEG data.
    """
    return await _capture_and_encode(
        capture_func=capture_screenshot_in_memory, max_size=max_size, quality=quality
    )
