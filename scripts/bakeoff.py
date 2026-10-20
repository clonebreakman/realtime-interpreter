"""Phase 0 bake-off runner.

Examples:
    python scripts/bakeoff.py --list-providers
    python scripts/bakeoff.py --provider mock --duration 3
    python scripts/bakeoff.py --provider aliyun --wav fixtures/zh_30s.wav
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rti.asr.base import AsrConfig  # noqa: E402
from rti.asr.fake import FakeAsrStream  # noqa: E402
from rti.audio.resample import resample_linear  # noqa: E402
from rti.mt.mock import MockTranslator  # noqa: E402
from rti.pipeline.cascade import CascadeSession  # noqa: E402
from rti.pipeline.cost import session_cost_usd  # noqa: E402
from rti.pipeline.metrics import LatencyRecorder, StageLatencyRecorder  # noqa: E402
from rti.pipeline.session import InterpreterSession  # noqa: E402
from rti.providers.base import ProviderConfig, ProviderError  # noqa: E402
from rti.providers.registry import available_providers, create_provider  # noqa: E402
from rti.tts.base import TtsConfig  # noqa: E402
from rti.tts.fake import FakeTtsEngine  # noqa: E402


def load_wav(path: Path, target_sr: int) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return resample_linear(data, rate, target_sr)


def make_tone(duration_s: float, sample_rate: int) -> np.ndarray:
    n = max(1, int(duration_s * sample_rate))
    t = np.arange(n, dtype=np.float32) / float(sample_rate)
    return (0.1 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)


async def run_once(
    provider_name: str,
    samples: np.ndarray,
    sample_rate: int,
    chunk_ms: int,
    realtime: bool,
) -> tuple[LatencyRecorder, list[str], int]:
    provider = create_provider(
        provider_name,
        ProviderConfig(
            sample_rate=sample_rate,
            extra={"partial_every_s": 0.5, "audio_duration_s": 0.4, "latency_s": 0.15},
        ),
    )
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
    chunk = max(1, int(sample_rate * chunk_ms / 1000))
    for offset in range(0, samples.size, chunk):
        await session.feed(samples[offset : offset + chunk])
        if realtime:
            await asyncio.sleep(chunk_ms / 1000.0)
    await provider.stop()
    await asyncio.wait_for(task, 10.0)
    return recorder, texts, len(audios)


async def run_cascade(
    samples: np.ndarray,
    sample_rate: int,
    chunk_ms: int,
    realtime: bool,
    segment_seconds: float,
) -> tuple[StageLatencyRecorder, list[str], int, FakeTtsEngine]:
    asr_rate = 16000
    asr_samples = resample_linear(samples, sample_rate, asr_rate)
    asr = FakeAsrStream(
        AsrConfig(
            sample_rate=asr_rate,
            extra={"segment_seconds": segment_seconds, "latency_s": 0.15},
        ),
        segments=["第一段测试", "第二段测试", "第三段测试"],
    )
    tts = FakeTtsEngine(TtsConfig(sample_rate=24000, extra={"latency_s": 0.10}))
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
    chunk = max(1, int(asr_rate * chunk_ms / 1000))
    for offset in range(0, asr_samples.size, chunk):
        await session.feed(asr_samples[offset : offset + chunk])
        if realtime:
            await asyncio.sleep(chunk_ms / 1000.0)
    await session.stop()
    return recorder, texts, len(audios), tts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud provider latency bake-off")
    parser.add_argument("--system", choices=["e2e", "cascade"], default="e2e")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--chunk-ms", type=int, default=40)
    parser.add_argument("--segment-seconds", type=float, default=0.5)
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "bakeoff.jsonl")
    parser.add_argument("--no-realtime", action="store_true")
    parser.add_argument("--list-providers", action="store_true")
    args = parser.parse_args(argv)

    if args.list_providers:
        for name in available_providers():
            print(name)
        return 0

    samples = (
        load_wav(args.wav, args.sample_rate)
        if args.wav
        else make_tone(args.duration, args.sample_rate)
    )
    started = time.monotonic()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.system == "cascade":
        recorder, texts, audio_count, tts = asyncio.run(
            run_cascade(
                samples,
                args.sample_rate,
                args.chunk_ms,
                realtime=not args.no_realtime,
                segment_seconds=args.segment_seconds,
            )
        )
        recorder.to_jsonl(args.out)
        tts_chars = sum(len(text) for text in tts.texts)
        output_tokens = max(1, int(tts_chars / 4))
        input_tokens = max(1, int(sum(len(text) for text in texts) / 3))
        cost = session_cost_usd(
            asr_seconds=samples.size / args.sample_rate,
            tts_chars=tts_chars,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        result = {
            "system": "cascade",
            "provider": "cascade-fake",
            "audio_seconds": round(samples.size / args.sample_rate, 3),
            "wall_seconds": round(time.monotonic() - started, 3),
            "latency": recorder.summary(),
            "text_events": len(texts),
            "audio_events": audio_count,
            "tts_chars": tts_chars,
            "cost_usd_estimate": round(cost, 6),
            "out": str(args.out),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    try:
        recorder, texts, audio_count = asyncio.run(
            run_once(
                args.provider,
                samples,
                args.sample_rate,
                args.chunk_ms,
                realtime=not args.no_realtime,
            )
        )
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    recorder.to_jsonl(args.out)
    result = {
        "system": "e2e",
        "provider": args.provider,
        "audio_seconds": round(samples.size / args.sample_rate, 3),
        "wall_seconds": round(time.monotonic() - started, 3),
        "latency": recorder.summary(),
        "text_events": len(texts),
        "audio_events": audio_count,
        "out": str(args.out),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
