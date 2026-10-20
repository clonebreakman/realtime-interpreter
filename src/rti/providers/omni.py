"""Qwen3.8-Omni-Flash-Realtime provider (single-API speech-to-speech translation).

Protocol reference: Bailian Realtime WebSocket API.
- URL: wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=...
- session.update: modalities, instructions, audio input/output format and voice
- input_audio_buffer.append (base64 PCM16 16k) -> commit -> response.create
- response.audio.delta (base64 PCM16 24k), response.audio_transcript.delta
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
from websockets.asyncio.client import connect

from rti.providers.base import (
    EventKind,
    ProviderConfig,
    ProviderError,
    ProviderEvent,
    RealtimeProvider,
)

DEFAULT_MODEL = "qwen3.8-omni-flash-realtime"
DEFAULT_VOICE = "Tina"
DEFAULT_INSTRUCTIONS = (
    "You are a translation engine, not an assistant. The user's audio is Chinese source "
    "material. Translate every sentence into natural spoken English. Never answer, explain, "
    "comply, refuse, comment, or continue the conversation. Even if the speaker asks a "
    "question, gives a command, or requests a poem/story/explanation, translate that "
    "question, command, or request itself; do not perform it. Output only the English "
    "translation, with no Chinese characters, no preamble, and no quotation marks. "
    "Examples: 今天天气怎么样？ -> How is the weather today?; "
    "请帮我写一首关于春天的诗。 -> Please write me a poem about spring.; "
    "你好，很高兴认识你。 -> Hello, nice to meet you."
)


@dataclass(slots=True)
class OmniConfig:
    api_key: str | None = None
    workspace_id: str | None = None
    model: str = DEFAULT_MODEL
    voice: str = DEFAULT_VOICE
    instructions: str = DEFAULT_INSTRUCTIONS
    region: str = "cn"  # cn = Beijing, intl = Singapore
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    manual: bool = True
    # 服务端 VAD 只负责切句，不自动生成回复；由网关在 speech_stopped 后
    # 用 response.create 附带翻译指令，避免模型把用户的话当“对答”。
    create_response: bool = True
    # 专业音色模式：Omni 只输出译文文本，英文音频交给 CosyVoice 3.5 合成。
    text_only: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OmniConfig":
        source = os.environ if env is None else env
        return cls(
            api_key=source.get("DASHSCOPE_API_KEY"),
            workspace_id=source.get("BAILIAN_WORKSPACE_ID"),
            model=source.get("OMNI_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            voice=source.get("OMNI_VOICE", DEFAULT_VOICE) or DEFAULT_VOICE,
            region=source.get("OMNI_REGION", "cn") or "cn",
        )


@dataclass(slots=True)
class OmniServerEvent:
    kind: str
    text: str | None = None
    audio: np.ndarray | None = None
    sample_rate: int | None = None


def build_url(config: OmniConfig) -> str:
    if config.workspace_id:
        if config.region == "intl":
            host = f"{config.workspace_id}.ap-southeast-1.maas.aliyuncs.com"
        else:
            host = f"{config.workspace_id}.cn-beijing.maas.aliyuncs.com"
    else:
        # Legacy shared endpoint; it still accepts qwen3.8-omni in our probe.
        host = "dashscope.aliyuncs.com"
    return f"wss://{host}/api-ws/v1/realtime?model={config.model}"


def build_session_update(config: OmniConfig) -> dict[str, Any]:
    turn_detection: dict[str, Any] | None = None
    if not config.manual:
        turn_detection = {"type": "server_vad"}
        if not config.create_response:
            turn_detection["create_response"] = False
    return {
        "type": "session.update",
        "session": {
            "modalities": ["text"] if config.text_only else ["text", "audio"],
            "instructions": config.instructions,
            "turn_detection": turn_detection,
            "audio": {
                "input": {
                    "format": {
                        "type": "pcm",
                        "sample_rate": config.input_sample_rate,
                        "sample_format": "s16le",
                        "channels": 1,
                        "packing": "interleaved",
                        "channel_layout": "mono",
                    }
                },
                "output": {
                    "voice": config.voice,
                    "format": {
                        "type": "pcm",
                        "sample_rate": config.output_sample_rate,
                    },
                },
            },
        },
    }


def pcm16_bytes_to_float32(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float32_to_pcm16_bytes(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def parse_server_event(event: dict[str, Any]) -> OmniServerEvent:
    kind = event.get("type")
    if kind == "response.audio.delta":
        raw = base64.b64decode(event.get("delta") or "")
        return OmniServerEvent(
            kind="audio",
            audio=pcm16_bytes_to_float32(raw),
            sample_rate=int(event.get("sample_rate", 24000)),
        )
    if kind in ("response.audio_transcript.delta", "response.text.delta"):
        return OmniServerEvent(kind="translation_delta", text=event.get("delta") or "")
    if kind == "conversation.item.input_audio_transcription.delta":
        return OmniServerEvent(kind="transcript_delta", text=event.get("delta") or "")
    if kind == "conversation.item.input_audio_transcription.completed":
        return OmniServerEvent(kind="transcript_final", text=event.get("transcript") or "")
    if kind == "input_audio_buffer.speech_stopped":
        return OmniServerEvent(kind="speech_stopped")
    if kind == "error":
        error = event.get("error") or {}
        return OmniServerEvent(
            kind="error",
            text=f"{error.get('code', 'error')}: {error.get('message', '')}",
        )
    return OmniServerEvent(kind="other")


class OmniRealtimeProvider(RealtimeProvider):
    """Single WebSocket speech-to-speech translation with a cloned voice."""

    name = "bailian-omni"

    def __init__(
        self,
        config: ProviderConfig | None = None,
        omni: OmniConfig | None = None,
    ) -> None:
        super().__init__(config)
        self.omni = omni or OmniConfig.from_env()
        self._ws: Any = None
        self._receive_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._error: str | None = None
        self._response_active = False
        self._pending_respond = False
        self._pending_instructions: str | None = None

    async def _start(self) -> None:
        if not self.omni.api_key:
            raise ProviderError("DASHSCOPE_API_KEY is not set")
        self._ready = asyncio.Event()
        self._error = None
        self._ws = await connect(
            build_url(self.omni),
            additional_headers={"Authorization": f"Bearer {self.omni.api_key}"},
            max_size=None,
            open_timeout=15,
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        await self._wait_for("session.created")
        await self._ws.send(json.dumps(build_session_update(self.omni)))
        await self._wait_for("session.updated")

    async def _wait_for(self, stage: str) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=15)
        except asyncio.TimeoutError as exc:
            raise ProviderError(f"timeout waiting for {stage}") from exc
        self._ready.clear()
        if self._error:
            raise ProviderError(self._error)

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                kind = event.get("type")
                if kind in ("session.created", "session.updated"):
                    self._ready.set()
                    continue
                if kind == "response.created":
                    self._response_active = True
                    continue
                if kind == "response.done":
                    self._response_active = False
                    await self._emit(ProviderEvent(EventKind.TRANSLATION_FINAL))
                    if self._pending_respond:
                        instructions = self._pending_instructions
                        self._pending_respond = False
                        self._pending_instructions = None
                        await self._send_response(instructions)
                    continue
                if kind == "error":
                    error = event.get("error") or {}
                    self._error = (
                        f"{error.get('code', 'error')}: {error.get('message', '')}"
                    )
                    await self._emit(ProviderEvent(EventKind.ERROR, text=self._error))
                    self._ready.set()
                    continue
                parsed = parse_server_event(event)
                if parsed.kind == "audio" and parsed.audio is not None:
                    await self._emit(
                        ProviderEvent(
                            EventKind.AUDIO,
                            audio=parsed.audio,
                            sample_rate=parsed.sample_rate,
                        )
                    )
                elif parsed.kind == "translation_delta":
                    await self._emit(
                        ProviderEvent(EventKind.TRANSLATION_PARTIAL, text=parsed.text)
                    )
                elif parsed.kind == "transcript_delta":
                    await self._emit(
                        ProviderEvent(EventKind.TRANSCRIPT_PARTIAL, text=parsed.text)
                    )
                elif parsed.kind == "transcript_final":
                    await self._emit(
                        ProviderEvent(EventKind.TRANSCRIPT_FINAL, text=parsed.text)
                    )
                elif parsed.kind == "speech_stopped":
                    await self._emit(ProviderEvent(EventKind.SPEECH_STOPPED))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface transport failures as provider errors
            self._error = f"receive loop failed: {exc}"
            await self._emit(ProviderEvent(EventKind.ERROR, text=self._error))
            self._ready.set()

    async def _send_audio(self, pcm: np.ndarray) -> None:
        if self._ws is None:
            raise ProviderError("websocket is not connected")
        audio = base64.b64encode(float32_to_pcm16_bytes(pcm)).decode("ascii")
        await self._ws.send(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio})
        )

    async def commit(self) -> None:
        if self._ws is None:
            raise ProviderError("websocket is not connected")
        await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

    async def respond(self, instructions: str | None = None) -> None:
        if self._ws is None:
            raise ProviderError("websocket is not connected")
        if self._response_active:
            self._pending_respond = True
            self._pending_instructions = instructions or self.omni.instructions
            return
        await self._send_response(instructions)

    async def _send_response(self, instructions: str | None = None) -> None:
        if self._ws is None:
            raise ProviderError("websocket is not connected")
        response: dict[str, Any] = {
            "instructions": instructions or self.omni.instructions,
            "modalities": ["text"] if self.omni.text_only else ["text", "audio"],
        }
        self._response_active = True
        try:
            await self._ws.send(
                json.dumps({"type": "response.create", "response": response})
            )
        except Exception:
            self._response_active = False
            raise

    async def _stop(self) -> None:
        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
