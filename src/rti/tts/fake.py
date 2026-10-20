"""Deterministic TTS engine for tests and dry runs."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import numpy as np

from rti.tts.base import TtsChunk, TtsConfig, TtsEngine


class FakeTtsEngine(TtsEngine):
    name = "fake-tts"

    def __init__(self, config: TtsConfig | None = None) -> None:
        super().__init__(config)
        self.texts: list[str] = []
        self.started = False
        self.stopped = False
        self.cancelled = False
        self.latency_s = float(self.config.extra.get("latency_s", 0.0))

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def cancel(self) -> None:
        self.cancelled = True

    async def synthesize(self, text: str) -> AsyncIterator[TtsChunk]:
        self.texts.append(text)
        if self.latency_s > 0:
            await asyncio.sleep(self.latency_s)
        for index in range(2):
            if self.cancelled:
                return
            samples = max(1, int(0.05 * self.config.sample_rate))
            yield TtsChunk(
                audio=np.full(samples, 0.01, dtype=np.float32),
                sample_rate=self.config.sample_rate,
                text=text,
                is_final=index == 1,
            )
