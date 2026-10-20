"""One-shot DeepSeek translation smoke test.

Requires DEEPSEEK_API_KEY.  Example:
    python scripts/translate_once.py "我们今天讨论一下供应链的预算问题。"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rti.config import Settings  # noqa: E402
from rti.mt.base import TranslationConfig  # noqa: E402
from rti.mt.deepseek import DeepSeekTranslator  # noqa: E402


async def translate(text: str) -> int:
    settings = Settings.from_env()
    config = TranslationConfig(
        source_language=settings.source_language,
        target_language=settings.target_language,
        model=settings.deepseek_model,
    )
    translator = DeepSeekTranslator(
        config,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    started = time.monotonic()
    first_token_ms: float | None = None
    parts: list[str] = []
    async for chunk in translator.translate_stream(text):
        if chunk.text and first_token_ms is None:
            first_token_ms = (time.monotonic() - started) * 1000
        parts.append(chunk.text)
    total_ms = (time.monotonic() - started) * 1000
    print("".join(parts))
    print(f"first_token={first_token_ms:.0f}ms total={total_ms:.0f}ms")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSeek translation smoke test")
    parser.add_argument("text", nargs="?", default="我们今天讨论一下供应链的预算问题。")
    args = parser.parse_args(argv)
    return asyncio.run(translate(args.text))


if __name__ == "__main__":
    raise SystemExit(main())
