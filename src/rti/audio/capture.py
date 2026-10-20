"""Microphone capture (WASAPI via PortAudio/sounddevice)."""
from __future__ import annotations

from typing import Callable

import numpy as np


def _sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - hardware dependent
        raise RuntimeError("sounddevice is required for microphone capture") from exc
    return sd


def list_input_devices() -> list[dict]:
    sd = _sounddevice()
    result: list[dict] = []
    for index, device in enumerate(sd.query_devices()):
        if device.get("max_input_channels", 0) > 0:
            result.append(
                {
                    "index": index,
                    "name": device["name"],
                    "channels": device["max_input_channels"],
                    "default_samplerate": device["default_samplerate"],
                }
            )
    return result


class MicCapture:
    def __init__(
        self,
        sample_rate: int = 48000,
        block_ms: int = 40,
        device: int | str | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.device = device
        self._stream = None
        self._on_block: Callable[[np.ndarray], None] | None = None

    def start(self, on_block: Callable[[np.ndarray], None]) -> None:
        sd = _sounddevice()
        self._on_block = on_block
        blocksize = max(1, int(self.sample_rate * self.block_ms / 1000))
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=blocksize,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:  # pragma: no cover
        if status:
            return
        if self._on_block is not None:
            self._on_block(np.asarray(indata[:, 0], dtype=np.float32).copy())

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "MicCapture":
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
