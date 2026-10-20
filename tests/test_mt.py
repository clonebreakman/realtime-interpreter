"""DeepSeek translator contract tests: prompt, streaming SSE, error handling and cost model."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from rti.mt.base import TranslationChunk, TranslationConfig, TranslationError
from rti.mt.deepseek import DeepSeekTranslator
from rti.mt.prompt import build_messages
from rti.pipeline.cost import (
    FLASH_OFF_PEAK,
    PRO_OFF_PEAK,
    deepseek_cost_usd,
    full_chain_cost_usd,
)


def test_prompt_contains_languages_glossary_and_context() -> None:
    messages = build_messages(
        "你好",
        context=["上一句中文"],
        glossary={"安佑": "Anyou"},
        source="zh-CN",
        target="en-US",
    )
    system = messages[0]["content"]
    assert "zh-CN" in system
    assert "en-US" in system
    assert "Anyou" in system
    assert any("上一句中文" in message["content"] for message in messages)
    assert messages[-1]["role"] == "user"
    assert "你好" in messages[-1]["content"]


def test_prompt_instructs_translation_only() -> None:
    system = build_messages("你好")[0]["content"].lower()
    assert "only" in system
    assert "translation" in system


def _sse_response() -> bytes:
    lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "",
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "",
        "data: [DONE]",
        "",
    ]
    return ("\n".join(lines)).encode("utf-8")


def test_deepseek_streams_sse_and_disables_thinking() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            content=_sse_response(),
            headers={"content-type": "text/event-stream"},
        )

    async def scenario() -> list[TranslationChunk]:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com",
        )
        translator = DeepSeekTranslator(TranslationConfig(), api_key="test-key", client=client)
        try:
            return [chunk async for chunk in translator.translate_stream("你好")]
        finally:
            await client.aclose()

    chunks = asyncio.run(scenario())
    assert "".join(chunk.text for chunk in chunks) == "Hello world"
    assert chunks[-1].is_final is True
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "deepseek-flash"
    assert captured["body"]["stream"] is True
    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_deepseek_aggregate_translate_returns_full_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse_response(),
            headers={"content-type": "text/event-stream"},
        )

    async def scenario() -> str:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com",
        )
        translator = DeepSeekTranslator(TranslationConfig(), api_key="test-key", client=client)
        try:
            return await translator.translate("你好")
        finally:
            await client.aclose()

    assert asyncio.run(scenario()) == "Hello world"


def test_deepseek_http_error_raises_translation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com",
        )
        translator = DeepSeekTranslator(TranslationConfig(), api_key="test-key", client=client)
        try:
            with pytest.raises(TranslationError, match="401"):
                [chunk async for chunk in translator.translate_stream("你好")]
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_deepseek_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(TranslationError, match="DEEPSEEK_API_KEY"):
        DeepSeekTranslator(TranslationConfig())


def test_deepseek_cost_model() -> None:
    assert deepseek_cost_usd(1_000_000, 1_000_000) == pytest.approx(0.75)
    assert deepseek_cost_usd(1_000_000, 1_000_000, peak=True) == pytest.approx(1.5)
    assert deepseek_cost_usd(
        1_000_000, 0, cache_hit_tokens=1_000_000
    ) == pytest.approx(FLASH_OFF_PEAK.input_cache_hit)
    assert deepseek_cost_usd(1_000_000, 0, pricing=PRO_OFF_PEAK) == pytest.approx(
        PRO_OFF_PEAK.input_cache_miss
    )
    with pytest.raises(ValueError):
        deepseek_cost_usd(-1, 0)


def test_full_chain_cost_for_one_hour() -> None:
    # 3600s ASR * $0.000047 + 45k chars * $0.116/10k + 15k/15k DeepSeek tokens
    expected = 3600 * 0.000047 + 4.5 * 0.116 + 0.01125
    assert full_chain_cost_usd(1.0) == pytest.approx(expected, rel=1e-3)
    assert full_chain_cost_usd(0.0) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        full_chain_cost_usd(-1.0)
