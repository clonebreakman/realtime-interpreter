"""Deterministic translator for tests and dry runs."""
from __future__ import annotations

from typing import AsyncIterator

from rti.mt.base import TranslationChunk, Translator


class MockTranslator(Translator):
    name = "mock"

    async def translate_stream(
        self, text: str, context: list[str] | None = None
    ) -> AsyncIterator[TranslationChunk]:
        for part in ("Hello", " from", " mock"):
            yield TranslationChunk(text=part, is_final=False)
        yield TranslationChunk(text="", is_final=True)
