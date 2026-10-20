"""Deterministic ASR stream for tests and dry runs."""
from __future__ import annotations

import asyncio
import time

import numpy as np

from rti.asr.base import AsrConfig, AsrEvent, AsrStream


class FakeAsrStream(AsrStream):
    """Emit one final event for each ``segment_seconds`` of audio."""

    name = "fake-asr"

    def __init__(self, config: AsrConfig | None = None, segments: list[str] | None = None) -> None:
        super().__init__(config)
        self.segments = list(segments) if segments is not None else ["第一段", "第二段"]
        self.segment_seconds = float(self.config.extra.get("segment_seconds", 0.5))
        self.latency_s = float(self.config.extra.get("latency_s", 0.0))
        self._buffer = np.zeros(0, dtype=np.float32)
        self._index = 0

    async def _start(self) -> None:
        return None

    async def _send_audio(self, pcm: np.ndarray) -> None:
        self._buffer = np.concatenate([self._buffer, pcm])
        need = max(1, int(self.segment_seconds * self.config.sample_rate))
        while self._index < len(self.segments) and self._buffer.size >= need:
            self._buffer = self._buffer[need:]
            index = self._index
            self._index += 1
            speech_end_ts = time.monotonic()
            if self.latency_s > 0:
                await asyncio.sleep(self.latency_s)
            await self._emit(
                AsrEvent(
                    text=self.segments[index],
                    is_final=True,
                    segment_id=f"seg-{index + 1}",
                    speech_end_ts=speech_end_ts,
                )
            )

    async def _stop(self) -> None:
        return None
