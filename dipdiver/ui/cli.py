"""dipdiver-ui — entry point for the operator console.

Usage:
    dipdiver-ui serve                 # localhost on default port
    dipdiver-ui serve --host 0.0.0.0  # bind all (container)
    dipdiver-ui serve --reload        # dev hot-reload
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from dipdiver.ui.settings import env_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dipdiver-ui")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI server")
    serve.add_argument("--host", default=None,
                       help="Bind address (default: env DIPDIVER_UI_HOST or 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None,
                       help="Port (default: env DIPDIVER_UI_PORT or 8765)")
    serve.add_argument("--reload", action="store_true",
                       help="Hot-reload on code change (dev only)")
    serve.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        env = env_settings()
        host = args.host or env.host
        port = args.port or env.port
        log_level = "DEBUG" if args.verbose else env.log_level
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        # Also write app logs to a file so the /logs page can show them (console
        # logs only reach `docker logs`). Re-applied in the app lifespan so it
        # still works under --reload / direct-uvicorn launches.
        from dipdiver.ui.logging_setup import setup_file_logging

        setup_file_logging(log_level)
        print(f"[dipdiver-ui] serving on http://{host}:{port}")
        uvicorn.run(
            "dipdiver.ui.app:app",
            host=host,
            port=port,
            reload=args.reload,
            log_level=log_level.lower(),
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
