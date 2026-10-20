"""Core contract tests: provider lifecycle, mock pipeline, latency metrics, config, resampling."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from rti.audio.resample import resample_linear
from rti.config import Settings
from rti.pipeline.metrics import LatencyRecorder
from rti.pipeline.session import InterpreterSession
from rti.providers.base import EventKind, ProviderConfig, ProviderError, RealtimeProvider
from rti.providers.mock import MockProvider
from rti.providers.registry import available_providers, create_provider


class DummyProvider(RealtimeProvider):
    name = "dummy"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)
        self.started_flag = False
        self.sent: list[np.ndarray] = []

    async def _start(self) -> None:
        self.started_flag = True

    async def _send_audio(self, pcm: np.ndarray) -> None:
        self.sent.append(pcm)

    async def _stop(self) -> None:
        self.started_flag = False


def test_send_before_start_raises() -> None:
    provider = DummyProvider()
    with pytest.raises(ProviderError, match="not started"):
        asyncio.run(provider.send_audio(np.zeros(8, dtype=np.float32)))


def test_start_is_idempotent_and_stop_closes_stream() -> None:
    async def scenario() -> tuple[DummyProvider, list]:
        provider = DummyProvider()
        await provider.start()
        await provider.start()
        events: list = []

        async def consume() -> None:
            async for event in provider.events():
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await provider.stop()
        await asyncio.wait_for(task, 1)
        return provider, events

    provider, events = asyncio.run(scenario())
    assert provider.started is False
    assert [event.kind for event in events] == [
        EventKind.SESSION_STARTED,
        EventKind.SESSION_ENDED,
    ]


def test_send_rejects_non_mono_audio() -> None:
    async def scenario() -> None:
        provider = DummyProvider()
        await provider.start()
        with pytest.raises(ProviderError, match="mono"):
            await provider.send_audio(np.zeros((2, 8), dtype=np.float32))
        await provider.stop()

    asyncio.run(scenario())


def test_mock_emits_partial_final_translation_and_audio() -> None:
    async def scenario() -> list:
        config = ProviderConfig(
            extra={"partial_every_s": 0.25, "audio_duration_s": 0.1, "latency_s": 0.0}
        )
        provider = MockProvider(config)
        await provider.start()
        events: list = []

        async def consume() -> None:
            async for event in provider.events():
                events.append(event)

        task = asyncio.create_task(consume())
        await provider.send_audio(np.zeros(int(24000 * 0.6), dtype=np.float32))
        await provider.stop()
        await asyncio.wait_for(task, 2)
        return events

    events = asyncio.run(scenario())
    kinds = [event.kind for event in events]
    assert kinds.count(EventKind.TRANSCRIPT_PARTIAL) == 2
    assert EventKind.TRANSCRIPT_FINAL in kinds
    assert EventKind.TRANSLATION_FINAL in kinds
    audio = [event for event in events if event.kind == EventKind.AUDIO]
    assert len(audio) == 1
    assert audio[0].sample_rate == 24000
    assert audio[0].audio is not None
    assert audio[0].audio.dtype == np.float32


def test_latency_sample_and_summary() -> None:
    recorder = LatencyRecorder()
    recorder.mark_speech_end("a", 10.0)
    sample = recorder.mark_first_audio("a", 11.2)
    assert sample is not None
    assert sample.latency_ms == pytest.approx(1200.0)

    recorder.mark_speech_end("b", 20.0)
    recorder.mark_first_audio("b", 21.0)
    summary = recorder.summary()
    assert summary["count"] == 2
    assert summary["p50_ms"] == pytest.approx(1100.0)
    assert summary["min_ms"] == pytest.approx(1000.0)
    assert summary["max_ms"] == pytest.approx(1200.0)


def test_first_audio_without_speech_end_returns_none() -> None:
    assert LatencyRecorder().mark_first_audio("missing") is None


def test_latency_to_jsonl(tmp_path) -> None:
    recorder = LatencyRecorder()
    recorder.mark_speech_end("a", 10.0)
    recorder.mark_first_audio("a", 11.2)
    path = tmp_path / "latency.jsonl"
    recorder.to_jsonl(path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "segment_id": "a",
            "speech_end_ts": 10.0,
            "first_audio_ts": 11.2,
            "latency_ms": pytest.approx(1200.0),
        }
    ]


def test_settings_defaults_and_env_override() -> None:
    defaults = Settings.from_env({})
    assert defaults.provider == "mock"
    assert defaults.source_language == "zh-CN"
    assert defaults.target_language == "en-US"
    assert defaults.sample_rate == 24000
    assert defaults.deepseek_model == "deepseek-flash"

    overridden = Settings.from_env(
        {
            "RTI_PROVIDER": "mock",
            "RTI_TARGET_LANGUAGE": "en-GB",
            "RTI_VOICE_ID": "voice-1",
            "RTI_SAMPLE_RATE": "16000",
            "DEEPSEEK_MODEL": "deepseek-v4-pro",
        }
    )
    assert overridden.target_language == "en-GB"
    assert overridden.voice_id == "voice-1"
    assert overridden.sample_rate == 16000
    assert overridden.deepseek_model == "deepseek-v4-pro"
    assert overridden.provider_config().voice_id == "voice-1"


def test_registry_creates_mock_and_rejects_unimplemented() -> None:
    provider = create_provider("mock")
    assert provider.name == "mock"
    assert "mock" in available_providers()
    assert "aliyun" in available_providers()
    with pytest.raises(ProviderError, match="Phase 0"):
        create_provider("aliyun")
    with pytest.raises(ProviderError, match="unknown"):
        create_provider("does-not-exist")


def test_session_records_latency_and_forwards_callbacks() -> None:
    async def scenario() -> tuple[LatencyRecorder, list[str], list[np.ndarray]]:
        config = ProviderConfig(
            extra={"partial_every_s": 0.25, "audio_duration_s": 0.1, "latency_s": 0.05}
        )
        provider = MockProvider(config)
        recorder = LatencyRecorder()
        texts: list[str] = []
        audios: list[np.ndarray] = []
        session = InterpreterSession(
            provider,
            recorder,
            on_text=texts.append,
            on_audio=audios.append,
        )
        await provider.start()
        task = asyncio.create_task(session.run())
        await session.feed(np.zeros(int(24000 * 0.6), dtype=np.float32))
        await provider.stop()
        await asyncio.wait_for(task, 2)
        return recorder, texts, audios

    recorder, texts, audios = asyncio.run(scenario())
    assert len(recorder.samples) == 1
    assert recorder.samples[0].latency_ms >= 40
    assert any("Mock translation" in text for text in texts)
    assert len(audios) == 1


def test_resample_linear_downsample_length_and_same_rate_copy() -> None:
    source = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000).astype(np.float32)
    downsampled = resample_linear(source, 48000, 24000)
    assert downsampled.dtype == np.float32
    assert abs(downsampled.size - 24000) <= 1

    identity = np.arange(10, dtype=np.float32)
    copied = resample_linear(identity, 16000, 16000)
    assert copied is not identity
    np.testing.assert_array_equal(copied, identity)

    with pytest.raises(ValueError):
        resample_linear(np.zeros(10), 0, 16000)
