"""Linear resampling; good enough for 48k -> 24k/16k before ASR upload."""
from __future__ import annotations

import numpy as np


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    source = np.asarray(samples, dtype=np.float32)
    if source.ndim != 1:
        raise ValueError("resample_linear expects mono audio")
    if src_rate == dst_rate:
        return source.copy()
    if source.size == 0:
        return source.copy()
    duration = source.size / float(src_rate)
    target_size = int(round(duration * dst_rate))
    if target_size <= 0:
        return np.zeros(0, dtype=np.float32)
    src_positions = np.linspace(0.0, source.size - 1, target_size)
    return np.interp(src_positions, np.arange(source.size), source).astype(np.float32)
