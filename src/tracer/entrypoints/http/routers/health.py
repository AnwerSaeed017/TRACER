"""Liveness and readiness.

They answer different questions, and an orchestrator (Docker, Kubernetes, a load balancer)
acts differently on each:

- live:  "is the process stuck?"  Failing means restart it. Checks nothing external, because a
          database outage must not make every replica restart in a loop.
- ready: "can it serve traffic now?"  Failing means stop sending requests until it recovers.
"""

from fastapi import APIRouter, Response, status

from tracer.entrypoints.http.dependencies import ContainerDep
from tracer.entrypoints.http.schemas.health import CheckResult, LivenessResponse, ReadinessResponse
from tracer.shared.health import run_health_checks

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", summary="The process is running")
async def live() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/ready",
    summary="Every dependency needed to serve requests is available",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(container: ContainerDep, response: Response) -> ReadinessResponse:
    results = await run_health_checks(
        container.readiness_checks, container.settings.readiness_timeout_seconds
    )
    is_ready = all(result.healthy for result in results)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        checks=[CheckResult(name=r.name, healthy=r.healthy, detail=r.detail) for r in results],
    )
