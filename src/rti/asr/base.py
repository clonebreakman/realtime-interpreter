"""Streaming ASR contract."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import numpy as np


@dataclass(slots=True)
class AsrConfig:
    sample_rate: int = 16000
    language: str = "zh"
    model: str = "qwen3-asr-flash-realtime"
    endpoint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AsrEvent:
    text: str
    is_final: bool = False
    speech_end_ts: float = field(default_factory=time.monotonic)
    segment_id: str | None = None
    timestamp: float = field(default_factory=time.monotonic)


class AsrError(RuntimeError):
    """Raised when the ASR stream cannot start, send or stop."""


class AsrStream(ABC):
    name = "base"

    def __init__(self, config: AsrConfig | None = None) -> None:
        self.config = config or AsrConfig()
        self._queue: asyncio.Queue[AsrEvent | None] = asyncio.Queue()
        self._started = False
        self._closed = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        if self._closed:
            raise AsrError("asr stream is closed")
        if self._started:
            return
        await self._start()
        self._started = True

    async def send_audio(self, pcm: np.ndarray) -> None:
        if not self._started:
            raise AsrError("asr stream not started")
        if self._closed:
            raise AsrError("asr stream is closed")
        samples = np.asarray(pcm, dtype=np.float32)
        if samples.ndim != 1:
            raise AsrError("pcm must be mono (1-D float32)")
        await self._send_audio(samples)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._started:
            await self._queue.put(None)
            return
        try:
            await self._stop()
        finally:
            self._started = False
            await self._queue.put(None)

    async def events(self) -> AsyncIterator[AsrEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def _emit(self, event: AsrEvent) -> None:
        await self._queue.put(event)

    @abstractmethod
    async def _start(self) -> None:
        """Open the vendor stream."""

    @abstractmethod
    async def _send_audio(self, pcm: np.ndarray) -> None:
        """Send one mono 16k float32 block."""

    @abstractmethod
    async def _stop(self) -> None:
        """Flush and close the vendor stream."""

    async def __aenter__(self) -> "AsrStream":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
