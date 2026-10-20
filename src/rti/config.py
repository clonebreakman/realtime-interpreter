"""Typed application settings loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from rti.providers.base import ProviderConfig


@dataclass(frozen=True, slots=True)
class Settings:
    provider: str = "mock"
    source_language: str = "zh-CN"
    target_language: str = "en-US"
    voice_id: str | None = None
    sample_rate: int = 24000
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    aliyun_api_key: str | None = None
    tencent_secret_id: str | None = None
    tencent_secret_key: str | None = None
    camb_api_key: str | None = None
    azure_speech_key: str | None = None
    azure_region: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env

        def get(key: str, default: str | None = None) -> str | None:
            return source.get(key, default)

        raw_rate = get("RTI_SAMPLE_RATE", "24000")
        try:
            sample_rate = int(raw_rate or "24000")
        except ValueError as exc:
            raise ValueError(f"RTI_SAMPLE_RATE must be an integer, got {raw_rate!r}") from exc
        if sample_rate <= 0:
            raise ValueError("RTI_SAMPLE_RATE must be positive")

        return cls(
            provider=get("RTI_PROVIDER", "mock") or "mock",
            source_language=get("RTI_SOURCE_LANGUAGE", "zh-CN") or "zh-CN",
            target_language=get("RTI_TARGET_LANGUAGE", "en-US") or "en-US",
            voice_id=get("RTI_VOICE_ID"),
            sample_rate=sample_rate,
            deepseek_api_key=get("DEEPSEEK_API_KEY"),
            deepseek_base_url=get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            or "https://api.deepseek.com",
            deepseek_model=get("DEEPSEEK_MODEL", "deepseek-flash") or "deepseek-flash",
            aliyun_api_key=get("ALIYUN_API_KEY") or get("DASHSCOPE_API_KEY"),
            tencent_secret_id=get("TENCENT_SECRET_ID"),
            tencent_secret_key=get("TENCENT_SECRET_KEY"),
            camb_api_key=get("CAMB_API_KEY"),
            azure_speech_key=get("AZURE_SPEECH_KEY"),
            azure_region=get("AZURE_SPEECH_REGION"),
        )

    def provider_config(self) -> ProviderConfig:
        return ProviderConfig(
            source_language=self.source_language,
            target_language=self.target_language,
            voice_id=self.voice_id,
            sample_rate=self.sample_rate,
        )
