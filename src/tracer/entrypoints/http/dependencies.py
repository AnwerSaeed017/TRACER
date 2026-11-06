"""FastAPI dependencies: how an endpoint receives what the composition root built."""

from typing import Annotated, cast

from fastapi import Depends, Request

from tracer.bootstrap.composition_root import Container


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]
