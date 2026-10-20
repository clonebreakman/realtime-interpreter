"""Streaming TTS contract with zero-shot voice cloning."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import numpy as np


@dataclass(slots=True)
class TtsConfig:
    voice_id: str | None = None
    sample_rate: int = 24000
    model: str = "cosyvoice-v3.5-flash"
    endpoint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TtsChunk:
    audio: np.ndarray
    sample_rate: int
    text: str = ""
    is_final: bool = False


class TtsError(RuntimeError):
    """Raised when synthesis fails or is misconfigured."""


class TtsEngine(ABC):
    name = "base"

    def __init__(self, config: TtsConfig | None = None) -> None:
        self.config = config or TtsConfig()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @abstractmethod
    def synthesize(self, text: str) -> AsyncIterator[TtsChunk]:
        """Yield PCM chunks for one clause, ending with ``is_final=True``."""
