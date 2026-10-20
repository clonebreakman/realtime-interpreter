"""Provider selection.  Real adapters are added in Phase 0."""
from __future__ import annotations

from rti.providers.base import ProviderConfig, ProviderError, RealtimeProvider
from rti.providers.mock import MockProvider
from rti.providers.omni import OmniRealtimeProvider

_IMPLEMENTED: dict[str, type[RealtimeProvider]] = {
    "mock": MockProvider,
    "omni": OmniRealtimeProvider,
    "bailian-omni": OmniRealtimeProvider,
}

# Names accepted by configuration but not wired to a vendor protocol yet.
# Keeping them explicit makes a typo fail differently from a planned adapter.
_KNOWN = {
    "aliyun",
    "tencent",
    "xunfei",
    "camb",
    "azure",
    "palabra",
    "elevenlabs",
    "deepgram",
    "assemblyai",
}


def available_providers() -> list[str]:
    return sorted(set(_IMPLEMENTED) | _KNOWN)


def create_provider(name: str, config: ProviderConfig | None = None) -> RealtimeProvider:
    key = (name or "").strip().lower()
    if key in _IMPLEMENTED:
        return _IMPLEMENTED[key](config)
    if key in _KNOWN:
        raise ProviderError(
            f"provider '{key}' adapter is planned for Phase 0; "
            "use 'mock' for dry runs or implement the adapter first"
        )
    raise ProviderError(f"unknown provider '{name}'")
