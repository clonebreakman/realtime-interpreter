"""DeepSeek chat-completions translator (OpenAI-compatible, SSE streaming).

Defaults are tuned for cost and first-token latency:
- ``deepseek-flash`` (DeepSeek-V4.1-Flash-1017)
- thinking mode disabled
- low temperature, small max_tokens
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from rti.mt.base import TranslationChunk, TranslationConfig, TranslationError, Translator
from rti.mt.prompt import build_messages

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


class DeepSeekTranslator(Translator):
    name = "deepseek"

    def __init__(
        self,
        config: TranslationConfig | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(config)
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise TranslationError("DEEPSEEK_API_KEY is not set")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        if not self.config.model:
            self.config.model = DEFAULT_MODEL
        self._client = client
        self._timeout = timeout

    def _payload(self, text: str, context: list[str] | None) -> dict:
        return {
            "model": self.config.model,
            "messages": build_messages(
                text,
                context=context,
                glossary=self.config.glossary,
                source=self.config.source_language,
                target=self.config.target_language,
            ),
            "stream": True,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "thinking": {"type": "disabled"},
        }

    async def translate_stream(
        self, text: str, context: list[str] | None = None
    ) -> AsyncIterator[TranslationChunk]:
        payload = self._payload(text, context)
        if self._client is not None:
            async for chunk in self._stream(self._client, payload):
                yield chunk
            return
        timeout = httpx.Timeout(self._timeout, connect=10.0)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
            async for chunk in self._stream(client, payload):
                yield chunk

    async def _stream(
        self, client: httpx.AsyncClient, payload: dict
    ) -> AsyncIterator[TranslationChunk]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with client.stream(
            "POST", "/chat/completions", json=payload, headers=headers
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")
                raise TranslationError(
                    f"DeepSeek HTTP {response.status_code}: {body[:300]}"
                )
            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped or not stripped.startswith("data:"):
                    continue
                data = stripped[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    yield TranslationChunk(text=content, is_final=False)
                if choice.get("finish_reason"):
                    break
        yield TranslationChunk(text="", is_final=True)
