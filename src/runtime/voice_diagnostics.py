"""Local microphone, VAD, Local Whisper, and barge-in diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

from runtime.voice_input import CHANNELS, DTYPE, FRAME_SAMPLES, SAMPLE_RATE, SOCKET_PATH, VoiceInput, _int16_to_float32
from utils.settings import Settings

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class DiagnosticCheck:
    name: str
    status: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiagnosticReport:
    title: str
    checks: list[DiagnosticCheck] = field(default_factory=list)

    def add(self, name: str, status: str, reason: str, **details: Any) -> None:
        self.checks.append(DiagnosticCheck(name=name, status=status, reason=reason, details=details))

    def check(self, name: str) -> DiagnosticCheck:
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)

    def extend(self, other: "DiagnosticReport") -> None:
        self.checks.extend(other.checks)

    def render(self) -> str:
        lines = [f"  {self.title}"]
        for check in self.checks:
            lines.append(f"  {check.status:<7} {check.name}: {check.reason}")
        return "\n".join(lines)


def list_input_devices() -> list[dict[str, Any]]:
    """Return sounddevice input devices in a stable, testable shape."""
    devices = []
    try:
        raw_devices = sd.query_devices()
    except Exception:
        return devices
    for index, device in enumerate(raw_devices):
        info = dict(device)
        info.setdefault("index", index)
        if int(info.get("max_input_channels", 0) or 0) > 0:
            devices.append(info)
    return devices


def resolve_input_device(selector: str | int | None, devices: list[dict[str, Any]] | None = None) -> int | str | None:
    """Resolve a configured microphone selector by index or case-insensitive name fragment."""
    if selector is None:
        return None
    if isinstance(selector, int):
        return selector
    needle = str(selector).strip()
    if not needle:
        return None
    devices = list_input_devices() if devices is None else devices
    if needle.isdigit():
        index = int(needle)
        for device in devices:
            if int(device.get("index", -1)) == index and int(device.get("max_input_channels", 0) or 0) > 0:
                return index
        raise ValueError(f"Configured microphone device {needle} was not found.")
    lowered = needle.lower()
    matches = [
        device
        for device in devices
        if lowered in str(device.get("name", "")).lower() and int(device.get("max_input_channels", 0) or 0) > 0
    ]
    if not matches:
        raise ValueError(f"Configured microphone device '{needle}' was not found.")
    if len(matches) > 1:
        names = ", ".join(str(device.get("name", "")) for device in matches[:3])
        raise ValueError(f"Configured microphone device '{needle}' matched more than one input: {names}.")
    return int(matches[0].get("index"))


class VoiceDiagnostics:
    """Runs bounded local diagnostics without sending audio off the machine."""

    def __init__(self, settings: Settings, wh_bin: str | None = None):
        self.settings = settings
        self.wh_bin = wh_bin or "wh"
        self.sample_rate = int(getattr(settings, "VOICE_SAMPLE_RATE", SAMPLE_RATE) or SAMPLE_RATE)
        self.record_seconds = max(1, int(getattr(settings, "VOICE_DEBUG_RECORD_SECONDS", 3) or 3))
        self.save_audio = bool(getattr(settings, "VOICE_DIAGNOSTIC_SAVE_AUDIO", False))
        self.device_selector = getattr(settings, "VOICE_INPUT_DEVICE", "") or ""
        self._last_audio: np.ndarray | None = None
        self._last_audio_path: str | None = None
        self._last_vad_speech_detected = False

    async def run(self, *, include_physical_barge_in: bool = False) -> DiagnosticReport:
        report = await asyncio.to_thread(self.run_microphone_checks)
        report.extend(await asyncio.to_thread(self.run_local_whisper_checks))
        if include_physical_barge_in:
            report.extend(await self.run_barge_in_probe())
        else:
            report.add(
                "tts_interrupt_by_mic_speech",
                "skipped",
                "Physical barge-in needs the user to speak during /voice-test or /voice-diagnose barge-in.",
            )
        return report

    def run_microphone_checks(self) -> DiagnosticReport:
        report = DiagnosticReport("Voice diagnostics")
        devices = list_input_devices()
        if devices:
            report.add("input_devices", "passed", f"{len(devices)} input device{'s' if len(devices) != 1 else ''} found")
        else:
            report.add("input_devices", "failed", "No sounddevice input devices were reported.")

        try:
            selected_device = resolve_input_device(self.device_selector, devices)
            selected_label = "system default" if selected_device is None else str(selected_device)
            report.add("selected_input_device", "passed", selected_label)
        except ValueError as exc:
            report.add("selected_input_device", "failed", str(exc))
            report.add("sample_rate_support", "skipped", "No valid microphone device was selected.")
            report.add("captured_audio", "skipped", "No valid microphone device was selected.")
            return report

        try:
            sd.check_input_settings(
                device=selected_device,
                channels=CHANNELS,
                samplerate=self.sample_rate,
                dtype=DTYPE,
            )
            report.add("sample_rate_support", "passed", f"{self.sample_rate} Hz input is supported")
        except Exception as exc:
            report.add("sample_rate_support", "failed", f"{self.sample_rate} Hz input is not usable: {exc}")
            report.add("captured_audio", "skipped", "Sample rate check failed.")
            return report

        frames: list[np.ndarray] = []
        overflowed = False
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                device=selected_device,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=FRAME_SAMPLES,
            ) as stream:
                frame_count = max(1, int(self.record_seconds * self.sample_rate / FRAME_SAMPLES))
                for _ in range(frame_count):
                    try:
                        data, overflow = stream.read(FRAME_SAMPLES)
                    except StopIteration:
                        break
                    overflowed = overflowed or bool(overflow)
                    frame = data[:, 0] if getattr(data, "ndim", 1) > 1 else data.flatten()
                    frames.append(frame.copy())
        except sd.PortAudioError as exc:
            reason = f"Microphone stream failed: {exc}"
            report.add("captured_audio", "failed", reason)
            permission_status = "failed" if "permission" in str(exc).lower() else "skipped"
            report.add("macos_microphone_permission", permission_status, "macOS microphone permission may be blocked.")
            return report
        except Exception as exc:
            report.add("captured_audio", "failed", f"Microphone stream failed: {exc}")
            report.add("macos_microphone_permission", "skipped", "Permission status could not be inferred.")
            return report

        if not frames:
            report.add("captured_audio", "failed", "No audio samples were captured.")
            report.add("stream_overflow", "skipped", "No stream data was captured.")
            report.add("macos_microphone_permission", "skipped", "Permission status could not be inferred.")
            return report

        audio = np.concatenate(frames).astype(np.int16, copy=False)
        self._last_audio = audio
        if np.any(audio):
            report.add("captured_audio", "passed", f"Captured {len(audio)} nonzero-capable samples")
            report.add(
                "macos_microphone_permission",
                "passed",
                "Microphone delivered nonzero samples; macOS permission is not obviously blocking capture.",
            )
        else:
            report.add("captured_audio", "failed", "microphone input is silent/all-zero")
            report.add(
                "macos_microphone_permission",
                "failed",
                "All-zero audio can mean microphone permission, a muted input, or the wrong input device.",
            )
        report.add("stream_overflow", "failed" if overflowed else "passed", "Input stream reported overflow." if overflowed else "No overflow reported.")
        vad_status, vad_reason = self._vad_check(audio)
        self._last_vad_speech_detected = vad_status == "passed"
        report.add("vad_detected_speech", vad_status, vad_reason)
        self._last_audio_path = self._maybe_save_audio(audio, report)
        return report

    def _vad_check(self, audio: np.ndarray) -> tuple[str, str]:
        if not np.any(audio):
            return "skipped", "VAD skipped because captured audio was all-zero."
        try:
            voice_input = VoiceInput(
                silence_duration_ms=int(getattr(self.settings, "VOICE_SILENCE_MS", 1500) or 1500),
                threshold=float(getattr(self.settings, "VOICE_VAD_THRESHOLD", 0.6) or 0.6),
                wh_bin=self.wh_bin,
                input_device=self.device_selector or None,
                sample_rate=self.sample_rate,
            )
            vad = voice_input._new_vad_iterator()
            frame_samples = int(FRAME_SAMPLES * (self.sample_rate / SAMPLE_RATE))
            for start in range(0, len(audio) - frame_samples + 1, frame_samples):
                chunk = audio[start : start + frame_samples]
                event = vad(_int16_to_float32(chunk), return_seconds=False)
                if event is not None and "start" in event:
                    return "passed", "VAD detected speech onset."
            return "skipped", "VAD needs live speech in the diagnostic capture."
        except Exception as exc:
            return "failed", f"VAD probe failed: {exc}"

    def _maybe_save_audio(self, audio: np.ndarray, report: DiagnosticReport) -> str | None:
        if not self.save_audio:
            report.add("diagnostic_audio_saved", "skipped", "VOICE_DIAGNOSTIC_SAVE_AUDIO=false")
            return None
        root = Path.home() / "Library" / "Application Support" / "Eyra" / "diagnostics"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"voice-diagnostic-{int(time.time())}.wav"
        self._write_wav(path, audio, self.sample_rate)
        report.add("diagnostic_audio_saved", "passed", str(path))
        return str(path)

    def run_local_whisper_checks(self) -> DiagnosticReport:
        report = DiagnosticReport("Local Whisper diagnostics")
        if not self.wh_bin:
            report.add("local_whisper_binary", "failed", "Local Whisper CLI was not resolved.")
            return report
        report.add("local_whisper_binary", "passed", self.wh_bin)
        report.add("local_whisper_socket", "passed" if SOCKET_PATH.exists() else "failed", str(SOCKET_PATH))

        try:
            status = subprocess.run([self.wh_bin, "status"], capture_output=True, text=True, timeout=3)
            output = f"{status.stdout or ''}{status.stderr or ''}".strip()
            if status.returncode == 0 and "running" in output.lower():
                report.add("local_whisper_asr_reachable", "passed", "Local Whisper service is running.")
            else:
                report.add("local_whisper_asr_reachable", "failed", output or "Local Whisper status did not report running.")
        except Exception as exc:
            report.add("local_whisper_asr_reachable", "failed", f"Could not query Local Whisper: {exc}")

        probe_path = self._generated_probe_wav()
        try:
            proc = subprocess.run([self.wh_bin, "transcribe", probe_path], capture_output=True, text=True, timeout=30)
            output = _clean_text(proc.stderr or proc.stdout or "")
            if proc.returncode == 0 or "empty transcription" in output.lower():
                report.add("wh_transcribe_generated_wav", "passed", "wh transcribe accepted a generated local WAV.")
            else:
                report.add("wh_transcribe_generated_wav", "failed", output or "transcribe failed")
        except Exception as exc:
            report.add("wh_transcribe_generated_wav", "failed", f"wh transcribe failed: {exc}")
        finally:
            with contextlib.suppress(Exception):
                Path(probe_path).unlink()

        if self._last_audio is not None and np.any(self._last_audio) and self._last_vad_speech_detected:
            wav_path = self._last_audio_path or self._generated_capture_wav(self._last_audio)
            try:
                proc = subprocess.run([self.wh_bin, "transcribe", wav_path], capture_output=True, text=True, timeout=30)
                text = (proc.stdout or "").strip()
                if proc.returncode == 0 and text:
                    report.add("transcription_returns_text", "passed", text[:160])
                elif proc.returncode == 0:
                    report.add("transcription_returns_text", "failed", "ASR returned no text for the diagnostic capture.")
                else:
                    report.add("transcription_returns_text", "failed", _clean_text(proc.stderr or "ASR failed"))
            finally:
                if wav_path != self._last_audio_path:
                    with contextlib.suppress(Exception):
                        Path(wav_path).unlink()
        elif self._last_audio is not None and np.any(self._last_audio):
            report.add("transcription_returns_text", "skipped", "No detected speech in the diagnostic capture.")
        else:
            report.add("transcription_returns_text", "skipped", "No nonzero microphone audio was captured.")
        return report

    async def run_barge_in_probe(self) -> DiagnosticReport:
        report = DiagnosticReport("Barge-in diagnostics")
        interrupted = False
        proc = None

        def interrupt_on_speech() -> None:
            nonlocal interrupted
            interrupted = True
            if proc is not None and proc.returncode is None:
                proc.terminate()

        try:
            proc = await asyncio.create_subprocess_exec(
                self.wh_bin,
                "whisper",
                "This is Eyra's barge-in diagnostic. Start speaking now.",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            report.add("tts_interrupt_by_mic_speech", "failed", f"Could not start Local Whisper speech: {exc}")
            return report

        try:
            voice_input = VoiceInput(
                silence_duration_ms=int(getattr(self.settings, "VOICE_SILENCE_MS", 1500) or 1500),
                max_duration_s=self.record_seconds,
                threshold=float(getattr(self.settings, "VOICE_VAD_THRESHOLD", 0.6) or 0.6),
                wh_bin=self.wh_bin,
                input_device=self.device_selector or None,
                sample_rate=self.sample_rate,
            )
            text = await voice_input.listen(on_speech_start=interrupt_on_speech)
        finally:
            if proc.returncode is None:
                proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=2)

        if interrupted and text:
            report.add(
                "tts_interrupt_by_mic_speech",
                "failed",
                "ASR transcribed audio during TTS, but this unattended run cannot prove it was human speech rather than speaker echo.",
            )
        elif interrupted:
            report.add("tts_interrupt_by_mic_speech", "failed", "TTS stopped, but ASR returned no text.")
        else:
            report.add("tts_interrupt_by_mic_speech", "failed", "No microphone speech onset was detected during TTS.")
        return report

    def _generated_probe_wav(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        samples = np.zeros(max(1, int(self.sample_rate * 0.1)), dtype=np.int16)
        self._write_wav(Path(path), samples, self.sample_rate)
        return path

    def _generated_capture_wav(self, audio: np.ndarray) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        self._write_wav(Path(path), audio, self.sample_rate)
        return path

    @staticmethod
    def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio.astype(np.int16, copy=False).tobytes())


def _clean_text(text: str) -> str:
    return _ANSI_RE.sub("", text).strip()
