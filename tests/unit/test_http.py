"""The HTTP layer, with fake dependencies: no database, no network."""

from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeHealthCheck
from tests.support import make_settings
from tracer.bootstrap.composition_root import build_container
from tracer.entrypoints.http.app import create_app
from tracer.entrypoints.http.middleware import REQUEST_ID_HEADER
from tracer.entrypoints.http.problems import PROBLEM_MEDIA_TYPE
from tracer.shared.health import HealthCheck


def _app(*checks: HealthCheck, environment: str = "test") -> FastAPI:
    container = build_container(make_settings(environment=environment))
    return create_app(replace(container, readiness_checks=checks))


def test_live_is_ok_without_touching_dependencies() -> None:
    with TestClient(_app(FakeHealthCheck(healthy=False))) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_every_check_passes() -> None:
    with TestClient(_app(FakeHealthCheck("database", detail="revision 0001"))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": [{"name": "database", "healthy": True, "detail": "revision 0001"}],
    }


def test_not_ready_returns_503_and_names_the_failing_check() -> None:
    app = _app(FakeHealthCheck("database", healthy=False, detail="down"), FakeHealthCheck("x"))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"][0] == {"name": "database", "healthy": False, "detail": "down"}


def test_every_response_carries_a_generated_request_id() -> None:
    with TestClient(_app()) as client:
        first = client.get("/health/live").headers[REQUEST_ID_HEADER]
        second = client.get("/health/live").headers[REQUEST_ID_HEADER]
    assert len(first) == 32
    assert first != second


def test_a_valid_caller_request_id_is_kept() -> None:
    with TestClient(_app()) as client:
        response = client.get("/health/live", headers={REQUEST_ID_HEADER: "trace-abc.1"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc.1"


def test_an_unsafe_caller_request_id_is_replaced() -> None:
    with TestClient(_app()) as client:
        response = client.get("/health/live", headers={REQUEST_ID_HEADER: "x\nforged log line"})
    assert response.headers[REQUEST_ID_HEADER] != "x\nforged log line"
    assert len(response.headers[REQUEST_ID_HEADER]) == 32


def test_unknown_route_is_a_problem_document() -> None:
    with TestClient(_app()) as client:
        response = client.get("/nope", headers={REQUEST_ID_HEADER: "req-404"})
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "instance": "/nope",
        "request_id": "req-404",
    }


def test_invalid_input_is_422_without_echoing_the_input() -> None:
    app = _app()

    @app.get("/echo")
    async def echo(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    with TestClient(app) as client:
        response = client.get("/echo", params={"quantity": "secret-token-xyz"})
    assert response.status_code == 422
    body = response.json()
    assert body["instance"] == "/echo"  # path only: the query string is not reflected
    assert body["errors"][0]["loc"] == ["query", "quantity"]
    assert "secret-token-xyz" not in response.text


def test_unexpected_error_is_a_generic_500_with_request_id() -> None:
    app = _app()

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("connection string postgres://admin:pw@db")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/explode", headers={REQUEST_ID_HEADER: "req-500"})
    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "req-500"
    assert response.json()["detail"] == "An unexpected error occurred."
    assert "admin:pw" not in response.text


def test_api_docs_are_hidden_in_production() -> None:
    with TestClient(_app(environment="production")) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    with TestClient(_app()) as client:
        assert client.get("/openapi.json").status_code == 200
