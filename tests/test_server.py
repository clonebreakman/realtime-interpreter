"""Gateway contract: browser PCM -> provider events -> browser audio/text."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from rti.providers.base import EventKind, ProviderEvent
from rti.server.app import create_app


class FakeProvider:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.audio: list[np.ndarray] = []
        self._events: asyncio.Queue[ProviderEvent | None] = asyncio.Queue()

    async def start(self) -> None:
        self.started = True
        await self._events.put(ProviderEvent(EventKind.TRANSCRIPT_PARTIAL, text="你好"))
        await self._events.put(
            ProviderEvent(
                EventKind.AUDIO,
                audio=np.full(240, 0.1, dtype=np.float32),
                sample_rate=24000,
            )
        )
        await self._events.put(
            ProviderEvent(EventKind.TRANSLATION_PARTIAL, text="Hello")
        )
        await self._events.put(ProviderEvent(EventKind.TRANSLATION_FINAL))
        await self._events.put(None)

    async def send_audio(self, pcm: np.ndarray) -> None:
        self.audio.append(np.asarray(pcm))

    async def stop(self) -> None:
        self.stopped = True

    async def events(self):
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event


def test_health_and_config() -> None:
    app = create_app()
    app.state.settings.api_key = ""
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["ok"] is True
        config = client.get("/api/config").json()
        assert config["has_key"] is False
        assert "voice" in config and "model" in config


def test_settings_persist_key_and_voice(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    app = create_app(config_path=config_path)
    app.state.settings.api_key = ""
    with TestClient(app) as client:
        response = client.post(
            "/api/settings", json={"api_key": "test-key", "voice": "qwen-omni-vc-x"}
        )
        assert response.status_code == 200
        assert response.json()["has_key"] is True
        config = client.get("/api/config").json()
        assert config["voice"] == "qwen-omni-vc-x"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["voice"] == "qwen-omni-vc-x"
    assert saved["api_key"] == "test-key"


def test_config_file_loads_key_and_voice(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_key": "saved-key",
                "voice": "qwen-omni-vc-default",
                "model": "custom-model",
            }
        ),
        encoding="utf-8",
    )
    app = create_app(config_path=config_path)
    assert app.state.settings.api_key == "saved-key"
    assert app.state.settings.voice == "qwen-omni-vc-default"
    assert app.state.settings.model == "custom-model"


def test_websocket_streams_audio_and_text() -> None:
    fake = FakeProvider()
    app = create_app(provider_factory=lambda _omni: fake)
    app.state.settings.api_key = "test-key"
    app.state.settings.voice = "qwen-omni-vc-test"
    app.state.settings.pro_voice = ""
    app.state.settings.allow_defaults = False
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "start", "voice": "qwen-omni-vc-test"})
            ready = websocket.receive_json()
            assert ready["type"] == "ready"
            websocket.send_bytes(np.zeros(320, dtype="<i2").tobytes())
            seen: list[str] = []
            for _ in range(4):
                message = websocket.receive()
                if message.get("bytes") is not None:
                    seen.append("audio")
                elif message.get("text"):
                    seen.append(json.loads(message["text"])["type"])
            websocket.send_json({"type": "stop"})
    assert "transcript" in seen
    assert "audio" in seen
    assert "translation" in seen
    assert "done" in seen
    assert fake.started is True
    assert fake.stopped is True


def test_empty_voice_does_not_overwrite_existing(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"voice": "qwen-omni-vc-existing"}), encoding="utf-8"
    )
    app = create_app(config_path=config_path)
    with TestClient(app) as client:
        response = client.post("/api/settings", json={"voice": ""})
        assert response.status_code == 200
        assert response.json()["voice"] == "qwen-omni-vc-existing"


def test_enroll_requires_api_key(tmp_path) -> None:
    app = create_app(config_path=tmp_path / "config.json")
    with TestClient(app) as client:
        response = client.post(
            "/api/enroll",
            json={"audio_base64": base64.b64encode(b"x" * 2000).decode()},
        )
        assert response.status_code == 400


def make_wav(seconds: float = 4.0, rate: int = 24000, amplitude: float = 0.2) -> bytes:
    count = int(seconds * rate)
    time = np.arange(count, dtype=np.float32) / rate
    pcm = (amplitude * np.sin(2 * np.pi * 440 * time) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def test_enroll_saves_voice(tmp_path, monkeypatch) -> None:
    def fake_create(audio: bytes, config, mime_type: str = "audio/wav") -> str:
        assert len(audio) > 10000
        return "qwen-omni-vc-from-test"

    monkeypatch.setattr("rti.server.app.create_voice", fake_create)
    monkeypatch.setattr(
        "rti.server.app.create_professional_voice",
        lambda audio, config, language_hints=None: "cosyvoice-v3.5-plus-test",
    )
    config_path = tmp_path / "config.json"
    app = create_app(config_path=config_path)
    app.state.settings.api_key = "test-key"
    with TestClient(app) as client:
        response = client.post(
            "/api/enroll",
            json={
                "audio_base64": base64.b64encode(make_wav()).decode(),
                "mime_type": "audio/wav",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["voice"] == "qwen-omni-vc-from-test"
        assert payload["pro_voice"] == "cosyvoice-v3.5-plus-test"
        assert payload["duration_s"] == 4.0
        assert payload["peak"] > 0
        assert Path(payload["saved"]).is_file()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["voice"] == "qwen-omni-vc-from-test"
    assert saved["pro_voice"] == "cosyvoice-v3.5-plus-test"
    voices = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    assert voices["voices"][0]["id"] == "qwen-omni-vc-from-test"


def test_enroll_peer_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "rti.server.app.create_voice",
        lambda audio, config, mime_type="audio/wav": "qwen-omni-vc-peer-test",
    )
    monkeypatch.setattr(
        "rti.server.app.create_professional_voice",
        lambda audio, config, language_hints=None: "cosyvoice-v3.5-plus-peer-test",
    )
    config_path = tmp_path / "config.json"
    app = create_app(config_path=config_path)
    app.state.settings.api_key = "test-key"
    with TestClient(app) as client:
        response = client.post(
            "/api/enroll",
            json={
                "audio_base64": base64.b64encode(make_wav()).decode(),
                "profile": "peer",
                "name": "对方音色",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["profile"] == "peer"
        assert payload["peer_pro_voice"] == "cosyvoice-v3.5-plus-peer-test"
        assert payload["pro_voice"] == "cosyvoice-v3.5-plus-peer-test"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["peer_voice"] == "qwen-omni-vc-peer-test"
    assert saved["peer_pro_voice"] == "cosyvoice-v3.5-plus-peer-test"


def test_voice_collector_rebuilds_professional_voice(tmp_path, monkeypatch) -> None:
    from rti.server.app import RuntimeSettings, VoiceCollector

    captured = {}

    def fake_create(audio, config, language_hints=None):
        captured["bytes"] = len(audio)
        captured["hints"] = language_hints
        return "cosyvoice-v3.5-plus-auto"

    monkeypatch.setattr("rti.server.app.create_professional_voice", fake_create)
    config_path = tmp_path / "config.json"
    settings = RuntimeSettings(api_key="test-key", pro_voice="old-pro")
    collector = VoiceCollector("self", config_path, settings, "test-key")
    pcm = b"\x01\x00" * 16000  # 1 s
    for _ in range(30):
        collector.buffer.extend(pcm)
        collector.finish_segment()
    asyncio.run(collector.optimize())
    assert captured["bytes"] > 50000
    assert captured["hints"] == ["zh"]
    assert settings.pro_voice == "cosyvoice-v3.5-plus-auto"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["pro_voice"] == "cosyvoice-v3.5-plus-auto"


def test_voices_list_and_set_default(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    app = create_app(config_path=config_path)
    app.state.settings.voice = "qwen-omni-vc-first"
    with TestClient(app) as client:
        listed = client.get("/api/voices").json()
        assert listed["default"] == "qwen-omni-vc-first"
        response = client.post(
            "/api/voices/default", json={"voice": "qwen-omni-vc-second"}
        )
        assert response.status_code == 200
        assert response.json()["default"] == "qwen-omni-vc-second"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["voice"] == "qwen-omni-vc-second"


def test_enroll_rejects_silence(tmp_path) -> None:
    app = create_app(config_path=tmp_path / "config.json")
    app.state.settings.api_key = "test-key"
    silent = make_wav(amplitude=0.0)
    with TestClient(app) as client:
        response = client.post(
            "/api/enroll",
            json={"audio_base64": base64.b64encode(silent).decode()},
        )
        assert response.status_code == 400
        assert "安静" in response.json()["detail"]


def test_missing_voice_returns_onboarding_error() -> None:
    app = create_app(provider_factory=lambda _omni: FakeProvider())
    app.state.settings.api_key = "test-key"
    app.state.settings.voice = ""
    app.state.settings.pro_voice = ""
    app.state.settings.allow_defaults = False
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "start"})
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert "音色" in error["message"]


def test_missing_key_returns_actionable_error() -> None:
    app = create_app(provider_factory=lambda _omni: FakeProvider())
    app.state.settings.api_key = ""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "start"})
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert "API Key" in error["message"]
