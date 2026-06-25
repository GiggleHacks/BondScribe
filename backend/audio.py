"""Audio capture for Windows 11 via pyaudiowpatch.

Two modes:
  - "loopback": capture whatever is playing through the default speakers (WASAPI loopback)
  - "mic": capture the default microphone

Yields mono float32 frames at 16 kHz (resampled from the device's native rate).
"""
from __future__ import annotations

import argparse
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np

# Audio capture relies on pyaudiowpatch (WASAPI), which is Windows-only. Fail
# with a clear message instead of a cryptic ImportError if someone runs this on
# another OS.
if os.name != "nt":
    raise RuntimeError(
        "BondScribe currently supports Windows only. Audio capture uses "
        "pyaudiowpatch (WASAPI), which is Windows-specific; macOS/Linux support "
        "is not yet available."
    )

import pyaudiowpatch as pyaudio

TARGET_RATE = 16_000
# Silero VAD v5 requires exactly 512-sample windows at 16 kHz (32 ms).
FRAME_SAMPLES = 512
FRAME_MS = FRAME_SAMPLES * 1000 // TARGET_RATE  # 32 ms


@dataclass
class DeviceInfo:
    index: int
    name: str
    channels: int
    rate: int
    is_loopback: bool


def list_devices() -> list[DeviceInfo]:
    pa = pyaudio.PyAudio()
    devices: list[DeviceInfo] = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] <= 0:
                continue
            devices.append(
                DeviceInfo(
                    index=int(info["index"]),
                    name=str(info["name"]),
                    channels=int(info["maxInputChannels"]),
                    rate=int(info["defaultSampleRate"]),
                    is_loopback=bool(info.get("isLoopbackDevice", False)),
                )
            )
    finally:
        pa.terminate()
    return devices


def _default_loopback(pa: pyaudio.PyAudio) -> dict:
    # Find the loopback device that mirrors the default speakers.
    default_speakers = pa.get_device_info_by_index(
        pa.get_host_api_info_by_type(pyaudio.paWASAPI)["defaultOutputDevice"]
    )
    if default_speakers.get("isLoopbackDevice"):
        return default_speakers
    for lb in pa.get_loopback_device_info_generator():
        if default_speakers["name"] in lb["name"]:
            return lb
    raise RuntimeError("No WASAPI loopback device found for default speakers.")


def _default_mic(pa: pyaudio.PyAudio) -> dict:
    return pa.get_default_input_device_info()


class AudioSource:
    """Threaded PyAudio capture that resamples to 16 kHz mono float32 frames."""

    def __init__(
        self,
        mode: str = "mic",
        device_index: Optional[int] = None,
        frame_samples: int = FRAME_SAMPLES,
    ):
        assert mode in ("mic", "loopback")
        self.mode = mode
        self.device_index = device_index
        self.frame_samples = frame_samples
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._native_rate: int = TARGET_RATE
        self._native_channels: int = 1
        self._buf = np.zeros(0, dtype=np.float32)
        self._level: float = 0.0  # smoothed RMS of most recent audio (0..1)

    def start(self) -> None:
        self._pa = pyaudio.PyAudio()
        if self.device_index is not None:
            info = self._pa.get_device_info_by_index(self.device_index)
        elif self.mode == "loopback":
            info = _default_loopback(self._pa)
        else:
            info = _default_mic(self._pa)

        self._native_rate = int(info["defaultSampleRate"])
        self._native_channels = min(int(info["maxInputChannels"]), 2)

        # Block size in native samples so we get ~FRAME_MS of audio per callback.
        native_block = max(1, int(self._native_rate * FRAME_MS / 1000))

        self._stream = self._pa.open(
            format=pyaudio.paFloat32,
            channels=self._native_channels,
            rate=self._native_rate,
            input=True,
            input_device_index=int(info["index"]),
            frames_per_buffer=native_block,
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    def _callback(self, in_data, frame_count, time_info, status):
        try:
            arr = np.frombuffer(in_data, dtype=np.float32)
            if self._native_channels > 1:
                arr = arr.reshape(-1, self._native_channels).mean(axis=1)
            # Resample to 16 kHz via linear interp (good enough for speech).
            if self._native_rate != TARGET_RATE:
                n_out = int(round(len(arr) * TARGET_RATE / self._native_rate))
                if n_out > 0:
                    x_old = np.linspace(0, 1, num=len(arr), endpoint=False)
                    x_new = np.linspace(0, 1, num=n_out, endpoint=False)
                    arr = np.interp(x_new, x_old, arr).astype(np.float32)

            # Compute a smoothed RMS level on the raw (pre-queue) audio so the
            # level meter keeps moving even while paused — that way the user
            # can verify the mic is still live.
            if arr.size:
                rms = float(np.sqrt(np.mean(arr * arr)))
                self._level = 0.8 * self._level + 0.2 * rms

            if self._paused.is_set():
                # Drain internal buffer so we don't emit a burst of stale audio
                # on resume.
                self._buf = np.zeros(0, dtype=np.float32)
                return (None, pyaudio.paContinue)

            self._buf = np.concatenate([self._buf, arr])
            while len(self._buf) >= self.frame_samples:
                frame = self._buf[: self.frame_samples].copy()
                self._buf = self._buf[self.frame_samples :]
                try:
                    self._q.put_nowait(frame)
                except queue.Full:
                    try:
                        self._q.get_nowait()
                    except queue.Empty:
                        pass
                    self._q.put_nowait(frame)
        except Exception as exc:  # pragma: no cover
            print(f"[audio] callback error: {exc}")
        return (None, pyaudio.paContinue)

    def frames(self) -> Iterator[np.ndarray]:
        while not self._stop.is_set():
            try:
                yield self._q.get(timeout=0.5)
            except queue.Empty:
                continue

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.pause()
        else:
            self.resume()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def level(self) -> float:
        return self._level

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="List input devices and exit")
    parser.add_argument("--mode", choices=["mic", "loopback"], default="mic")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--device", type=int, default=None)
    args = parser.parse_args()

    if args.list:
        for d in list_devices():
            tag = " [loopback]" if d.is_loopback else ""
            print(f"{d.index:>3}  {d.name}{tag}  ({d.channels}ch @ {d.rate}Hz)")
        return

    src = AudioSource(mode=args.mode, device_index=args.device)
    src.start()
    print(f"Capturing {args.seconds}s from {args.mode}...")
    t_end = time.time() + args.seconds
    total = 0
    peak = 0.0
    for frame in src.frames():
        total += len(frame)
        peak = max(peak, float(np.max(np.abs(frame))))
        if time.time() >= t_end:
            break
    src.stop()
    print(f"Got {total} samples ({total / TARGET_RATE:.2f}s). Peak amplitude: {peak:.3f}")


if __name__ == "__main__":
    _main()
