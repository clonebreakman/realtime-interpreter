"""Provider contract shared by every cloud vendor adapter.

The pipeline only depends on this module.  Vendor adapters translate their
wire protocol into ``ProviderEvent`` values; they never leak vendor types into
the rest of the application.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

import numpy as np


class EventKind(str, Enum):
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    TRANSLATION_PARTIAL = "translation_partial"
    TRANSLATION_FINAL = "translation_final"
    SPEECH_STOPPED = "speech_stopped"
    AUDIO = "audio"
    ERROR = "error"


@dataclass(slots=True)
class ProviderEvent:
    kind: EventKind
    text: str | None = None
    audio: np.ndarray | None = None
    sample_rate: int | None = None
    timestamp: float = field(default_factory=time.monotonic)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderConfig:
    source_language: str = "zh-CN"
    target_language: str = "en-US"
    voice_id: str | None = None
    sample_rate: int = 24000
    model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Raised when a provider cannot start, stream, or stop."""


class RealtimeProvider(ABC):
    """Template for a streaming speech-to-speech vendor.

    Subclasses implement ``_start``, ``_send_audio`` and ``_stop``.  The base
    class owns lifecycle validation and the event queue so every adapter has
    identical semantics.
    """

    name = "base"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig()
        self._queue: asyncio.Queue[ProviderEvent | None] = asyncio.Queue()
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
            raise ProviderError("provider is closed")
        if self._started:
            return
        await self._start()
        self._started = True
        await self._emit(ProviderEvent(EventKind.SESSION_STARTED))

    async def send_audio(self, pcm: np.ndarray) -> None:
        if not self._started:
            raise ProviderError("provider not started")
        if self._closed:
            raise ProviderError("provider is closed")
        samples = np.asarray(pcm, dtype=np.float32)
        if samples.ndim != 1:
            raise ProviderError("pcm must be mono (1-D float32)")
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
            await self._queue.put(ProviderEvent(EventKind.SESSION_ENDED))
            await self._queue.put(None)

    async def events(self) -> AsyncIterator[ProviderEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def _emit(self, event: ProviderEvent) -> None:
        await self._queue.put(event)

    @abstractmethod
    async def _start(self) -> None:
        """Open the vendor connection."""

    @abstractmethod
    async def _send_audio(self, pcm: np.ndarray) -> None:
        """Send one mono float32 block to the vendor."""

    @abstractmethod
    async def _stop(self) -> None:
        """Flush and close the vendor connection."""

    async def __aenter__(self) -> "RealtimeProvider":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
