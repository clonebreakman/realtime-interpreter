"""Zero-shot voice enrollment for Qwen3.8-Omni (qwen-voice-enrollment)."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import httpx

DEFAULT_TARGET_MODEL = "qwen3.8-omni-flash-realtime"
ENROLLMENT_PATH = "/api/v1/services/audio/tts/customization"


@dataclass(slots=True)
class VoiceEnrollmentConfig:
    api_key: str | None = None
    workspace_id: str | None = None
    target_model: str = DEFAULT_TARGET_MODEL
    preferred_name: str = "myvoice"
    region: str = "cn"
    voice_clone_mode: str = "normal"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "VoiceEnrollmentConfig":
        source = os.environ if env is None else env
        return cls(
            api_key=source.get("DASHSCOPE_API_KEY"),
            workspace_id=source.get("BAILIAN_WORKSPACE_ID"),
            target_model=source.get("OMNI_MODEL", DEFAULT_TARGET_MODEL)
            or DEFAULT_TARGET_MODEL,
            preferred_name=source.get("OMNI_VOICE_NAME", "myvoice") or "myvoice",
            region=source.get("OMNI_REGION", "cn") or "cn",
        )


def build_enrollment_url(config: VoiceEnrollmentConfig) -> str:
    if config.workspace_id:
        if config.region == "intl":
            host = f"{config.workspace_id}.ap-southeast-1.maas.aliyuncs.com"
        else:
            host = f"{config.workspace_id}.cn-beijing.maas.aliyuncs.com"
    else:
        host = "dashscope.aliyuncs.com"
    return f"https://{host}{ENROLLMENT_PATH}"


def build_enrollment_payload(
    audio_data_uri: str, config: VoiceEnrollmentConfig
) -> dict:
    name = config.preferred_name
    if not (1 <= len(name) <= 16) or not all(
        char.isalnum() or char == "_" for char in name
    ):
        raise ValueError("preferred_name must be 1-16 letters, digits or underscores")
    return {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": config.target_model,
            "preferred_name": name,
            "audio": {"data": audio_data_uri},
        },
        "parameters": {
            "voice_clone_mode": config.voice_clone_mode,
            "skip_audio_process": False,
        },
    }


def create_voice(
    audio: bytes | str | Path,
    config: VoiceEnrollmentConfig,
    *,
    client: httpx.Client | None = None,
    mime_type: str = "audio/wav",
) -> str:
    """Upload 10-20s of clean audio and return the cloned ``voice`` id."""
    if not config.api_key:
        raise ValueError("DASHSCOPE_API_KEY is not set")
    raw = audio if isinstance(audio, bytes) else Path(audio).read_bytes()
    data_uri = f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"
    payload = build_enrollment_payload(data_uri, config)
    url = build_enrollment_url(config)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=60.0)
    try:
        response = client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(
                f"voice enrollment failed: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
        data = response.json()
        return str(data["output"]["voice"])
    finally:
        if own_client:
            client.close()
