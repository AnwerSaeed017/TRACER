"""A resolved ontology: one chain of package versions merged into a single vocabulary.

Built from packages in dependency order (core first, the tenant package last). Construction
checks everything that spans packages and raises ``OntologyError`` listing every problem:

- no key defined twice (a tenant cannot redefine a core type or relation);
- every parent, extension target and relation endpoint exists in the chain;
- inheritance has no cycles; no attribute overrides an inherited or already-defined one.

Then it validates values for the graph: entity properties (unknown, missing, ill-typed,
out-of-range attributes; abstract types) and relation endpoints and properties, with subtypes
accepted wherever their parent is.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from tracer.modules.knowledge.domain.attributes import AttributeDefinition
from tracer.modules.knowledge.domain.ontology import (
    EntityTypeDefinition,
    OntologyError,
    OntologyPackageId,
    PublishedPackage,
    RelationTypeDefinition,
)
from tracer.shared.identifiers import TenantId


@dataclass(frozen=True, slots=True)
class ResolvedEntityType:
    key: str
    definition: EntityTypeDefinition
    package_id: OntologyPackageId
    ancestors: tuple[str, ...]  # nearest first
    attributes: Mapping[str, AttributeDefinition]  # own + inherited + extensions

    @property
    def abstract(self) -> bool:
        return self.definition.abstract


@dataclass(frozen=True, slots=True)
class ResolvedRelationType:
    key: str
    definition: RelationTypeDefinition
    package_id: OntologyPackageId


class ResolvedOntology:
    def __init__(self, packages: Sequence[PublishedPackage]) -> None:
        if not packages:
            raise OntologyError(["an ontology needs at least one package"])
        self._packages = tuple(packages)
        problems: list[str] = []
        keys = [p.key for p in self._packages]
        if len(keys) != len(set(keys)):
            problems.append(f"two versions of the same package in one ontology: {sorted(keys)}")

        definitions: dict[str, tuple[EntityTypeDefinition, PublishedPackage]] = {}
        relations: dict[str, ResolvedRelationType] = {}
        extensions: dict[str, list[AttributeDefinition]] = {}
        for package in self._packages:
            for definition in package.content.entity_types:
                if definition.key in definitions:
                    owner = definitions[definition.key][1].label()
                    problems.append(f"{definition.key} is already defined by {owner}")
                    continue
                definitions[definition.key] = (definition, package)
            for extension in package.content.extensions:
                if extension.type_key not in definitions:
                    problems.append(f"extension of unknown type {extension.type_key}")
                    continue
                extensions.setdefault(extension.type_key, []).extend(extension.attributes)
            for relation in package.content.relation_types:
                if relation.key in relations:
                    problems.append(f"relation {relation.key} is already defined")
                    continue
                relations[relation.key] = ResolvedRelationType(relation.key, relation, package.id)

        resolved: dict[str, ResolvedEntityType] = {}
        for key in definitions:
            self._resolve(key, definitions, extensions, resolved, (), problems)
        for registered in relations.values():
            endpoints = registered.definition.source_types | registered.definition.target_types
            problems += [
                f"relation {registered.key} refers to unknown type {endpoint}"
                for endpoint in endpoints
                if endpoint not in definitions
            ]
        if problems:
            raise OntologyError(sorted(set(problems)))
        self._types = MappingProxyType(resolved)
        self._relations = MappingProxyType(relations)

    def _resolve(
        self,
        key: str,
        definitions: Mapping[str, tuple[EntityTypeDefinition, PublishedPackage]],
        extensions: Mapping[str, list[AttributeDefinition]],
        resolved: dict[str, ResolvedEntityType],
        visiting: tuple[str, ...],
        problems: list[str],
    ) -> ResolvedEntityType | None:
        if key in resolved:
            return resolved[key]
        if key in visiting:
            problems.append(f"inheritance cycle: {' -> '.join((*visiting, key))}")
            return None
        definition, package = definitions[key]
        inherited: dict[str, AttributeDefinition] = {}
        ancestors: tuple[str, ...] = ()
        if definition.parent is not None:
            if definition.parent not in definitions:
                problems.append(f"{key}: unknown parent type {definition.parent}")
                return None
            parent = self._resolve(
                definition.parent, definitions, extensions, resolved, (*visiting, key), problems
            )
            if parent is None:
                return None
            inherited = dict(parent.attributes)
            ancestors = (parent.key, *parent.ancestors)
        attributes = dict(inherited)
        for attribute in (*definition.attributes, *extensions.get(key, ())):
            if attribute.name in attributes:
                problems.append(f"{key}: attribute {attribute.name} is already defined")
                continue
            attributes[attribute.name] = attribute
        result = ResolvedEntityType(
            key, definition, package.id, ancestors, MappingProxyType(attributes)
        )
        resolved[key] = result
        return result

    # ------------------------------------------------------------------ queries

    @property
    def packages(self) -> tuple[PublishedPackage, ...]:
        return self._packages

    @property
    def package_ids(self) -> tuple[OntologyPackageId, ...]:
        return tuple(p.id for p in self._packages)

    @property
    def tenant_id(self) -> TenantId | None:
        return next((p.content.tenant_id for p in self._packages if p.content.tenant_id), None)

    @property
    def snapshot_id(self) -> OntologyPackageId:
        """Identifies this exact ontology: the leaf package. Every package pins its dependencies
        and published packages never change, so the leaf id fixes the whole closure. This is
        what a graph write records as "validated under"."""
        return self._packages[-1].id

    @property
    def snapshot_fingerprint(self) -> str:
        """SHA-256 over the chain's package fingerprints, in order: verifies the whole closure."""
        material = "|".join(f"{p.id}:{p.fingerprint}" for p in self._packages)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def creation_problems(self, type_key: str) -> list[str]:
        """Why a *new* entity of this type may not be created ([] if it may).

        Deprecation semantics: existing entities of a deprecated type stay valid, readable and
        traversable; new ones are refused; nothing deprecated is ever removed.
        """
        resolved = self._types.get(type_key)
        if resolved is None:
            return [f"unknown entity type {type_key}"]
        problems = []
        if resolved.abstract:
            problems.append(f"{type_key} is abstract and cannot have instances")
        if resolved.definition.deprecated:
            problems.append(
                f"{type_key} is deprecated: existing entities remain, new ones are refused"
            )
        return problems

    def relation_creation_problems(self, relation_key: str) -> list[str]:
        """Why a *new* relation of this type may not be created; same deprecation semantics."""
        relation = self._relations.get(relation_key)
        if relation is None:
            return [f"unknown relation type {relation_key}"]
        if relation.definition.deprecated:
            return [
                f"{relation_key} is deprecated: existing relations remain, new ones are refused"
            ]
        return []

    @property
    def entity_type_keys(self) -> frozenset[str]:
        return frozenset(self._types)

    @property
    def relation_type_keys(self) -> frozenset[str]:
        return frozenset(self._relations)

    def entity_type(self, key: str) -> ResolvedEntityType:
        try:
            return self._types[key]
        except KeyError:
            raise OntologyError([f"unknown entity type {key}"]) from None

    def relation_type(self, key: str) -> ResolvedRelationType:
        try:
            return self._relations[key]
        except KeyError:
            raise OntologyError([f"unknown relation type {key}"]) from None

    def is_a(self, type_key: str, ancestor: str) -> bool:
        resolved = self._types.get(type_key)
        return resolved is not None and (type_key == ancestor or ancestor in resolved.ancestors)

    # ------------------------------------------------------------------ validation

    def validate_properties(self, type_key: str, properties: Mapping[str, object]) -> list[str]:
        """Every reason ``properties`` are not valid for an entity of ``type_key``; [] if valid."""
        resolved = self._types.get(type_key)
        if resolved is None:
            return [f"unknown entity type {type_key}"]
        problems = (
            [f"{type_key} is abstract and cannot have instances"] if resolved.abstract else []
        )
        return problems + _check(resolved.attributes, properties, type_key)

    def validate_relation(
        self,
        relation_key: str,
        source_type: str,
        target_type: str,
        properties: Mapping[str, object],
    ) -> list[str]:
        relation = self._relations.get(relation_key)
        if relation is None:
            return [f"unknown relation type {relation_key}"]
        problems = []
        if not any(self.is_a(source_type, allowed) for allowed in relation.definition.source_types):
            problems.append(
                f"{relation_key} cannot start at {source_type} "
                f"(allowed: {sorted(relation.definition.source_types)})"
            )
        if not any(self.is_a(target_type, allowed) for allowed in relation.definition.target_types):
            problems.append(
                f"{relation_key} cannot end at {target_type} "
                f"(allowed: {sorted(relation.definition.target_types)})"
            )
        attributes = {a.name: a for a in relation.definition.attributes}
        return problems + _check(attributes, properties, relation_key)


def _check(
    # ``object``: properties arrive from JSON, so "is it an object at all?" is a real check.
    attributes: Mapping[str, AttributeDefinition],
    properties: object,
    owner: str,
) -> list[str]:
    if not isinstance(properties, Mapping):
        return [f"{owner}: properties must be an object"]
    problems = [
        f"{owner}: unknown attribute {name}" for name in properties if name not in attributes
    ]
    for name, attribute in attributes.items():
        if name not in properties:
            if attribute.required:
                problems.append(f"{owner}: missing required attribute {name}")
            continue
        problem = attribute.check(properties[name])
        if problem is not None:
            problems.append(f"{owner}.{problem}")
    return problems
