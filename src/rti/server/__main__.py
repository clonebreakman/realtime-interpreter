"""Run the local web app: python -m rti.server."""
from __future__ import annotations

import argparse

from rti.server.app import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RTI local web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--ssl-keyfile", default=None)
    parser.add_argument("--ssl-certfile", default=None)
    args = parser.parse_args(argv)
    run(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        ssl_keyfile=args.ssl_keyfile,
        ssl_certfile=args.ssl_certfile,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
