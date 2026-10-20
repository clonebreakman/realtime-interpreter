"""Probe qwen3.8-omni-flash-realtime connectivity and session.update.

Environment:
    DASHSCOPE_API_KEY      required
    BAILIAN_WORKSPACE_ID   optional; required by workspace-bound domains
    OMNI_MODEL             default qwen3.8-omni-flash-realtime
"""
from __future__ import annotations

import json
import os
import sys

from websockets.sync.client import connect

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def build_url() -> str:
    model = os.getenv("OMNI_MODEL", "qwen3.8-omni-flash-realtime")
    workspace = os.getenv("BAILIAN_WORKSPACE_ID", "").strip()
    if workspace:
        host = f"{workspace}.cn-beijing.maas.aliyuncs.com"
    else:
        host = "dashscope.aliyuncs.com"
    return f"wss://{host}/api-ws/v1/realtime?model={model}"


def main() -> int:
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        print("DASHSCOPE_API_KEY is not set")
        return 2
    url = build_url()
    print("connecting:", url.split("?")[0])
    try:
        with connect(
            url,
            additional_headers={"Authorization": f"Bearer {key}"},
            open_timeout=15,
            close_timeout=5,
        ) as ws:
            for _ in range(3):
                raw = ws.recv(timeout=8)
                event = json.loads(raw)
                print("event:", event.get("type"), str(event)[:300])
                if event.get("type") == "session.created":
                    ws.send(
                        json.dumps(
                            {
                                "type": "session.update",
                                "session": {
                                    "modalities": ["text", "audio"],
                                    "instructions": (
                                        "You are a professional simultaneous interpreter. "
                                        "Translate the user's Chinese speech into English speech. "
                                        "Output only the English translation."
                                    ),
                                    "audio": {
                                        "input": {
                                            "format": {
                                                "type": "pcm",
                                                "sample_rate": 16000,
                                                "sample_format": "s16le",
                                                "channels": 1,
                                                "packing": "interleaved",
                                                "channel_layout": "mono",
                                            }
                                        },
                                        "output": {
                                            "voice": "Tina",
                                            "format": {"type": "pcm", "sample_rate": 24000},
                                        },
                                    },
                                },
                            }
                        )
                    )
                    break
            for _ in range(3):
                raw = ws.recv(timeout=8)
                event = json.loads(raw)
                print("event:", event.get("type"), str(event)[:300])
                if event.get("type") == "session.updated":
                    print("session updated successfully")
                    return 0
    except Exception as exc:  # noqa: BLE001 - probe should print any failure
        print("ERR", type(exc).__name__, str(exc)[:500])
        return 1
    print("no session.updated received")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
