"""Print the domestic cascade cost model for common session lengths."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rti.pipeline.cost import full_chain_cost_usd, omni_cost_cny  # noqa: E402

CNY_PER_USD = 7.1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Domestic cascade cost report")
    parser.add_argument("--cny-rate", type=float, default=CNY_PER_USD)
    args = parser.parse_args(argv)
    rows = [
        ("1 小时", 1.0),
        ("8 小时（1 天）", 8.0),
        ("176 小时（22 天 × 8 小时）", 8.0 * 22),
    ]
    print("=== 级联（ASR + DeepSeek + TTS）===")
    print(f"{'时长':<26} {'USD':>9} {'CNY':>10}")
    for label, hours in rows:
        usd = full_chain_cost_usd(hours)
        print(f"{label:<26} {usd:>9.4f} {usd * args.cny_rate:>10.2f}")
    print("\n=== Omni 直达（S2S + 复刻音色）===")
    print(f"{'时长':<26} {'CNY':>10}")
    for label, hours in rows:
        print(f"{label:<26} {omni_cost_cny(hours):>10.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
