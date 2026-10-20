"""Send one Chinese WAV through Qwen3.8-Omni and save the English reply.

Example:
    $env:DASHSCOPE_API_KEY="sk-..."
    $env:BAILIAN_WORKSPACE_ID="ws-..."
    $env:OMNI_VOICE="qwen-omni-vc-..."
    .\.venv\Scripts\python.exe scripts\omni_roundtrip.py --wav zh.wav --out reply.wav
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rti.audio.resample import resample_linear  # noqa: E402
from rti.providers.base import EventKind, ProviderConfig  # noqa: E402
from rti.providers.omni import OmniConfig, OmniRealtimeProvider  # noqa: E402


def read_wav(path: Path, target_sr: int) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return resample_linear(data, rate, target_sr)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


async def run(args: argparse.Namespace) -> int:
    audio = read_wav(args.wav, 16000)
    omni = OmniConfig(
        api_key=args.api_key,
        workspace_id=args.workspace,
        model=args.model,
        voice=args.voice,
        manual=True,
    )
    provider = OmniRealtimeProvider(ProviderConfig(), omni=omni)
    started = time.monotonic()
    first_audio_ms: float | None = None
    speech_end_to_audio_ms: float | None = None
    chunks: list[np.ndarray] = []
    texts: list[str] = []
    await provider.start()
    try:
        chunk = 3200  # 0.2s at 16k mono s16le
        for offset in range(0, audio.size, chunk):
            await provider.send_audio(audio[offset : offset + chunk])
        speech_end = time.monotonic()
        await provider.commit()
        await provider.respond()
        async with asyncio.timeout(args.timeout):
            async for event in provider.events():
                if event.kind == EventKind.AUDIO and event.audio is not None:
                    if first_audio_ms is None:
                        now = time.monotonic()
                        first_audio_ms = (now - started) * 1000
                        speech_end_to_audio_ms = (now - speech_end) * 1000
                    chunks.append(event.audio)
                elif event.kind in (
                    EventKind.TRANSLATION_PARTIAL,
                    EventKind.TRANSCRIPT_PARTIAL,
                ):
                    if event.text:
                        texts.append(event.text)
                elif event.kind == EventKind.ERROR:
                    print(f"error: {event.text}", file=sys.stderr)
                    return 2
                elif event.kind == EventKind.TRANSLATION_FINAL:
                    break
    finally:
        await provider.stop()

    if not chunks:
        print("no audio received", file=sys.stderr)
        return 1
    write_wav(args.out, np.concatenate(chunks), 24000)
    print(f"connect_to_first_audio_ms={first_audio_ms:.0f}" if first_audio_ms else "connect_to_first_audio_ms=unknown")
    print(
        f"speech_end_to_first_audio_ms={speech_end_to_audio_ms:.0f}"
        if speech_end_to_audio_ms
        else "speech_end_to_first_audio_ms=unknown"
    )
    print("text:", "".join(texts))
    print("out:", args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qwen3.8-Omni round-trip")
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("reply.wav"))
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--model", default="qwen3.8-omni-flash-realtime")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    import os

    args.api_key = args.api_key or os.getenv("DASHSCOPE_API_KEY")
    args.workspace = args.workspace or os.getenv("BAILIAN_WORKSPACE_ID")
    args.voice = args.voice or os.getenv("OMNI_VOICE", "Tina")
    if not args.api_key:
        print("DASHSCOPE_API_KEY is required", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
