"""Record a 10-20s voice sample for Qwen3.8-Omni voice cloning.

Example:
    .\.venv\Scripts\python.exe scripts\record_voice.py --seconds 15 --out voice.wav
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def list_inputs() -> None:
    print(sd.query_devices())
    try:
        default = sd.query_devices(kind="input")
        print(f"\ndefault input: {default['name']}")
    except Exception as exc:  # noqa: BLE001 - informational only
        print(f"no default input: {exc}")


def record(seconds: float, sample_rate: int, device: int | None, countdown: bool) -> np.ndarray:
    if countdown:
        for number in (3, 2, 1):
            print(f"{number}...")
            time.sleep(1)
    print("录音中：请用正常语速连续说中文，不要长时间停顿。")
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    print("录音结束。")
    return audio[:, 0]


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record an Omni voice-clone sample")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--out", type=Path, default=Path("voice.wav"))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-countdown", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        list_inputs()
        return 0
    if not 1 <= args.seconds <= 60:
        print("--seconds must be between 1 and 60", file=sys.stderr)
        return 2

    try:
        default = sd.query_devices(kind="input")
        print(f"input device: {default['name']}")
    except Exception as exc:  # noqa: BLE001 - device may still be explicit
        print(f"warning: cannot query default input: {exc}", file=sys.stderr)

    try:
        samples = record(args.seconds, args.sample_rate, args.device, not args.no_countdown)
    except Exception as exc:  # noqa: BLE001 - CLI reports actionable failure
        print(f"录音失败: {exc}", file=sys.stderr)
        return 1

    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
    write_wav(args.out, samples, args.sample_rate)
    print(f"peak={peak:.4f} rms={rms:.4f}")
    if peak < 0.01:
        print("警告: 音量过低，请检查麦克风是否被静音，或换一个输入设备。", file=sys.stderr)
    print(f"已保存: {args.out.resolve()}")
    print(f"时长: {samples.size / args.sample_rate:.1f}s  采样率: {args.sample_rate}Hz  单声道")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
