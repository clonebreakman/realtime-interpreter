"""Qwen3.8-Omni realtime provider and voice enrollment contract tests."""
from __future__ import annotations

import base64
import json

import httpx
import numpy as np
import pytest

from rti.providers.omni import (
    OmniConfig,
    build_session_update,
    build_url,
    float32_to_pcm16_bytes,
    parse_server_event,
    pcm16_bytes_to_float32,
)
from rti.voice.enroll import VoiceEnrollmentConfig, build_enrollment_payload, create_voice


def test_build_url_uses_workspace_and_model() -> None:
    config = OmniConfig(api_key="k", workspace_id="ws-123", region="cn")
    assert build_url(config) == (
        "wss://ws-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        "?model=qwen3.8-omni-flash-realtime"
    )
    fallback = build_url(OmniConfig(api_key="k", workspace_id=""))
    assert fallback == (
        "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        "?model=qwen3.8-omni-flash-realtime"
    )


def test_build_session_update_contains_voice_and_pcm_formats() -> None:
    config = OmniConfig(
        api_key="k",
        workspace_id="ws",
        voice="qwen-omni-vc-test",
        instructions="translate zh to en",
    )
    payload = build_session_update(config)
    session = payload["session"]
    assert session["modalities"] == ["text", "audio"]
    assert session["instructions"] == "translate zh to en"
    assert session["turn_detection"] is None
    assert session["audio"]["input"]["format"]["sample_rate"] == 16000
    assert session["audio"]["input"]["format"]["sample_format"] == "s16le"
    assert session["audio"]["output"]["voice"] == "qwen-omni-vc-test"
    assert session["audio"]["output"]["format"]["sample_rate"] == 24000


def test_build_session_update_manual_vad_does_not_auto_respond() -> None:
    config = OmniConfig(api_key="k", manual=False, create_response=False)
    detection = build_session_update(config)["session"]["turn_detection"]
    assert detection == {"type": "server_vad", "create_response": False}


def test_build_session_update_text_only_for_professional_voice() -> None:
    config = OmniConfig(api_key="k", manual=False, text_only=True)
    session = build_session_update(config)["session"]
    assert session["modalities"] == ["text"]


def test_parse_speech_stopped() -> None:
    parsed = parse_server_event({"type": "input_audio_buffer.speech_stopped"})
    assert parsed.kind == "speech_stopped"


def test_parse_audio_delta() -> None:
    samples = np.array([1000, -1000], dtype="<i2")
    event = {
        "type": "response.audio.delta",
        "delta": base64.b64encode(samples.tobytes()).decode("ascii"),
    }
    parsed = parse_server_event(event)
    assert parsed.kind == "audio"
    assert parsed.sample_rate == 24000
    assert parsed.audio is not None
    np.testing.assert_allclose(parsed.audio, samples.astype(np.float32) / 32768.0)


def test_parse_translation_and_transcript_deltas() -> None:
    translation = parse_server_event(
        {"type": "response.audio_transcript.delta", "delta": "Hello"}
    )
    assert translation.kind == "translation_delta"
    assert translation.text == "Hello"

    transcript = parse_server_event(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": "你好"}
    )
    assert transcript.kind == "transcript_delta"
    assert transcript.text == "你好"


def test_parse_error_event() -> None:
    parsed = parse_server_event(
        {
            "type": "error",
            "error": {"message": "Access denied", "code": "AccessDenied"},
        }
    )
    assert parsed.kind == "error"
    assert "Access denied" in (parsed.text or "")


def test_pcm16_roundtrip() -> None:
    source = np.array([-1.0, -0.5, 0.0, 0.5, 1.0], dtype=np.float32)
    restored = pcm16_bytes_to_float32(float32_to_pcm16_bytes(source))
    np.testing.assert_allclose(restored, source, atol=1e-4)


def test_enrollment_payload_and_create_voice() -> None:
    config = VoiceEnrollmentConfig(api_key="k", workspace_id="ws", preferred_name="me")
    payload = build_enrollment_payload("data:audio/wav;base64,AAAA", config)
    assert payload["model"] == "qwen-voice-enrollment"
    assert payload["input"]["action"] == "create"
    assert payload["input"]["target_model"] == "qwen3.8-omni-flash-realtime"
    assert payload["input"]["preferred_name"] == "me"
    assert payload["parameters"]["voice_clone_mode"] == "normal"

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"output": {"voice": "qwen-omni-vc-me-1"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.com")
    voice = create_voice(b"RIFFfakewav", config, client=client, mime_type="audio/wav")
    assert voice == "qwen-omni-vc-me-1"
    assert captured["url"].endswith("/api/v1/services/audio/tts/customization")
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["input"]["audio"]["data"].startswith("data:audio/wav;base64,")
