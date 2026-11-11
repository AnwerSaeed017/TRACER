"""Can version n+1 of a package replace version n without invalidating data?

Data valid under the previous version must stay valid under the new one, so a new version may
only *add*: new types, new relations, new optional attributes, wider relation endpoints, and
deprecation marks. Everything else is reported, and the publish is refused.
"""

from collections.abc import Iterable

from tracer.modules.knowledge.domain.attributes import AttributeDefinition
from tracer.modules.knowledge.domain.ontology import PackageContent


def _attributes(
    owner: str, before: Iterable[AttributeDefinition], after: Iterable[AttributeDefinition]
) -> list[str]:
    previous = {a.name: a for a in before}
    current = {a.name: a for a in after}
    problems = []
    for name, attribute in previous.items():
        if name not in current:
            problems.append(f"{owner}: attribute {name} was removed")
        elif not attribute.same_contract_as(current[name]):
            problems.append(f"{owner}: attribute {name} changed its definition")
    problems += [
        f"{owner}: new attribute {name} must be optional"
        for name, attribute in current.items()
        if name not in previous and attribute.required
    ]
    return problems


def incompatibilities(previous: PackageContent, new: PackageContent) -> list[str]:
    if (previous.key, previous.scope, previous.namespace, previous.tenant_id) != (
        new.key,
        new.scope,
        new.namespace,
        new.tenant_id,
    ):
        return ["a new version must keep the package key, scope, namespace and tenant"]
    problems: list[str] = []

    types = {t.key: t for t in new.entity_types}
    for old in previous.entity_types:
        current = types.get(old.key)
        if current is None:
            problems.append(f"entity type {old.key} was removed (mark it deprecated instead)")
            continue
        if current.parent != old.parent:
            problems.append(f"{old.key}: parent changed from {old.parent} to {current.parent}")
        if current.abstract and not old.abstract:
            problems.append(f"{old.key}: became abstract, existing entities would be invalid")
        problems += _attributes(old.key, old.attributes, current.attributes)

    extensions = {e.type_key: e for e in new.extensions}
    for old_extension in previous.extensions:
        current_extension = extensions.get(old_extension.type_key)
        if current_extension is None:
            problems.append(f"extension of {old_extension.type_key} was removed")
            continue
        problems += _attributes(
            f"extension of {old_extension.type_key}",
            old_extension.attributes,
            current_extension.attributes,
        )

    relations = {r.key: r for r in new.relation_types}
    for old_relation in previous.relation_types:
        current_relation = relations.get(old_relation.key)
        if current_relation is None:
            problems.append(f"relation {old_relation.key} was removed (mark it deprecated instead)")
            continue
        for side in ("source_types", "target_types"):
            lost = getattr(old_relation, side) - getattr(current_relation, side)
            if lost:
                problems.append(f"{old_relation.key}: {side} no longer accept {sorted(lost)}")
        problems += _attributes(
            old_relation.key, old_relation.attributes, current_relation.attributes
        )
    return problems
