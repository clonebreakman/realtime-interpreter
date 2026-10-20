"""Deterministic in-process provider used for tests and dry runs."""
from __future__ import annotations

import asyncio
import time

import numpy as np

from rti.providers.base import EventKind, ProviderConfig, ProviderEvent, RealtimeProvider


class MockProvider(RealtimeProvider):
    """Emits partial transcripts, a final translation and a short test tone.

    ``config.extra`` keys:
    - ``partial_every_s``: seconds of audio per partial transcript (default 0.5)
    - ``audio_duration_s``: length of the emitted tone (default 0.4)
    - ``latency_s``: artificial delay before the first audio event (default 0)
    """

    name = "mock"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        extra = self.config.extra
        self.partial_every_s = float(extra.get("partial_every_s", 0.5))
        self.audio_duration_s = float(extra.get("audio_duration_s", 0.4))
        self.latency_s = float(extra.get("latency_s", 0.0))
        self._buffer = np.zeros(0, dtype=np.float32)
        self._chunks = 0

    async def _start(self) -> None:
        return None

    async def _send_audio(self, pcm: np.ndarray) -> None:
        self._buffer = np.concatenate([self._buffer, pcm])
        step = max(1, int(self.partial_every_s * self.config.sample_rate))
        while self._buffer.size >= step:
            self._buffer = self._buffer[step:]
            self._chunks += 1
            await self._emit(
                ProviderEvent(
                    EventKind.TRANSCRIPT_PARTIAL,
                    text=f"第{self._chunks}段",
                    meta={"speech_end_ts": time.monotonic()},
                )
            )

    async def _stop(self) -> None:
        if self._chunks or self._buffer.size:
            self._chunks += 1
            text = f"第{self._chunks}段"
        else:
            text = ""
        await self._emit(
            ProviderEvent(
                EventKind.TRANSCRIPT_FINAL,
                text=text,
                meta={"speech_end_ts": time.monotonic(), "segment_id": "seg-1"},
            )
        )
        await self._emit(
            ProviderEvent(EventKind.TRANSLATION_FINAL, text=f"Mock translation of {text}")
        )
        if self.latency_s > 0:
            await asyncio.sleep(self.latency_s)
        await self._emit(
            ProviderEvent(
                EventKind.AUDIO,
                audio=self._tone(),
                sample_rate=self.config.sample_rate,
            )
        )

    def _tone(self) -> np.ndarray:
        n = max(1, int(self.audio_duration_s * self.config.sample_rate))
        t = np.arange(n, dtype=np.float32) / float(self.config.sample_rate)
        return (0.1 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
