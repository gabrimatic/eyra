import os
import platform
import subprocess

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
            subprocess.Popen(
                ["afplay", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        elif system == "Windows":
            subprocess.Popen(
                r"powershell -c (New-Object Media.SoundPlayer 'C:\Windows\Media\Camera.wav').PlaySync()",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            )

        elif system == "Linux":
            subprocess.Popen(
                "paplay /usr/share/sounds/freedesktop/stereo/camera-shutter.oga",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            )

    except Exception:
        # Silently handle any sound playback errors
        pass
