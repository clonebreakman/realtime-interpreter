"""DeepSeek V4.1 token pricing (USD per 1M tokens, official 2026-10 rates)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeepSeekPricing:
    input_cache_hit: float
    input_cache_miss: float
    output: float


# deepseek-flash = DeepSeek-V4.1-Flash-1017, off-peak USD per 1M tokens.
FLASH_OFF_PEAK = DeepSeekPricing(input_cache_hit=0.003, input_cache_miss=0.15, output=0.6)
# deepseek-v4-pro = DeepSeek-V4-Pro-0813, off-peak USD per 1M tokens.
PRO_OFF_PEAK = DeepSeekPricing(input_cache_hit=0.022, input_cache_miss=0.66, output=1.98)


def deepseek_cost_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    peak: bool = False,
    pricing: DeepSeekPricing = FLASH_OFF_PEAK,
) -> float:
    if input_tokens < 0 or output_tokens < 0 or cache_hit_tokens < 0:
        raise ValueError("token counts must be non-negative")
    cache_hit = min(cache_hit_tokens, input_tokens)
    cache_miss = input_tokens - cache_hit
    cost = (
        cache_hit * pricing.input_cache_hit
        + cache_miss * pricing.input_cache_miss
        + output_tokens * pricing.output
    ) / 1_000_000
    return cost * 2.0 if peak else cost


def meeting_cost_usd(
    minutes: float,
    *,
    input_tokens_per_min: int = 250,
    output_tokens_per_min: int = 250,
    **kwargs,
) -> float:
    if minutes < 0:
        raise ValueError("minutes must be non-negative")
    return deepseek_cost_usd(
        int(minutes * input_tokens_per_min),
        int(minutes * output_tokens_per_min),
        **kwargs,
    )


# Bailian (Beijing) list prices used by the domestic cascade, 2026-10.
BAILIAN_ASR_USD_PER_SECOND = 0.000047
BAILIAN_COSYVOICE_USD_PER_10K_CHARS = 0.116


def full_chain_cost_usd(
    hours: float,
    *,
    characters_per_hour: float = 45_000,
    asr_usd_per_second: float = BAILIAN_ASR_USD_PER_SECOND,
    tts_usd_per_10k_chars: float = BAILIAN_COSYVOICE_USD_PER_10K_CHARS,
    input_tokens_per_min: int = 250,
    output_tokens_per_min: int = 250,
    peak: bool = False,
) -> float:
    """Cost of ASR + DeepSeek MT + cloned TTS for a session of ``hours``."""
    if hours < 0:
        raise ValueError("hours must be non-negative")
    asr = hours * 3600.0 * asr_usd_per_second
    tts = (hours * characters_per_hour / 10_000.0) * tts_usd_per_10k_chars
    mt = meeting_cost_usd(
        hours * 60.0,
        input_tokens_per_min=input_tokens_per_min,
        output_tokens_per_min=output_tokens_per_min,
        peak=peak,
    )
    return asr + tts + mt


def session_cost_usd(
    asr_seconds: float,
    tts_chars: float,
    input_tokens: int,
    output_tokens: int,
    *,
    asr_usd_per_second: float = BAILIAN_ASR_USD_PER_SECOND,
    tts_usd_per_10k_chars: float = BAILIAN_COSYVOICE_USD_PER_10K_CHARS,
    peak: bool = False,
) -> float:
    """Actual cost of one session from measured seconds/chars/tokens."""
    if asr_seconds < 0 or tts_chars < 0 or input_tokens < 0 or output_tokens < 0:
        raise ValueError("usage counters must be non-negative")
    return (
        asr_seconds * asr_usd_per_second
        + (tts_chars / 10_000.0) * tts_usd_per_10k_chars
        + deepseek_cost_usd(input_tokens, output_tokens, peak=peak)
    )


# Qwen3.8-Omni-Flash-Realtime (Beijing), CNY list prices, 2026-10.
OMNI_AUDIO_TOKENS_PER_SECOND = 25
OMNI_CNY_PER_MILLION_AUDIO_IN = 6.0
OMNI_CNY_PER_MILLION_AUDIO_OUT = 12.0
OMNI_CNY_PER_MILLION_TEXT_OUT = 4.5


def omni_cost_cny(
    hours: float,
    *,
    text_output_tokens_per_hour: float = 15_000,
) -> float:
    """Single-API Omni S2S cost: audio in/out + assistant text output."""
    if hours < 0:
        raise ValueError("hours must be non-negative")
    audio_tokens = hours * 3600.0 * OMNI_AUDIO_TOKENS_PER_SECOND
    audio = audio_tokens / 1_000_000.0 * (
        OMNI_CNY_PER_MILLION_AUDIO_IN + OMNI_CNY_PER_MILLION_AUDIO_OUT
    )
    text = text_output_tokens_per_hour * hours / 1_000_000.0 * OMNI_CNY_PER_MILLION_TEXT_OUT
    return audio + text

