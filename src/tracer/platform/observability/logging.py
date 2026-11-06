"""Logging, configured once at start-up, written to stdout.

Two formats: ``json`` (one object per line, for machines and log pipelines) and
``console`` (readable while developing). Both carry the request id, and both include any
``extra={...}`` fields a caller attaches, so logs stay structured without a third-party
library.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from tracer.platform.configuration.settings import LogFormat, LogLevel
from tracer.platform.observability.context import request_id_var

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName", "request_id", "color_message"}  # uvicorn's ANSI copy
)

# Libraries that log routine INFO lines; only their warnings are worth reading. Alembic,
# for example, logs "Context impl PostgresqlImpl" every time readiness reads the revision.
_QUIET_LIBRARIES = ("alembic",)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRIBUTES}


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")


class RequestIdFilter(logging.Filter):
    """Stamp the current request id (or None) onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or "-"
        extras = " ".join(f"{k}={v}" for k, v in _extra_fields(record).items())
        line = (
            f"{_timestamp(record)} {record.levelname:<7} {record.name} "
            f"[{request_id}] {record.getMessage()}"
        )
        if extras:
            line = f"{line} | {extras}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    """Route every logger (ours, uvicorn's, SQLAlchemy's) through one stdout handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if log_format is LogFormat.JSON else ConsoleFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # Our request middleware logs every request with its id and duration; uvicorn's
    # access log would duplicate it without either.
    logging.getLogger("uvicorn.access").disabled = True
    for name in _QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
