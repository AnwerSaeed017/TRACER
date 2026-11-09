"""The FastAPI application.

``create_app`` takes an already-built container, which is what makes the app testable: a
test builds a container with fakes and passes it in. ``build_app`` is the production path
(settings, logging, real container) and is what uvicorn calls.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from tracer.bootstrap.composition_root import Container, build_container
from tracer.entrypoints.http.error_handlers import register_error_handlers
from tracer.entrypoints.http.middleware import RequestContextMiddleware
from tracer.entrypoints.http.routers import health, webhooks
from tracer.platform.configuration.settings import load_settings
from tracer.platform.observability.logging import configure_logging

logger = logging.getLogger("tracer")


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("tracer started", extra={"environment": container.settings.environment.value})
        try:
            yield
        finally:
            await container.aclose()
            logger.info("tracer stopped")

    production = container.settings.is_production
    app = FastAPI(
        title="TRACER",
        summary="Temporal Risk Analysis, Causal Evaluation & Response",
        version=version("tracer"),
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.container = container
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(webhooks.router)
    return app


def build_app() -> FastAPI:
    """Uvicorn factory: ``uvicorn tracer.entrypoints.http.app:build_app --factory``."""
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    return create_app(build_container(settings))
