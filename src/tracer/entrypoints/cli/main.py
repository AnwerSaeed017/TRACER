"""The ``tracer`` command.

tracer serve [--reload]    run the HTTP API
"""

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from pydantic import ValidationError

from tracer.platform.configuration.settings import load_settings
from tracer.platform.observability.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracer")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ValidationError as exc:
        sys.stderr.write(f"Invalid configuration (environment or env/.env):\n{exc}\n")
        return 1

    configure_logging(settings.log_level, settings.log_format)
    if args.command == "serve":
        uvicorn.run(
            "tracer.entrypoints.http.app:build_app",
            factory=True,
            host=settings.http_host,
            port=settings.http_port,
            reload=args.reload,
            log_config=None,  # keep our logging configuration
            access_log=False,  # the request middleware logs requests
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
