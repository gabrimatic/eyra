import asyncio
import os
import platform

_DARWIN_SOUNDS = {
    "camera": [
        "/System/Library/Audio/UISounds/photoShutter.caf",
        "/System/Library/Audio/UISounds/PhotoShutter.caf",
        "/System/Library/Sounds/Tink.aiff",
    ],
    "listen": ["/System/Library/Sounds/Tink.aiff"],
    "process": ["/System/Library/Sounds/Pop.aiff"],
    "respond": ["/System/Library/Sounds/Glass.aiff"],
}


async def play_sound(sound_type: str = "camera"):
    """Play a system sound. Non-blocking -- fires and forgets the subprocess."""
    system = platform.system()

    try:
        if system == "Darwin":
            candidates = _DARWIN_SOUNDS.get(sound_type, [])
            sound_path = next((p for p in candidates if os.path.exists(p)), None)
            if sound_path is None:
                return
            cmd = ["afplay", sound_path]
            await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            # Intentionally not awaiting -- fire and forget

        elif system == "Windows":
            await asyncio.create_subprocess_shell(
                r"powershell -c (New-Object Media.SoundPlayer 'C:\Windows\Media\Camera.wav').PlaySync()",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

        elif system == "Linux":
            await asyncio.create_subprocess_shell(
                "paplay /usr/share/sounds/freedesktop/stereo/camera-shutter.oga",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

    except Exception:
        # Silently handle any sound playback errors
        pass
