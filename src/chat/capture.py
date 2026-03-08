# capture.py
"""
High-level capture & encoding functions using Python libraries,
avoiding disk I/O by capturing frames/screenshots directly to memory.

Improvements:
1. Private helpers for DRY code and clarity.
2. Optional parameters for camera usage.
3. Unified logic for capturing+encoding.
4. Consistent logging & docstrings.

Functions:
- _validate_pil_image(img: Image.Image) -> bool
- _encode_pil_image_in_memory(...)
- capture_screenshot_in_memory() -> Image.Image
- capture_selfie_in_memory(...) -> Image.Image
- capture_screenshot_and_encode(...)
- capture_selfie_and_encode(...)
"""

import logging
import base64
import time
from io import BytesIO
from typing import Tuple, Callable, Awaitable

import mss
import cv2
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
    img: Image.Image, max_size: Tuple[int, int] = (1024, 728), quality: int = 85
) -> str:
    """
    1) Optionally resizes 'img' if it's larger than 'max_size'.
    2) Converts to "RGB" if needed.
    3) Removes all metadata.
    4) Saves to an in-memory buffer (JPEG) with the specified 'quality'.
    5) Returns the base64-encoded string of that buffer.

    Args:
        img (Image.Image): The PIL Image to encode.
        max_size (Tuple[int, int]): (width, height) limit for in-memory resize.
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
    if img.mode in ("RGBA", "P", "CMYK", "P"):
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


def _initialize_camera(
    camera_index: int = 0, backend: int = cv2.CAP_AVFOUNDATION
) -> cv2.VideoCapture:
    """
    Attempt to initialize an OpenCV VideoCapture with the given backend.
    If it fails, fallback to cv2.CAP_ANY.

    Args:
        camera_index (int): Which camera device index to open (default=0).
        backend (int): OpenCV backend code, e.g., cv2.CAP_AVFOUNDATION on macOS.

    Returns:
        A valid cv2.VideoCapture object.

    Raises:
        RuntimeError: If no camera can be opened at all.
    """
    cap = cv2.VideoCapture(camera_index, backend)
    if not cap.isOpened():
        # Fallback to any backend if the given one fails
        cap = cv2.VideoCapture(camera_index, cv2.CAP_ANY)

    if not cap.isOpened():
        raise RuntimeError("Could not open webcam (OpenCV).")
    return cap


def _warm_up_camera(
    cap: cv2.VideoCapture, frames_to_discard: int = 6, sleep_time: float = 0.1
) -> None:
    """
    Discard a few black frames if the camera is not ready yet.
    Helps reduce 'dark image' captures.

    Args:
        cap (cv2.VideoCapture): The already-initialized capture.
        frames_to_discard (int): How many frames to read/discard.
        sleep_time (float): How many seconds to wait between each frame capture.
    """
    for _ in range(frames_to_discard):
        ret, _ = cap.read()
        if not ret:
            time.sleep(sleep_time)
        else:
            time.sleep(sleep_time)


async def capture_screenshot_in_memory() -> Image.Image:
    """
    Capture the primary monitor's screenshot directly into a PIL Image
    without writing to disk (using 'mss').

    Returns:
        PIL.Image: The screenshot as an in-memory PIL Image.
    """
    await play_sound("camera")

    with mss.mss() as sct:
        # Grab the entire primary monitor
        monitor = sct.monitors[0]
        raw_screenshot = sct.grab(monitor)  # MSS returns an mss.base.ScreenShot

        # Convert raw bytes to a PIL Image (RGB)
        img = Image.frombytes(
            "RGB", (raw_screenshot.width, raw_screenshot.height), raw_screenshot.rgb
        )
    logger.info("Screenshot captured in memory.")
    return img


async def capture_selfie_in_memory(
    camera_index: int = 0, camera_backend: int = cv2.CAP_AVFOUNDATION
) -> Image.Image:
    """
    Capture a single frame from the default webcam into a PIL Image
    (using 'OpenCV') without writing to disk.

    Args:
        camera_index (int): Which camera device to open (default=0).
        camera_backend (int): Desired OpenCV capture backend (default=CAP_AVFOUNDATION).

    Returns:
        PIL.Image: The webcam image as an in-memory PIL Image.
    """
    await play_sound("camera")

    cap = _initialize_camera(camera_index, camera_backend)

    # Set some typical capture dimensions (optional, but often helps)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # Warm-up to avoid black frames
    _warm_up_camera(cap, frames_to_discard=6, sleep_time=0.1)

    # Now read the actual frame
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Failed to capture webcam frame (returned None).")

    # Convert from BGR to RGB
    img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(img_rgb)
    logger.info("Selfie captured in memory.")
    return img


async def _capture_and_encode(
    capture_func: Callable[[], Awaitable[Image.Image]],
    max_size: Tuple[int, int],
    quality: int,
) -> str:
    """
    Private helper to unify the flow of capture + encode.
    """
    pil_image = await capture_func()
    return _encode_pil_image_in_memory(pil_image, max_size, quality)


async def capture_screenshot_and_encode(
    max_size: Tuple[int, int] = (1024, 728), quality: int = 99
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


async def capture_selfie_and_encode(
    max_size: Tuple[int, int] = (1024, 728), quality: int = 99
) -> str:
    """
    Capture a webcam frame entirely in memory, then encode it as base64 JPEG.

    Args:
        max_size (Tuple[int,int]): Resize limit for final image if needed.
        quality (int): JPEG compression quality.

    Returns:
        str: Base64-encoded JPEG data.
    """
    return await _capture_and_encode(
        capture_func=capture_selfie_in_memory, max_size=max_size, quality=quality
    )
