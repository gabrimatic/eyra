import os
import sys
import re
import time
import logging
import asyncio
import contextlib
import numpy as np
import pyaudio
import torch
import whisper
import simpleaudio as sa

from typing import List, Dict, Any, Optional

from chat.message_handler import process_task_stream

# from chat.message_handler import process_task_stream
# from chat.complexity_scorer import ComplexityScorer
# from modes.base_mode import BaseMode
# from utils.settings import Settings

# --------------------------------------------------------------
# 1. CONFIG
# --------------------------------------------------------------
INPUT_FORMAT = pyaudio.paInt16
INPUT_CHANNELS = 1
INPUT_RATE = 16000
INPUT_CHUNK = 1024


@contextlib.contextmanager
def suppress_stdout_stderr():
    """
    Redirect stdout/stderr to /dev/null or nul (silencing TTS logs).
    """
    null = open(os.devnull, "w")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = null, null
        yield
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        null.close()


# --------------------------------------------------------------
# 2. AUDIO UTILS
# --------------------------------------------------------------
class AudioUtils:
    def __init__(
        self,
        whisper_model_path: str,
        device: Optional[str] = None,
        tts_model_name="tts_models/en/vctk/vits",
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.device = (
            device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self.tts_available = False
        self.tts_model = None

        # Try to load TTS
        try:
            with suppress_stdout_stderr():
                from TTS.api import TTS

                self.tts_model = TTS(tts_model_name, progress_bar=False).to(self.device)
                self.tts_available = True
        except ImportError:
            self.logger.warning("TTS not available. Install with: pip install TTS")
        except Exception as e:
            self.logger.error(f"TTS initialization failed: {e}")

        # Load Whisper STT
        try:
            self.whisper_model = whisper.load_model(
                whisper_model_path, device=self.device
            )
        except Exception as e:
            self.logger.error(f"Failed loading Whisper: {e}")
            self.whisper_model = None

    def transcribe_waveform(self, waveform: np.ndarray, language="en") -> str:
        if waveform.size == 0 or not self.whisper_model:
            return ""
        try:
            result = self.whisper_model.transcribe(waveform, language=language)
            return result.get("text", "").strip()
        except Exception as e:
            self.logger.error(f"Whisper transcription failed: {e}")
            return ""

    async def generate_tts_wav(self, text: str) -> bytes:
        """
        Convert text into WAV bytes using Coqui TTS, removing '*' so it won't read them.
        Falls back to empty bytes if TTS is not available.
        """
        if not self.tts_available:
            self.logger.warning("TTS not available, skipping voice generation")
            return b""

        text_clean = re.sub(r"\*", "", text).strip()
        if not text_clean:
            return b""

        tmp_path = f"temp_{int(time.time()*1000)}.wav"
        try:
            # Add retries for robustness
            for attempt in range(3):
                try:
                    wav_data = await asyncio.to_thread(
                        self._generate_tts, text_clean, tmp_path
                    )
                    if wav_data:  # Verify we got data
                        return wav_data
                    raise ValueError("TTS generated empty audio")
                except Exception as e:
                    if attempt == 2:  # Last attempt
                        raise
                    self.logger.warning(
                        f"TTS attempt {attempt + 1} failed: {e}, retrying..."
                    )
                    await asyncio.sleep(0.5)  # Brief pause before retry
            return b""  # Fallback if all retries fail
        except Exception as exc:
            self.logger.error(f"TTS generation failed for text '{text_clean}': {exc}")
            return b""
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup temp file {tmp_path}: {e}")

    def _generate_tts(self, text: str, tmp_path: str) -> bytes:
        """Synchronous TTS generation helper"""
        with suppress_stdout_stderr():
            self.tts_model.tts_to_file(text, file_path=tmp_path, speaker="p335")
        with open(tmp_path, "rb") as f:
            return f.read()

    async def play_wav_data(self, wav_data: bytes) -> None:
        """Async wrapper for playing audio"""
        if not wav_data:
            return
        await asyncio.to_thread(self._play_wav, wav_data)

    def _play_wav(self, wav_data: bytes) -> None:
        """Synchronous playback helper"""
        import io

        with io.BytesIO(wav_data) as buf:
            wave_obj = sa.WaveObject.from_wave_file(buf)
            play_obj = wave_obj.play()
            play_obj.wait_done()


# --------------------------------------------------------------
# 3. VOICE MODE
# --------------------------------------------------------------
class VoiceMode:
    """
    Async-based:
      - Mic is opened only while recording the user.
      - We accumulate partial AI text in a buffer, output TTS by sentence.
      - This avoids word-by-word TTS.
      - The mic is off during TTS, so no self-listening is possible.
    """

    def __init__(self, settings, messages=None, complexity_scorer=None):
        self.settings = settings
        self.messages = messages if messages else []
        self.complexity_scorer = complexity_scorer

        self.SILENCE_THRESHOLD = 1000
        self.SILENCE_DURATION = 1.5
        self.MIN_PHRASE_DURATION = 1.0

        self.logger = logging.getLogger(self.__class__.__name__)

        # PyAudio
        self.pyaudio_instance = pyaudio.PyAudio()

        # Audio utils
        self.audio_utils = AudioUtils(
            whisper_model_path=self.settings.VOICE_MODEL_PATH,
            device=None,  # or 'cuda'
            tts_model_name="tts_models/en/vctk/vits",
        )

        self.tts_queue = asyncio.Queue()

    async def run(self):
        """
        Main loop, fully async, no run_until_complete.
        """
        print(
            "\n\033[92m=== Voice Mode (Sentence-based Partial TTS, Mic Off During AI) ===\033[0m"
        )
        print(" \033[94mListening...\033[0m (Press \033[91mCtrl+C\033[0m to exit)")

        while True:
            try:
                # 1) Record user
                waveform = await self._record_audio_async()
                if waveform.size == 0:
                    continue

                # 2) Transcribe
                print("\033[93mTranscribing...\033[0m")
                user_text = self.audio_utils.transcribe_waveform(waveform)
                if not user_text:
                    print("No speech detected. Listening again...")
                    continue

                print(f"\033[94mYou:\033[0m {user_text}")
                if user_text.lower() in ["/quit", "/exit"]:
                    print("Goodbye!")
                    break

                # 3) Get partial AI text, but TTS chunk by sentence
                print("\033[95mThinking...\033[0m")
                self.messages.append({"role": "user", "content": user_text})

                final_text = await self._speak_sentences_async(user_text)
                self.messages.append({"role": "assistant", "content": final_text})

                print(f"\033[92mAssistant (final):\033[0m {final_text}\n")
                print(" \033[94mListening...\033[0m")

            except KeyboardInterrupt:
                print("Exiting Voice Mode.")
                break
            except Exception as e:
                self.logger.error(f"VoiceMode error: {e}", exc_info=True)
                print("Error occurred. Continuing...")

        self.pyaudio_instance.terminate()

    async def _record_audio_async(self) -> np.ndarray:
        """
        Opens mic, records until silence or 30s, closes mic.
        """
        device_idx = self._find_input_device()
        if device_idx is None:
            print("No input device found.")
            return np.array([], dtype=np.float32)

        stream = self.pyaudio_instance.open(
            format=INPUT_FORMAT,
            channels=INPUT_CHANNELS,
            rate=INPUT_RATE,
            input=True,
            input_device_index=device_idx,
            frames_per_buffer=INPUT_CHUNK,
        )

        frames = []
        silent_frames = 0
        is_speaking = False
        start_time = time.time()

        try:
            while True:
                data = stream.read(INPUT_CHUNK)
                audio_data = np.frombuffer(data, dtype=np.int16)
                volume = np.abs(audio_data).mean()

                if volume > self.SILENCE_THRESHOLD:
                    if not is_speaking:
                        is_speaking = True
                        print("\033[96mVoice detected...\033[0m")
                    frames.append(data)
                    silent_frames = 0
                elif is_speaking:
                    frames.append(data)
                    silent_frames += 1
                    if (
                        silent_frames * INPUT_CHUNK / INPUT_RATE
                    ) > self.SILENCE_DURATION:
                        break

                if is_speaking and (time.time() - start_time) > 30:
                    break
        finally:
            stream.stop_stream()
            stream.close()

        duration = len(frames) * INPUT_CHUNK / INPUT_RATE
        if duration < self.MIN_PHRASE_DURATION:
            return np.array([], dtype=np.float32)

        waveform = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32)
        waveform *= 1.0 / 32768.0
        return waveform

    async def _speak_sentences_async(self, user_text: str) -> str:
        final_chunks = []
        buffer = ""
        # Stricter sentence ending pattern - must end with proper punctuation
        sentence_end = re.compile(r"([.!?])\s*")

        try:
            play_task = asyncio.create_task(self._play_tts_chunks())

            async for partial in process_task_stream(
                task_type="text",
                text_content=user_text,
                complexity_scorer=self.complexity_scorer,
                settings=self.settings,
                messages=self.messages,
            ):
                buffer += partial

                # Keep checking for sentence boundaries until no more found
                while True:
                    match = sentence_end.search(buffer)
                    if match:
                        end_idx = match.end()
                        sentence = buffer[:end_idx].strip()

                        # Only process if we have a meaningful sentence
                        if len(sentence) >= 20:  # Minimum sentence length
                            buffer = buffer[end_idx:].lstrip()
                            print("\033[92mAssistant (sentence):\033[0m", sentence)
                            final_chunks.append(sentence)
                            await self.tts_queue.put(sentence)
                        else:
                            break  # Wait for more text if sentence too short
                    else:
                        break

                # Only force split if buffer is very large (increased threshold)
                if len(buffer) > 300:  # Increased from 200 to 300
                    # Try to find a natural break point
                    break_points = [
                        buffer.rfind(", "),
                        buffer.rfind(" and "),
                        buffer.rfind(" or "),
                        buffer.rfind(" but "),
                    ]

                    # Filter valid break points
                    valid_points = [p for p in break_points if p != -1]

                    if valid_points:
                        # Use the latest valid break point
                        idx = max(valid_points) + 1
                    else:
                        # Fallback to space if no natural breaks found
                        idx = buffer.rfind(" ", 0, len(buffer) // 2)
                        if idx == -1:
                            idx = len(buffer) // 2

                    forced = buffer[:idx].strip()
                    buffer = buffer[idx:].lstrip()

                    if forced:  # Only add if we have content
                        print("\033[93mAssistant (long chunk):\033[0m", forced)
                        final_chunks.append(
                            forced + "..."
                        )  # Add ellipsis for forced breaks
                        await self.tts_queue.put(forced)

            # Handle remaining text - try to clean it up
            leftover = buffer.strip()
            if leftover:
                # Add proper ending if missing
                if not sentence_end.search(leftover):
                    leftover += "."
                print("\033[92mAssistant (final chunk):\033[0m", leftover)
                final_chunks.append(leftover)
                await self.tts_queue.put(leftover)

            # Signal end and wait for playback
            await self.tts_queue.put(None)
            await play_task

            return " ".join(final_chunks).strip()

        except Exception as e:
            self.logger.error(f"Speech processing error: {e}")
            # Cleanup on error
            if play_task and not play_task.done():
                play_task.cancel()
            # Clear queue
            while not self.tts_queue.empty():
                try:
                    self.tts_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            return (
                " ".join(final_chunks).strip()
                if final_chunks
                else "Sorry, there was an error processing the speech."
            )

    async def _play_tts_chunks(self):
        """
        Continuously plays TTS chunks from the queue, pre-generating the next chunk
        while the current one is playing.
        """
        next_wav_data = None
        chunks_processed = 0

        try:
            while True:
                current_wav_data = next_wav_data
                next_text = await self.tts_queue.get()

                if next_text is None:
                    if current_wav_data:
                        await self.audio_utils.play_wav_data(current_wav_data)
                    self.logger.info(
                        f"TTS playback completed, processed {chunks_processed} chunks"
                    )
                    break

                chunks_processed += 1
                if current_wav_data:
                    try:
                        # Run TTS generation and playback concurrently
                        next_wav_task = asyncio.create_task(
                            self.audio_utils.generate_tts_wav(next_text)
                        )
                        play_task = asyncio.create_task(
                            self.audio_utils.play_wav_data(current_wav_data)
                        )

                        # Wait for both operations to complete
                        done, pending = await asyncio.wait(
                            [play_task, next_wav_task],
                            return_when=asyncio.ALL_COMPLETED,
                        )

                        # Check for errors
                        for task in done:
                            if task.exception():
                                raise task.exception()

                        next_wav_data = next_wav_task.result()
                        if not next_wav_data:
                            self.logger.warning(
                                f"Empty TTS generated for text: {next_text}"
                            )
                    except Exception as e:
                        self.logger.error(
                            f"Error processing chunk {chunks_processed}: {e}"
                        )
                        # Continue with next chunk if possible
                        next_wav_data = None
                else:
                    # First iteration - just generate the first chunk
                    next_wav_data = await self.audio_utils.generate_tts_wav(next_text)

        except asyncio.CancelledError:
            self.logger.info("TTS playback cancelled")
            raise
        except Exception as e:
            self.logger.error(f"Fatal error in TTS playback: {e}")
            raise

    def _find_input_device(self) -> Optional[int]:
        count = self.pyaudio_instance.get_device_count()
        for i in range(count):
            info = self.pyaudio_instance.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                self.logger.info(f"Using input device: {info['name']}")
                return i
        return None
