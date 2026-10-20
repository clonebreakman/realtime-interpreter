"""List Windows audio devices and locate VB-Cable."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rti.audio.playback import find_device, list_output_devices  # noqa: E402
from rti.audio.capture import list_input_devices  # noqa: E402


def main() -> int:
    print("== input devices ==")
    for device in list_input_devices():
        print(f"{device['index']:>3}  {device['name']}  ({device['channels']}ch)")
    print("== output devices ==")
    for device in list_output_devices():
        print(f"{device['index']:>3}  {device['name']}  ({device['channels']}ch)")
    cable = find_device("CABLE", kind="output")
    print(f"VB-Cable input endpoint: {cable if cable is not None else 'not found'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
