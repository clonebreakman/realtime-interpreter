"""Build the Windows desktop app with PyInstaller."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if not (ROOT / "web" / "index.html").is_file():
        print("web/index.html is missing; build the frontend first", file=sys.stderr)
        return 2
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "packaging" / "rti.spec"),
    ]
    print(" ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)
    config = ROOT / "config.json"
    target = ROOT / "dist" / "RTI" / "config.json"
    if config.is_file():
        shutil.copy2(config, target)
        print(f"config copied: {target}")
    print(f"\nbuilt: {ROOT / 'dist' / 'RTI' / 'RTI.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
