import json
import logging
import sys
from collections.abc import Iterator

import pytest

from tracer.platform.configuration.settings import LogFormat
from tracer.platform.observability.context import request_id_var
from tracer.platform.observability.logging import (
    ConsoleFormatter,
    JsonFormatter,
    RequestIdFilter,
    configure_logging,
)


def _record(message: str = "shipment delayed", **extra: object) -> logging.LogRecord:
    record = logging.getLogger("tracer.test").makeRecord(
        "tracer.test", logging.INFO, __file__, 1, message, (), None, extra=extra
    )
    RequestIdFilter().filter(record)
    return record


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_json_line_has_core_fields_extras_and_request_id() -> None:
    token = request_id_var.set("req-123")
    try:
        line = JsonFormatter().format(_record(shipment="SHIP-501", delay_days=7))
    finally:
        request_id_var.reset(token)

    payload = json.loads(line)
    assert payload["message"] == "shipment delayed"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "tracer.test"
    assert payload["request_id"] == "req-123"
    assert payload["shipment"] == "SHIP-501"
    assert payload["delay_days"] == 7
    assert payload["timestamp"].endswith("+00:00")


def test_json_line_omits_request_id_outside_a_request() -> None:
    payload = json.loads(JsonFormatter().format(_record()))
    assert "request_id" not in payload


def test_json_line_includes_exception() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "failed", (), exc_info=sys.exc_info()
        )
    payload = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in payload["exception"]


def test_console_line_is_readable() -> None:
    line = ConsoleFormatter().format(_record(order="ORD-101"))
    assert "INFO" in line
    assert "[-]" in line  # no request in progress
    assert "shipment delayed | order=ORD-101" in line


def test_configure_logging_writes_json_to_stdout(
    capsys: pytest.CaptureFixture[str], restore_root_logger: None
) -> None:
    configure_logging("INFO", LogFormat.JSON)
    logging.getLogger("tracer.any").info("hello", extra={"k": "v"})
    logging.getLogger("tracer.any").debug("hidden below INFO")

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["k"] == "v"
