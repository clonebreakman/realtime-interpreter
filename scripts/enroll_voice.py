"""Create a Qwen3.8-Omni cloned voice from 10-20s of clean audio.

Example:
    $env:DASHSCOPE_API_KEY="sk-..."
    $env:BAILIAN_WORKSPACE_ID="ws-..."
    .\.venv\Scripts\python.exe scripts\enroll_voice.py --audio voice.wav --name myvoice
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rti.voice.enroll import VoiceEnrollmentConfig, create_voice  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an Omni cloned voice")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--name", default=os.getenv("OMNI_VOICE_NAME", "myvoice"))
    parser.add_argument("--workspace", default=os.getenv("BAILIAN_WORKSPACE_ID"))
    parser.add_argument(
        "--model", default=os.getenv("OMNI_MODEL", "qwen3.8-omni-flash-realtime")
    )
    parser.add_argument("--region", choices=["cn", "intl"], default="cn")
    parser.add_argument("--mime", default="audio/wav")
    args = parser.parse_args(argv)

    config = VoiceEnrollmentConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        workspace_id=args.workspace,
        target_model=args.model,
        preferred_name=args.name,
        region=args.region,
    )
    try:
        voice = create_voice(args.audio, config, mime_type=args.mime)
    except Exception as exc:  # noqa: BLE001 - CLI reports actionable failure
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(voice)
    print(f"set OMNI_VOICE={voice}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
