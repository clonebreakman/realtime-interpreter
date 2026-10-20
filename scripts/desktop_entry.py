"""PyInstaller entry point for the desktop app."""
from __future__ import annotations

import argparse

from rti.server.app import run


def main() -> None:
    parser = argparse.ArgumentParser(description="RTI desktop")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--ssl-keyfile", default=None)
    parser.add_argument("--ssl-certfile", default=None)
    args = parser.parse_args()
    run(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        ssl_keyfile=args.ssl_keyfile,
        ssl_certfile=args.ssl_certfile,
    )


if __name__ == "__main__":
    main()
