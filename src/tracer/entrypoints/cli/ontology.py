"""``tracer ontology publish-core``: publish the core ontology from code. Safe to run on every
deployment: identical content is not published again."""

import getpass
import sys

from tracer.bootstrap.composition_root import build_container
from tracer.modules.knowledge.domain.core_ontology import CORE_ONTOLOGY
from tracer.platform.configuration.settings import Settings


async def publish_core(settings: Settings) -> int:
    container = build_container(settings)
    try:
        result = await container.ontology_registry.publish(
            CORE_ONTOLOGY, published_by=getpass.getuser()
        )
    finally:
        await container.aclose()
    state = "published" if result.created else "already current"
    sys.stdout.write(f"{result.package.label()} {state} ({result.package.id})\n")
    return 0
