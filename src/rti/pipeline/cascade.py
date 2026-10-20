"""Cascade orchestration: streaming ASR -> DeepSeek MT -> cloned TTS."""
from __future__ import annotations

import asyncio
import time
from typing import Callable

import numpy as np

from rti.asr.base import AsrEvent, AsrStream
from rti.mt.base import Translator
from rti.pipeline.metrics import StageLatencyRecorder
from rti.pipeline.segmenter import ClauseSegmenter
from rti.tts.base import TtsEngine


class CascadeSession:
    """Wire ASR, translator and TTS with clause-level overlap and barge-in."""

    def __init__(
        self,
        asr: AsrStream,
        translator: Translator,
        tts: TtsEngine,
        recorder: StageLatencyRecorder | None = None,
        on_text: Callable[[str], None] | None = None,
        on_audio: Callable[[np.ndarray], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        context_size: int = 2,
    ) -> None:
        self.asr = asr
        self.translator = translator
        self.tts = tts
        self.recorder = recorder
        self.on_text = on_text
        self.on_audio = on_audio
        self.on_error = on_error
        self.context_size = max(0, context_size)
        self._asr_task: asyncio.Task[None] | None = None
        self._context: list[str] = []
        self._sequence = 0
        self._audio_started: set[str] = set()
        self._cancelled = False

    async def start(self) -> None:
        await self.asr.start()
        await self.tts.start()
        self._cancelled = False
        self._asr_task = asyncio.create_task(self._consume_asr())

    async def feed(self, pcm: np.ndarray) -> None:
        await self.asr.send_audio(pcm)

    async def stop(self) -> None:
        await self.asr.stop()
        if self._asr_task is not None:
            await self._asr_task
            self._asr_task = None
        await self.tts.stop()

    def cancel(self) -> None:
        """Stop producing new translated audio; the current segment is abandoned."""
        self._cancelled = True
        cancel = getattr(self.tts, "cancel", None)
        if callable(cancel):
            cancel()

    async def _consume_asr(self) -> None:
        async for event in self.asr.events():
            if not event.is_final:
                continue
            try:
                await self._handle_final(event)
            except Exception as exc:  # keep the stream alive; report and continue
                if self.on_error is not None:
                    self.on_error(exc)

    async def _handle_final(self, event: AsrEvent) -> None:
        self._sequence += 1
        segment_id = event.segment_id or f"seg-{self._sequence}"
        if self.recorder is not None:
            self.recorder.begin(segment_id, event.speech_end_ts)
            self.recorder.mark(segment_id, "asr_final", event.timestamp)

        text = event.text.strip()
        if not text:
            if self.recorder is not None:
                self.recorder.finish(segment_id)
            return

        segmenter = ClauseSegmenter()
        first_token = False
        context = self._context[-self.context_size :] if self.context_size else None
        async for chunk in self.translator.translate_stream(text, context=context):
            if chunk.text:
                if not first_token:
                    first_token = True
                    if self.recorder is not None:
                        self.recorder.mark(segment_id, "mt_first_token")
                if self.on_text is not None:
                    self.on_text(chunk.text)
            for clause in segmenter.feed(chunk.text):
                await self._speak(segment_id, clause)
        tail = segmenter.flush()
        if tail is not None:
            await self._speak(segment_id, tail)

        self._context.append(text)
        if self.recorder is not None:
            self.recorder.finish(segment_id)

    async def _speak(self, segment_id: str, text: str) -> None:
        if self._cancelled:
            return
        first_chunk = True
        async for chunk in self.tts.synthesize(text):
            if self._cancelled:
                return
            if first_chunk:
                first_chunk = False
                if self.recorder is not None:
                    self.recorder.mark(segment_id, "tts_first_chunk", time.monotonic())
            if self.recorder is not None and segment_id not in self._audio_started:
                self.recorder.mark(segment_id, "first_audio", time.monotonic())
                self._audio_started.add(segment_id)
            if self.on_audio is not None:
                self.on_audio(chunk.audio)
