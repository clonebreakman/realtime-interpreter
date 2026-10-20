"""Translator contract.  Any cloud MT vendor can implement this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass(slots=True)
class TranslationConfig:
    source_language: str = "zh-CN"
    target_language: str = "en-US"
    model: str = "deepseek-flash"
    glossary: dict[str, str] = field(default_factory=dict)
    context_sentences: int = 2
    max_tokens: int = 512
    temperature: float = 0.2


@dataclass(slots=True)
class TranslationChunk:
    text: str
    is_final: bool = False


class TranslationError(RuntimeError):
    """Raised when a translation request fails or is misconfigured."""


class Translator(ABC):
    name = "base"

    def __init__(self, config: TranslationConfig | None = None) -> None:
        self.config = config or TranslationConfig()

    @abstractmethod
    def translate_stream(
        self, text: str, context: list[str] | None = None
    ) -> AsyncIterator[TranslationChunk]:
        """Yield incremental translation chunks, ending with ``is_final=True``."""

    async def translate(self, text: str, context: list[str] | None = None) -> str:
        parts: list[str] = []
        async for chunk in self.translate_stream(text, context):
            parts.append(chunk.text)
        return "".join(parts)
