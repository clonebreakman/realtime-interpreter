"""ASR/TTS contracts, clause segmentation, stage latency and session cost."""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from rti.asr.base import AsrConfig
from rti.asr.fake import FakeAsrStream
from rti.pipeline.cost import omni_cost_cny, session_cost_usd
from rti.pipeline.metrics import StageLatencyRecorder
from rti.pipeline.segmenter import ClauseSegmenter
from rti.tts.base import TtsConfig
from rti.tts.fake import FakeTtsEngine


def test_fake_asr_emits_one_final_per_segment() -> None:
    async def scenario() -> list:
        asr = FakeAsrStream(
            AsrConfig(sample_rate=16000, extra={"segment_seconds": 0.5}),
            segments=["你好", "世界"],
        )
        await asr.start()
        events: list = []

        async def consume() -> None:
            async for event in asr.events():
                events.append(event)

        task = asyncio.create_task(consume())
        await asr.send_audio(np.zeros(8000, dtype=np.float32))
        await asr.send_audio(np.zeros(8000, dtype=np.float32))
        await asr.stop()
        await asyncio.wait_for(task, 1)
        return events

    events = asyncio.run(scenario())
    finals = [event for event in events if event.is_final]
    assert [event.text for event in finals] == ["你好", "世界"]
    assert [event.segment_id for event in finals] == ["seg-1", "seg-2"]


def test_fake_asr_applies_configured_latency() -> None:
    async def scenario() -> float:
        asr = FakeAsrStream(
            AsrConfig(
                sample_rate=16000,
                extra={"segment_seconds": 0.5, "latency_s": 0.05},
            ),
            segments=["x"],
        )
        await asr.start()
        started = time.monotonic()
        await asr.send_audio(np.zeros(8000, dtype=np.float32))
        await asr.stop()
        return time.monotonic() - started

    assert asyncio.run(scenario()) >= 0.04


def test_fake_tts_streams_two_chunks_and_records_text() -> None:
    async def scenario() -> tuple[FakeTtsEngine, list]:
        tts = FakeTtsEngine(TtsConfig(sample_rate=24000))
        await tts.start()
        chunks = [chunk async for chunk in tts.synthesize("Hello")]
        await tts.stop()
        return tts, chunks

    tts, chunks = asyncio.run(scenario())
    assert tts.started is True
    assert tts.stopped is True
    assert tts.texts == ["Hello"]
    assert len(chunks) == 2
    assert chunks[-1].is_final is True
    assert chunks[0].sample_rate == 24000
    assert chunks[0].audio.dtype == np.float32


def test_segmenter_splits_clauses_and_flushes() -> None:
    segmenter = ClauseSegmenter()
    assert segmenter.feed("Hello, ") == ["Hello,"]
    assert segmenter.feed("world. How") == ["world."]
    assert segmenter.feed(" are you?") == ["How are you?"]
    assert segmenter.flush() is None


def test_segmenter_keeps_decimal_and_flushes() -> None:
    segmenter = ClauseSegmenter()
    assert segmenter.feed("pi is 3.14") == []
    assert segmenter.flush() == "pi is 3.14"


def test_stage_latency_recorder() -> None:
    recorder = StageLatencyRecorder()
    recorder.begin("s1", 10.0)
    recorder.mark("s1", "asr_final", 10.3)
    recorder.mark("s1", "mt_first_token", 10.55)
    recorder.mark("s1", "tts_first_chunk", 10.75)
    recorder.mark("s1", "first_audio", 10.9)

    sample = recorder.finish("s1")
    assert sample is not None
    assert sample.latency_ms() == pytest.approx(900.0)
    summary = recorder.summary()
    assert summary["count"] == 1
    assert summary["p50_ms"] == pytest.approx(900.0)

    with pytest.raises(ValueError):
        recorder.mark("s1", "bogus")


def test_stage_latency_without_first_audio_has_no_latency() -> None:
    recorder = StageLatencyRecorder()
    recorder.begin("s2", 1.0)
    sample = recorder.finish("s2")
    assert sample is not None
    assert sample.latency_ms() is None
    assert recorder.summary()["count"] == 0


def test_session_cost_matches_full_chain_hour() -> None:
    cost = session_cost_usd(3600, 45000, 15000, 15000)
    assert cost == pytest.approx(0.70245, rel=1e-4)
    assert session_cost_usd(0, 0, 0, 0) == 0.0
    with pytest.raises(ValueError):
        session_cost_usd(-1, 0, 0, 0)


def test_omni_cost_for_one_hour() -> None:
    # 25 audio tokens/s, CNY 6/M in + 12/M out, plus 15k text output tokens/h.
    assert omni_cost_cny(1.0) == pytest.approx(1.6875, rel=1e-4)
    assert omni_cost_cny(0.0) == 0.0
    with pytest.raises(ValueError):
        omni_cost_cny(-1.0)
