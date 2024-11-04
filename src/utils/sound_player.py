import asyncio
import platform
import os


async def play_sound(sound_type: str = "camera"):
    """Play a system sound."""
    system = platform.system()

    try:
        if system == "Darwin":
            if sound_type == "camera":
                # Try different possible paths for the camera sound
                sound_paths = [
                    "/System/Library/Audio/UISounds/photoShutter.caf",
                    "/System/Library/Audio/UISounds/PhotoShutter.caf",
                    "/System/Library/Sounds/Tink.aiff",
                ]

                for path in sound_paths:
                    if os.path.exists(path):
                        cmd = f"afplay {path}"
                        break
                else:
                    # Silently continue if no sound file found
                    return

        elif system == "Windows":
            cmd = "powershell -c (New-Object Media.SoundPlayer 'C:\Windows\Media\Camera.wav').PlaySync()"
        elif system == "Linux":
            cmd = "paplay /usr/share/sounds/freedesktop/stereo/camera-shutter.oga"
        else:
            return

        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await process.wait()

    except Exception as e:
        # Silently handle any sound playback errors
        pass
