"""Professional CosyVoice 3.5 voice cloning + streaming TTS.

CosyVoice v3.5-plus is Alibaba's high-similarity voice-clone model.  Unlike the
Omni realtime clone used for S2S audio, this path needs a public/temporary URL
for enrollment, then synthesizes the English translation text with ``pcm``
streaming so the browser can keep the existing 24 kHz playback queue.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Mapping

import httpx

PRO_MODEL = "cosyvoice-v3.5-plus"
UPLOAD_API = "https://maas.qianwenaiapi.com/api/v1/uploads"
CUSTOMIZATION_API = "https://maas.qianwenaiapi.com/api/v1/services/audio/tts/customization"
TTS_API = "https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer"


@dataclass(slots=True)
class ProfessionalVoiceConfig:
    api_key: str | None = None
    model: str = PRO_MODEL
    output_sample_rate: int = 24000
    language: str = "en"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProfessionalVoiceConfig":
        source = os.environ if env is None else env
        return cls(api_key=source.get("DASHSCOPE_API_KEY"))


def _headers(config: ProfessionalVoiceConfig) -> dict[str, str]:
    if not config.api_key:
        raise ValueError("DASHSCOPE_API_KEY is not set")
    return {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}


def _upload_audio(audio: bytes, config: ProfessionalVoiceConfig, filename: str = "sample.wav") -> str:
    """Upload to the free 48h temp storage and return an ``oss://`` URL."""
    headers = _headers(config)
    with httpx.Client(timeout=60.0) as client:
        policy_response = client.get(
            UPLOAD_API,
            params={"action": "getPolicy", "model": config.model},
            headers=headers,
        )
        if policy_response.status_code != 200:
            raise RuntimeError(
                f"professional upload policy failed: HTTP {policy_response.status_code} "
                f"{policy_response.text[:300]}"
            )
        policy = policy_response.json()["data"]
        key = f"{policy['upload_dir']}/{filename}"
        data = {
            "OSSAccessKeyId": policy["oss_access_key_id"],
            "Signature": policy["signature"],
            "policy": policy["policy"],
            "x-oss-object-acl": policy["x_oss_object_acl"],
            "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
            "key": key,
            "success_action_status": "200",
        }
        upload = client.post(
            policy["upload_host"],
            data=data,
            files={"file": (filename, audio, "audio/wav")},
            timeout=120.0,
        )
        if upload.status_code != 200:
            raise RuntimeError(
                f"professional upload failed: HTTP {upload.status_code} {upload.text[:300]}"
            )
    return f"oss://{key}"


def create_professional_voice(
    audio: bytes,
    config: ProfessionalVoiceConfig,
    *,
    language_hints: list[str] | None = None,
    prefix: str = "provoice",
) -> str:
    """Create a CosyVoice 3.5 clone and return its voice id."""
    url = _upload_audio(audio, config)
    headers = _headers(config)
    headers["X-DashScope-OssResourceResolve"] = "enable"
    payload = {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": config.model,
            "prefix": prefix[:10] or "provoice",
            "url": url,
            "language_hints": language_hints or ["zh"],
            "enable_preprocess": False,
            "enable_volume_normalization": "true",
        },
    }
    with httpx.Client(timeout=180.0) as client:
        response = client.post(CUSTOMIZATION_API, headers=headers, json=payload)
    if response.status_code != 200:
        raise RuntimeError(
            f"professional voice enrollment failed: HTTP {response.status_code} "
            f"{response.text[:500]}"
        )
    data = response.json()
    output = data.get("output") or {}
    voice = output.get("voice_id") or output.get("voice")
    if not voice:
        raise RuntimeError(f"professional voice id missing: {data}")
    return str(voice)


def _iter_sse_events(lines: list[str]) -> list[dict]:
    events: list[dict] = []
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
            continue
        if line != "" or not data_lines:
            continue
        body = "\n".join(data_lines).strip()
        data_lines = []
        if not body or body == "[DONE]":
            continue
        try:
            events.append(json.loads(body))
        except json.JSONDecodeError:
            continue
    if data_lines:
        try:
            events.append(json.loads("\n".join(data_lines).strip()))
        except json.JSONDecodeError:
            pass
    return events


async def synthesize_stream(
    text: str,
    voice: str,
    config: ProfessionalVoiceConfig,
) -> AsyncIterator[bytes]:
    """Yield PCM16 LE 24 kHz bytes for ``text`` using a CosyVoice clone."""
    if not text.strip():
        return
    headers = _headers(config)
    headers["X-DashScope-SSE"] = "enable"
    payload = {
        "model": config.model,
        "input": {
            "text": text,
            "voice": voice,
            "format": "pcm",
            "sample_rate": config.output_sample_rate,
            "language_hints": [config.language],
        },
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        async with client.stream("POST", TTS_API, headers=headers, json=payload) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", "replace")
                raise RuntimeError(
                    f"professional TTS failed: HTTP {response.status_code} {detail[:500]}"
                )
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                    continue
                if line != "" or not data_lines:
                    continue
                body = "\n".join(data_lines).strip()
                data_lines = []
                if not body or body == "[DONE]":
                    continue
                try:
                    event = json.loads(body)
                except json.JSONDecodeError:
                    continue
                output = event.get("output") or {}
                chunk = (output.get("audio") or {}).get("data") or ""
                if chunk:
                    decoded = base64.b64decode(chunk)
                    if decoded:
                        yield decoded
                if output.get("finish_reason") == "stop":
                    return


async def synthesize_text(text: str, voice: str, config: ProfessionalVoiceConfig) -> bytes:
    return b"".join([chunk async for chunk in synthesize_stream(text, voice, config)])


__all__ = [
    "PRO_MODEL",
    "ProfessionalVoiceConfig",
    "create_professional_voice",
    "synthesize_stream",
    "synthesize_text",
]
