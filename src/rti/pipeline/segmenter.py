"""Clause boundary detection for low-latency TTS starts."""
from __future__ import annotations

_BOUNDARIES = set("，。！？；,.!?;:")
_MAX_CHARS_DEFAULT = 80


class ClauseSegmenter:
    """Accumulate streamed translation text and release speakable clauses."""

    def __init__(self, max_chars: int = _MAX_CHARS_DEFAULT) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        parts: list[str] = []
        while True:
            index = self._find_boundary()
            if index is None:
                break
            clause = self._buffer[: index + 1].strip()
            self._buffer = self._buffer[index + 1 :]
            if clause:
                parts.append(clause)
        while len(self._buffer) >= self.max_chars:
            clause = self._buffer[: self.max_chars].strip()
            self._buffer = self._buffer[self.max_chars :]
            if clause:
                parts.append(clause)
        return parts

    def flush(self) -> str | None:
        clause = self._buffer.strip()
        self._buffer = ""
        return clause or None

    def _find_boundary(self) -> int | None:
        for index, char in enumerate(self._buffer):
            if char not in _BOUNDARIES:
                continue
            if (
                char == "."
                and 0 < index < len(self._buffer) - 1
                and self._buffer[index - 1].isdigit()
                and self._buffer[index + 1].isdigit()
            ):
                continue
            return index
        return None
