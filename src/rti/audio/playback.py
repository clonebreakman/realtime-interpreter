"""Playback to a speaker or the VB-Cable virtual microphone."""
from __future__ import annotations

import numpy as np


def _sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - hardware dependent
        raise RuntimeError("sounddevice is required for playback") from exc
    return sd


def list_output_devices() -> list[dict]:
    sd = _sounddevice()
    result: list[dict] = []
    for index, device in enumerate(sd.query_devices()):
        if device.get("max_output_channels", 0) > 0:
            result.append(
                {
                    "index": index,
                    "name": device["name"],
                    "channels": device["max_output_channels"],
                    "default_samplerate": device["default_samplerate"],
                }
            )
    return result


def find_device(name_fragment: str, kind: str = "output") -> int | None:
    sd = _sounddevice()
    key = "max_output_channels" if kind == "output" else "max_input_channels"
    for index, device in enumerate(sd.query_devices()):
        if device.get(key, 0) > 0 and name_fragment.lower() in device["name"].lower():
            return index
    return None


class PCMPlayer:
    def __init__(
        self,
        sample_rate: int = 24000,
        block_ms: int = 40,
        device: int | str | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.device = device
        self._stream = None

    def start(self) -> None:
        sd = _sounddevice()
        blocksize = max(1, int(self.sample_rate * self.block_ms / 1000))
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=blocksize,
            device=self.device,
        )
        self._stream.start()

    def write(self, pcm: np.ndarray) -> None:
        if self._stream is None:
            raise RuntimeError("player is not started")
        self._stream.write(np.asarray(pcm, dtype=np.float32))

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "PCMPlayer":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
