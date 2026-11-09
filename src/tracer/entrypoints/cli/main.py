"""The ``tracer`` command.

tracer serve [--reload]            run the HTTP API
tracer worker                      deliver outbox events to their consumers
tracer outbox replay <event-id>    give a dead-lettered event a fresh start
"""

import argparse
import asyncio
import contextlib
import sys
from collections.abc import Sequence
from uuid import UUID

import uvicorn
from pydantic import ValidationError

from tracer.entrypoints.workers.outbox_worker import replay_dead_event, run_worker
from tracer.platform.configuration.settings import load_settings
from tracer.platform.observability.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracer")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    commands.add_parser("worker", help="deliver outbox events to their consumers")
    outbox = commands.add_parser("outbox", help="operate the outbox")
    outbox_commands = outbox.add_subparsers(dest="outbox_command", required=True)
    replay = outbox_commands.add_parser("replay", help="retry a dead-lettered event")
    replay.add_argument("event_id", type=UUID)
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
    elif args.command == "worker":
        with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C is the normal way to stop
            asyncio.run(run_worker(settings))
    elif args.command == "outbox" and not asyncio.run(replay_dead_event(settings, args.event_id)):
        sys.stderr.write(f"event {args.event_id} is not dead-lettered; nothing replayed\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
