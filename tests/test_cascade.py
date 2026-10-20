"""Cascade orchestration: ASR -> DeepSeek-style MT -> cloned TTS."""
from __future__ import annotations

import asyncio

import numpy as np

from rti.asr.base import AsrConfig
from rti.asr.fake import FakeAsrStream
from rti.mt.mock import MockTranslator
from rti.pipeline.cascade import CascadeSession
from rti.pipeline.metrics import StageLatencyRecorder
from rti.tts.base import TtsConfig
from rti.tts.fake import FakeTtsEngine


def test_cascade_translates_and_speaks_two_segments() -> None:
    async def scenario():
        asr = FakeAsrStream(
            AsrConfig(sample_rate=16000, extra={"segment_seconds": 0.5}),
            segments=["你好", "世界"],
        )
        tts = FakeTtsEngine(TtsConfig(sample_rate=24000))
        recorder = StageLatencyRecorder()
        texts: list[str] = []
        audios: list[np.ndarray] = []
        session = CascadeSession(
            asr,
            MockTranslator(),
            tts,
            recorder,
            on_text=texts.append,
            on_audio=audios.append,
        )
        await session.start()
        await session.feed(np.zeros(8000, dtype=np.float32))
        await session.feed(np.zeros(8000, dtype=np.float32))
        await session.stop()
        return tts, recorder, texts, audios

    tts, recorder, texts, audios = asyncio.run(scenario())
    assert tts.texts == ["Hello from mock", "Hello from mock"]
    assert len(recorder.samples) == 2
    assert all(sample.latency_ms() is not None for sample in recorder.samples)
    assert len(texts) == 6
    assert len(audios) == 4
    assert tts.stopped is True


def test_cascade_skips_empty_asr_and_stops_cleanly() -> None:
    async def scenario():
        asr = FakeAsrStream(
            AsrConfig(sample_rate=16000, extra={"segment_seconds": 0.5}),
            segments=["", "世界"],
        )
        tts = FakeTtsEngine(TtsConfig(sample_rate=24000))
        recorder = StageLatencyRecorder()
        texts: list[str] = []
        session = CascadeSession(asr, MockTranslator(), tts, recorder, on_text=texts.append)
        await session.start()
        await session.feed(np.zeros(8000, dtype=np.float32))
        await session.feed(np.zeros(8000, dtype=np.float32))
        await session.stop()
        return tts, recorder, texts

    tts, recorder, texts = asyncio.run(scenario())
    assert tts.texts == ["Hello from mock"]
    assert len(recorder.samples) == 1
    assert len(texts) == 3
