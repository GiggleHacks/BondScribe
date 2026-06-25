"""Thread-safe transcript ring buffer."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict
from typing import Iterable

from .stt import TranscriptSegment


class TranscriptBuffer:
    def __init__(self, max_segments: int = 500):
        self._segs: deque[TranscriptSegment] = deque(maxlen=max_segments)
        self._lock = threading.Lock()

    def append(self, seg: TranscriptSegment) -> None:
        with self._lock:
            self._segs.append(seg)

    def all(self) -> list[TranscriptSegment]:
        with self._lock:
            return list(self._segs)

    def recent_text(self, n: int = 20) -> str:
        with self._lock:
            items: Iterable[TranscriptSegment] = list(self._segs)[-n:]
        return "\n".join(f"- {s.text}" for s in items)

    @staticmethod
    def to_dict(seg: TranscriptSegment) -> dict:
        return asdict(seg)
