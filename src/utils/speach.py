import asyncio
import platform
import sys
import logging

logger = logging.getLogger(__name__)


async def speak_text(text: str) -> None:
    if not text.strip():
        return

    system = platform.system()
    # Escape quotes
    text = text.replace('"', '\\"')

    if system == "Darwin":
        # Single call to 'say' with entire text
        cmd = f'say -r 175 -v Alex "{text}"'
    elif system == "Windows":
        ...
    elif system == "Linux":
        ...

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
    except Exception as e:
        logger.warning(f"Failed to execute TTS command: {e}")
