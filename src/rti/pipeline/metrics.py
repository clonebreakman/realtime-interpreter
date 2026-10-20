"""Latency instrumentation: speech end -> first translated audio packet."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class LatencySample:
    segment_id: str
    speech_end_ts: float
    first_audio_ts: float
    latency_ms: float


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


class LatencyRecorder:
    def __init__(self) -> None:
        self._pending: dict[str, float] = {}
        self._samples: list[LatencySample] = []

    @property
    def samples(self) -> tuple[LatencySample, ...]:
        return tuple(self._samples)

    def mark_speech_end(self, segment_id: str, ts: float | None = None) -> None:
        self._pending[segment_id] = time.monotonic() if ts is None else float(ts)

    def mark_first_audio(self, segment_id: str, ts: float | None = None) -> LatencySample | None:
        end = self._pending.pop(segment_id, None)
        if end is None:
            return None
        first = time.monotonic() if ts is None else float(ts)
        sample = LatencySample(
            segment_id=segment_id,
            speech_end_ts=end,
            first_audio_ts=first,
            latency_ms=(first - end) * 1000.0,
        )
        self._samples.append(sample)
        return sample

    def summary(self) -> dict[str, float | int | None]:
        values = [sample.latency_ms for sample in self._samples]
        return {
            "count": len(values),
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "min_ms": min(values) if values else None,
            "max_ms": max(values) if values else None,
        }

    def to_jsonl(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for sample in self._samples:
                handle.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")
        return target


_STAGE_FIELDS = ("asr_final", "mt_first_token", "tts_first_chunk", "first_audio")


@dataclass(slots=True)
class StageLatency:
    segment_id: str
    speech_end_ts: float
    asr_final_ts: float | None = None
    mt_first_token_ts: float | None = None
    tts_first_chunk_ts: float | None = None
    first_audio_ts: float | None = None

    def latency_ms(self) -> float | None:
        if self.first_audio_ts is None:
            return None
        return (self.first_audio_ts - self.speech_end_ts) * 1000.0

    def as_dict(self) -> dict[str, float | str | None]:
        row = asdict(self)
        row["latency_ms"] = self.latency_ms()
        return row


class StageLatencyRecorder:
    """Per-segment stage timestamps for the ASR -> MT -> TTS cascade."""

    def __init__(self) -> None:
        self._stages: dict[str, StageLatency] = {}
        self._samples: list[StageLatency] = []

    @property
    def samples(self) -> tuple[StageLatency, ...]:
        return tuple(self._samples)

    def begin(self, segment_id: str, speech_end_ts: float | None = None) -> None:
        self._stages[segment_id] = StageLatency(
            segment_id=segment_id,
            speech_end_ts=time.monotonic() if speech_end_ts is None else float(speech_end_ts),
        )

    def mark(self, segment_id: str, stage: str, ts: float | None = None) -> None:
        if stage not in _STAGE_FIELDS:
            raise ValueError(f"unknown latency stage {stage!r}")
        sample = self._stages.get(segment_id)
        if sample is None:
            raise KeyError(f"unknown segment {segment_id!r}")
        setattr(
            sample,
            f"{stage}_ts",
            time.monotonic() if ts is None else float(ts),
        )

    def finish(self, segment_id: str) -> StageLatency | None:
        sample = self._stages.pop(segment_id, None)
        if sample is None:
            return None
        if sample.first_audio_ts is not None:
            self._samples.append(sample)
        return sample

    def summary(self) -> dict[str, float | int | None]:
        values = [
            sample.latency_ms()
            for sample in self._samples
            if sample.latency_ms() is not None
        ]
        return {
            "count": len(values),
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "min_ms": min(values) if values else None,
            "max_ms": max(values) if values else None,
        }

    def to_jsonl(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for sample in self._samples:
                handle.write(json.dumps(sample.as_dict(), ensure_ascii=False) + "\n")
        return target
