"""VAD-gated streaming transcription.

Pipeline:
  AudioSource (512-sample float32 frames @ 16 kHz)
    -> Silero VAD per frame
    -> accumulate frames while speech is active
    -> on trailing silence > N ms OR utterance > MAX_UTTERANCE_S, flush
    -> faster-whisper transcribes the utterance
    -> yield TranscriptSegment
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np

from .audio import AudioSource, FRAME_MS, FRAME_SAMPLES, TARGET_RATE

# Tuning
VAD_THRESHOLD = 0.5
MIN_SILENCE_MS = 350      # default silence needed to flush an utterance (overridable per preset)
MIN_UTTERANCE_MS = 400    # drop fragments shorter than this
MAX_UTTERANCE_S = 10.0    # force-flush after this long
PRE_ROLL_MS = 200         # keep this much audio before detected speech start
INTERIM_EVERY_MS = 1500   # emit a provisional transcript every N ms during speech
INTERIM_MIN_MS = 800      # don't emit interims until the utterance is this long


# Model presets. Each preset picks the fast "interim" model and the accurate
# "final" model together, plus the trailing-silence flush window (lower = the
# committed line appears sooner). Models stay in the same multilingual family so
# language detection keeps working across a live switch. Tuned for an NVIDIA GPU;
# on CPU the heavy models are clamped down (see _resolve_preset).
PRESETS: dict[str, dict] = {
    "fastest": {
        "label": "⚡ Fastest",
        "description": "Lowest latency, looser accuracy. tiny interim / small final, 250 ms flush.",
        "interim": "tiny",
        "final": "small",
        "min_silence_ms": 250,
    },
    "balanced": {
        "label": "⚖️ Balanced",
        "description": "Near-large accuracy, fast on GPU. small interim / distil-large-v3 final.",
        "interim": "small",
        "final": "distil-large-v3",
        "min_silence_ms": 350,
    },
    "accurate": {
        "label": "🎯 Most Accurate",
        "description": "Best transcription, slightly higher latency. small interim / large-v3 final.",
        "interim": "small",
        "final": "large-v3",
        "min_silence_ms": 350,
    },
}
DEFAULT_PRESET = "fastest"

# Models too heavy to run at usable speed on CPU — substituted with light ones.
_CPU_HEAVY = {"distil-large-v3", "large-v3", "large-v2", "large-v1", "medium"}


def _resolve_preset(preset_key: str, has_cuda: bool) -> tuple[str, str, int]:
    """Resolve a preset key to concrete (final, interim, min_silence_ms),
    clamping heavy models down to small/base when there's no GPU."""
    p = PRESETS.get(preset_key, PRESETS[DEFAULT_PRESET])
    final_name, interim_name = p["final"], p["interim"]
    if not has_cuda:
        if final_name in _CPU_HEAVY:
            final_name = "small"
        if interim_name in _CPU_HEAVY or interim_name == "small":
            interim_name = "base"
    return final_name, interim_name, int(p["min_silence_ms"])


@dataclass
class TranscriptSegment:
    id: str
    t0: float
    t1: float
    text: str
    lang: str = "en"
    kind: str = "final"  # "final" or "interim"


def _load_vad():
    # silero-vad >= 5.0 provides load_silero_vad()
    try:
        from silero_vad import load_silero_vad  # type: ignore
        return load_silero_vad()
    except Exception:
        import torch  # type: ignore
        model, _ = torch.hub.load(  # type: ignore
            "snakers4/silero-vad", "silero_vad", trust_repo=True
        )
        return model


def _add_nvidia_dll_dirs() -> None:
    """On Windows, ctranslate2 needs cuBLAS/cuDNN DLLs on the DLL search path.
    The pip-installed nvidia-* wheels put them in site-packages/nvidia/*/bin."""
    if os.name != "nt":
        return
    try:
        import site
        roots: list[str] = []
        for sp in site.getsitepackages() + [site.getusersitepackages()]:
            roots.append(os.path.join(sp, "nvidia"))
        for root in roots:
            if not os.path.isdir(root):
                continue
            for sub in os.listdir(root):
                bin_dir = os.path.join(root, sub, "bin")
                if os.path.isdir(bin_dir):
                    try:
                        os.add_dll_directory(bin_dir)
                    except OSError:
                        pass
    except Exception as exc:
        print(f"[stt] DLL directory setup warning: {exc}")


def _cuda_available() -> bool:
    """True if a usable NVIDIA GPU is present. Wrapped so a missing/broken torch
    CUDA build never crashes startup — we just fall back to CPU."""
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _resolve_device_defaults() -> tuple[str, str, bool]:
    """Choose device + compute type from the hardware, honoring env overrides.

    Mixed audience: if no NVIDIA GPU is found we run on CPU so the app still
    works (just slower) instead of crashing on a missing CUDA runtime."""
    has_cuda = _cuda_available()
    device = os.getenv("WHISPER_DEVICE") or ("cuda" if has_cuda else "cpu")
    compute = os.getenv("WHISPER_COMPUTE_TYPE")
    if not compute:
        compute = "int8_float16" if device == "cuda" else "int8"
    return device, compute, has_cuda


def _load_one_whisper(model_name: str, label: str, device: str, compute: str):
    from faster_whisper import WhisperModel  # type: ignore
    print(f"[stt] loading {label} faster-whisper {model_name} on {device} ({compute})...")
    t0 = time.time()
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
    except Exception as exc:
        print(f"[stt] {label} {device} load failed ({exc}); falling back to CPU int8")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    print(f"[stt] {label} ready in {time.time() - t0:.1f}s")
    # Warm-up pass — first inference pays a large cuDNN/JIT cost; run a dummy
    # silent clip at startup so the first real utterance feels instant.
    try:
        t_warm = time.time()
        silent = np.zeros(TARGET_RATE, dtype=np.float32)  # 1 second of silence
        list(model.transcribe(silent, task="transcribe", beam_size=1)[0])
        print(f"[stt] {label} warm-up done in {time.time() - t_warm:.1f}s")
    except Exception as exc:
        print(f"[stt] {label} warm-up skipped: {exc}")
    return model


def _load_whisper():
    _add_nvidia_dll_dirs()
    device, compute, has_cuda = _resolve_device_defaults()
    preset_key = os.getenv("WHISPER_PRESET", DEFAULT_PRESET)
    if preset_key not in PRESETS:
        preset_key = DEFAULT_PRESET
    final_name, interim_name, min_silence_ms = _resolve_preset(preset_key, has_cuda)
    # Env overrides still win for power users who want a specific model.
    final_name = os.getenv("WHISPER_MODEL", final_name)
    interim_name = os.getenv("WHISPER_INTERIM_MODEL", interim_name)
    final_model = _load_one_whisper(final_name, "final", device, compute)
    if interim_name and interim_name != final_name:
        interim_model = _load_one_whisper(interim_name, "interim", device, compute)
    else:
        interim_model = final_model
        interim_name = final_name
    return preset_key, final_name, interim_name, min_silence_ms, final_model, interim_model


class STTPipeline:
    def __init__(self, source: AudioSource):
        self.source = source
        self._vad = _load_vad()
        # Guards model-reference swaps so a live preset switch never tears a
        # transcribe call (run loop reads under the lock, swap writes under it).
        self._model_lock = threading.Lock()
        (
            self.preset,
            self.final_model_name,
            self.interim_model_name,
            self._min_silence_ms,
            self._whisper,
            self._whisper_interim,
        ) = _load_whisper()
        import torch  # type: ignore
        self._torch = torch

    def set_models(self, final_name: str, interim_name: str, min_silence_ms: Optional[int] = None) -> None:
        """Load the requested models (reusing any already-loaded by name) and
        atomically swap them in. Slow loads happen outside the lock; only the
        reference swap is locked, so transcription keeps running during the load."""
        device, compute, _ = _resolve_device_defaults()

        def _get(name: str) -> object:
            # Reuse an in-memory model if its name already matches.
            if name == self.final_model_name and self._whisper is not None:
                return self._whisper
            if name == self.interim_model_name and self._whisper_interim is not None:
                return self._whisper_interim
            return _load_one_whisper(name, "switch", device, compute)

        new_final = _get(final_name)
        new_interim = new_final if interim_name == final_name else _get(interim_name)

        with self._model_lock:
            self._whisper = new_final
            self._whisper_interim = new_interim
            self.final_model_name = final_name
            self.interim_model_name = interim_name
            if min_silence_ms is not None:
                self._min_silence_ms = min_silence_ms

    def apply_preset(self, preset_key: str) -> None:
        if preset_key not in PRESETS:
            raise ValueError(f"unknown preset: {preset_key}")
        _, _, has_cuda = _resolve_device_defaults()
        final_name, interim_name, min_silence_ms = _resolve_preset(preset_key, has_cuda)
        self.set_models(final_name, interim_name, min_silence_ms)
        self.preset = preset_key

    def _vad_prob(self, frame: np.ndarray) -> float:
        # Silero v5: model expects 1D tensor of length 512 at 16 kHz.
        t = self._torch.from_numpy(frame)
        with self._torch.no_grad():
            return float(self._vad(t, TARGET_RATE).item())

    def _transcribe(self, audio: np.ndarray, interim: bool = False) -> tuple[str, str]:
        # Transcribe verbatim in the spoken language (no translation to English).
        # Grab the model reference under the lock so a concurrent preset switch
        # can't swap it mid-selection; the call itself runs outside the lock.
        with self._model_lock:
            model = self._whisper_interim if interim else self._whisper
        segments, info = model.transcribe(
            audio,
            task="transcribe",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        lang = getattr(info, "language", "en") or "en"
        return text, lang

    def run(self) -> Iterator[TranscriptSegment]:
        pre_roll_frames = PRE_ROLL_MS // FRAME_MS
        min_utt_frames = MIN_UTTERANCE_MS // FRAME_MS
        max_utt_frames = int(MAX_UTTERANCE_S * 1000) // FRAME_MS
        interim_every_frames = INTERIM_EVERY_MS // FRAME_MS
        interim_min_frames = INTERIM_MIN_MS // FRAME_MS

        pre_roll: list[np.ndarray] = []
        current: list[np.ndarray] = []
        in_speech = False
        silence_run = 0
        utt_start_ts: float = 0.0
        utt_id: str = ""
        frames_since_interim = 0
        frame_idx = 0  # wall-clock frame count since start

        start_wall = time.time()

        for frame in self.source.frames():
            frame_idx += 1
            prob = self._vad_prob(frame)
            is_speech = prob >= VAD_THRESHOLD

            if not in_speech:
                # Maintain a rolling pre-roll buffer.
                pre_roll.append(frame)
                if len(pre_roll) > pre_roll_frames:
                    pre_roll.pop(0)
                if is_speech:
                    in_speech = True
                    silence_run = 0
                    utt_start_ts = start_wall + (frame_idx - len(pre_roll)) * FRAME_MS / 1000
                    utt_id = uuid.uuid4().hex[:8]
                    frames_since_interim = 0
                    current = list(pre_roll)
                    current.append(frame)
                    pre_roll = []
            else:
                current.append(frame)
                frames_since_interim += 1
                if is_speech:
                    silence_run = 0
                else:
                    silence_run += 1

                # Read live so a preset switch changes the flush window mid-run.
                silence_frames_to_flush = max(1, self._min_silence_ms // FRAME_MS)
                should_flush = (
                    silence_run >= silence_frames_to_flush
                    or len(current) >= max_utt_frames
                )

                # Emit an interim transcript every ~INTERIM_EVERY_MS while speech
                # continues, as long as we have enough audio to be worth it and
                # we're not about to flush anyway.
                if (
                    not should_flush
                    and frames_since_interim >= interim_every_frames
                    and len(current) >= interim_min_frames
                ):
                    frames_since_interim = 0
                    try:
                        audio = np.concatenate(current)
                        text, lang = self._transcribe(audio, interim=True)
                    except Exception as exc:
                        print(f"[stt] interim transcribe error: {exc}")
                        text, lang = "", "en"
                    if text:
                        t1 = start_wall + frame_idx * FRAME_MS / 1000
                        yield TranscriptSegment(
                            id=utt_id,
                            t0=utt_start_ts,
                            t1=t1,
                            text=text,
                            lang=lang,
                            kind="interim",
                        )

                if should_flush:
                    if len(current) >= min_utt_frames:
                        audio = np.concatenate(current)
                        t1 = start_wall + frame_idx * FRAME_MS / 1000
                        try:
                            text, lang = self._transcribe(audio)
                        except Exception as exc:
                            print(f"[stt] transcribe error: {exc}")
                            text, lang = "", "en"
                        if text:
                            yield TranscriptSegment(
                                id=utt_id,
                                t0=utt_start_ts,
                                t1=t1,
                                text=text,
                                lang=lang,
                                kind="final",
                            )
                    # Reset
                    in_speech = False
                    silence_run = 0
                    current = []
                    pre_roll = []
                    utt_id = ""
                    frames_since_interim = 0
