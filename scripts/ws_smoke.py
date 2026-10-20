"""End-to-end smoke test: local gateway -> Omni -> local gateway."""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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
    if rate != target_sr:
        duration = data.size / rate
        target_n = int(duration * target_sr)
        data = np.interp(
            np.linspace(0, 1, target_n, endpoint=False),
            np.linspace(0, 1, data.size, endpoint=False),
            data,
        ).astype(np.float32)
    return data


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = 24000) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WebSocket gateway smoke test")
    parser.add_argument("--uri", default="ws://127.0.0.1:8765/ws")
    parser.add_argument("--wav", type=Path, default=ROOT / "fixtures" / "zh_test2.wav")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "ws_smoke_reply.wav")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--profile", default="self")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    audio = read_wav(args.wav)
    chunks: list[np.ndarray] = []
    texts: list[str] = []
    first_audio_ms: float | None = None
    with connect(args.uri, open_timeout=15, close_timeout=3, max_size=None) as ws:
        ws.send(json.dumps({"type": "start", "voice": args.voice or "", "profile": args.profile}))
        ready = json.loads(ws.recv(timeout=15))
        print("ready:", ready)
        if ready.get("type") == "error":
            return 2
        frame = 320
        for offset in range(0, audio.size, frame):
            pcm = (np.clip(audio[offset : offset + frame], -1, 1) * 32767).astype("<i2")
            ws.send(pcm.tobytes())
            time.sleep(0.02)
        # 真实麦克风在句末会继续送静音；测试音频补 1s 静音让服务端 VAD 收尾。
        silence = np.zeros(16000, dtype=np.float32)
        for offset in range(0, silence.size, frame):
            pcm = silence[offset : offset + frame].astype("<i2")
            ws.send(pcm.tobytes())
            time.sleep(0.02)
        speech_end = time.monotonic()
        while True:
            try:
                message = ws.recv(timeout=args.timeout)
            except TimeoutError:
                print("timeout waiting for response", file=sys.stderr)
                break
            if isinstance(message, bytes):
                if first_audio_ms is None:
                    first_audio_ms = (time.monotonic() - speech_end) * 1000
                chunks.append(
                    np.frombuffer(message, dtype="<i2").astype(np.float32) / 32768.0
                )
                continue
            event = json.loads(message)
            print("event:", event)
            if event.get("type") in ("translation", "transcript"):
                texts.append(str(event.get("text", "")))
            elif event.get("type") == "error":
                return 2
            elif event.get("type") == "done":
                break
        ws.send(json.dumps({"type": "stop"}))

    print("first_audio_ms:", None if first_audio_ms is None else round(first_audio_ms))
    print("text:", "".join(texts))
    if chunks:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_wav(args.out, np.concatenate(chunks))
        print("out:", args.out)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
