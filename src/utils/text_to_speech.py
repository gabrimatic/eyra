import asyncio
import platform


async def speak_text(text: str):
    """Convert text to speech using system commands."""
    system = platform.system()

    if system == "Darwin":
        cmd = f'say -r 178 "{text}"'
    elif system == "Windows":
        cmd = f"powershell -c \"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{text}')\""
    elif system == "Linux":
        cmd = f'espeak -s 80 "{text}"'
    else:
        print("Text-to-speech not supported on this platform")
        return

    # Create and wait for the process to complete
    process = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    # Wait for the process to finish
    await process.wait()
