"""Session orchestration: consume provider events, record latency, fan out callbacks."""
from __future__ import annotations

from typing import Callable

import numpy as np

from rti.pipeline.metrics import LatencyRecorder
from rti.providers.base import EventKind, ProviderEvent, RealtimeProvider


class InterpreterSession:
    """Wire one provider into the latency recorder and UI callbacks."""

    def __init__(
        self,
        provider: RealtimeProvider,
        recorder: LatencyRecorder | None = None,
        on_text: Callable[[str], None] | None = None,
        on_audio: Callable[[np.ndarray], None] | None = None,
        on_error: Callable[[ProviderEvent], None] | None = None,
    ) -> None:
        self.provider = provider
        self.recorder = recorder
        self.on_text = on_text
        self.on_audio = on_audio
        self.on_error = on_error
        self.errors: list[ProviderEvent] = []
        self._sequence = 0
        self._active_segment: str | None = None
        self._audio_seen: set[str] = set()

    async def feed(self, pcm: np.ndarray) -> None:
        await self.provider.send_audio(pcm)

    async def run(self) -> None:
        async for event in self.provider.events():
            if event.kind == EventKind.SESSION_ENDED:
                return
            if event.kind == EventKind.ERROR:
                self.errors.append(event)
                if self.on_error is not None:
                    self.on_error(event)
                continue
            if event.kind == EventKind.TRANSCRIPT_FINAL:
                self._sequence += 1
                segment = str(event.meta.get("segment_id") or f"seg-{self._sequence}")
                self._active_segment = segment
                if self.recorder is not None:
                    end = event.meta.get("speech_end_ts", event.timestamp)
                    self.recorder.mark_speech_end(segment, float(end))
                continue
            if event.kind in (EventKind.TRANSLATION_PARTIAL, EventKind.TRANSLATION_FINAL):
                if event.text and self.on_text is not None:
                    self.on_text(event.text)
                continue
            if event.kind == EventKind.AUDIO:
                segment = self._active_segment
                if segment is not None and segment not in self._audio_seen:
                    self._audio_seen.add(segment)
                    if self.recorder is not None:
                        self.recorder.mark_first_audio(segment, event.timestamp)
                if event.audio is not None and self.on_audio is not None:
                    self.on_audio(event.audio)
