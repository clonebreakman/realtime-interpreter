"""Test Omni server-VAD streaming (no manual commit)."""
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


def read_wav(path: Path, target_sr: int = 16000) -> np.ndarray:
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


async def run(args: argparse.Namespace) -> int:
    audio = read_wav(args.wav)
    omni = OmniConfig(
        api_key=args.api_key,
        workspace_id=args.workspace,
        voice=args.voice,
        manual=False,
    )
    provider = OmniRealtimeProvider(ProviderConfig(), omni=omni)
    chunks: list[np.ndarray] = []
    texts: list[str] = []
    first_audio_ms: float | None = None
    started = time.monotonic()
    await provider.start()
    try:
        chunk = 320  # 20ms at 16k
        for offset in range(0, audio.size, chunk):
            await provider.send_audio(audio[offset : offset + chunk])
            await asyncio.sleep(0.02)
        speech_end = time.monotonic()
        print(f"uploaded {audio.size / 16000:.2f}s, waiting for VAD response...")
        async with asyncio.timeout(args.timeout):
            async for event in provider.events():
                if event.kind == EventKind.AUDIO and event.audio is not None:
                    if first_audio_ms is None:
                        first_audio_ms = (time.monotonic() - speech_end) * 1000
                    chunks.append(event.audio)
                elif event.kind in (
                    EventKind.TRANSLATION_PARTIAL,
                    EventKind.TRANSCRIPT_PARTIAL,
                ):
                    if event.text:
                        texts.append(event.text)
                elif event.kind == EventKind.ERROR:
                    print("error:", event.text)
                    return 2
                elif event.kind == EventKind.TRANSLATION_FINAL:
                    break
    except TimeoutError:
        print("timeout waiting for VAD response")
    finally:
        await provider.stop()
    print("first_audio_after_upload_ms", None if first_audio_ms is None else round(first_audio_ms))
    print("audio_chunks", len(chunks), "text", "".join(texts))
    return 0 if chunks else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Omni server-VAD streaming test")
    parser.add_argument("--wav", type=Path, default=Path("fixtures/zh_test2.wav"))
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
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
