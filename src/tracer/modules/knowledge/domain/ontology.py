"""Ontology packages: the definitions a package declares, and the published, immutable version.

    tracer.core (scope CORE)            plain keys          SUPPLIER, SUPPLIES
    <industry>  (scope INDUSTRY)        namespaced keys     mfg.PRODUCTION_BATCH
    tenant      (scope TENANT)          namespaced keys     acme.COLD_ROOM

A package declares entity types, extensions (optional attributes added to a type it does not
own) and relation types. Rules that need only the package itself are checked here; rules that
need its dependencies (does the parent exist? is a key redefined?) are checked when the chain
is resolved (``resolved.py``).
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from tracer.modules.knowledge.domain.attributes import AttributeDefinition
from tracer.shared.errors import BusinessRuleError, InvalidValueError
from tracer.shared.guards import require_text, require_utc
from tracer.shared.identifiers import EntityId, TenantId

CORE_PACKAGE_KEY: Final = "tracer.core"
TENANT_PACKAGE_KEY: Final = "tenant"
PACKAGE_KEY: Final = re.compile(r"^[a-z][a-z0-9_.-]{1,59}$")
NAMESPACE: Final = re.compile(r"^[a-z][a-z0-9]{1,15}$")
DEFINITION_KEY: Final = re.compile(
    r"^(?:(?P<namespace>[a-z][a-z0-9]{1,15})\.)?[A-Z][A-Z0-9_]{1,62}$"
)
MAX_DEFINITIONS: Final = 500


class OntologyPackageId(EntityId):
    __slots__ = ()


class OntologyError(BusinessRuleError):
    """An ontology (or a value checked against it) was refused; ``problems`` lists every reason."""

    def __init__(self, problems: list[str] | tuple[str, ...]) -> None:
        self.problems = tuple(problems)
        super().__init__("ontology rejected: " + "; ".join(self.problems))


class PackageScope(StrEnum):
    CORE = "core"
    INDUSTRY = "industry"
    TENANT = "tenant"


def namespace_of(key: str) -> str | None:
    match = DEFINITION_KEY.fullmatch(key)
    if match is None:
        raise InvalidValueError(
            f"{key!r} is not a valid definition key (UPPER_SNAKE, optionally ns.)"
        )
    return match.group("namespace")


def _attributes(
    attributes: tuple[AttributeDefinition, ...], owner: str
) -> tuple[AttributeDefinition, ...]:
    attributes = tuple(attributes)
    names = [a.name for a in attributes]
    if len(names) != len(set(names)):
        raise InvalidValueError(f"{owner}: attribute names must be unique")
    return attributes


@dataclass(frozen=True, slots=True)
class EntityTypeDefinition:
    key: str
    display_name: str
    description: str = ""
    parent: str | None = None
    abstract: bool = False
    attributes: tuple[AttributeDefinition, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        namespace_of(self.key)
        object.__setattr__(
            self, "display_name", require_text(self.display_name, "display_name", max_length=200)
        )
        if self.parent is not None:
            namespace_of(self.parent)
            if self.parent == self.key:
                raise InvalidValueError(f"{self.key} cannot be its own parent")
        object.__setattr__(self, "attributes", _attributes(self.attributes, self.key))


@dataclass(frozen=True, slots=True)
class EntityTypeExtension:
    """Optional attributes added to a type this package does not define."""

    type_key: str
    attributes: tuple[AttributeDefinition, ...]

    def __post_init__(self) -> None:
        namespace_of(self.type_key)
        attributes = _attributes(self.attributes, f"extension of {self.type_key}")
        if not attributes:
            raise InvalidValueError(f"extension of {self.type_key} adds no attributes")
        if any(a.required for a in attributes):
            raise InvalidValueError(
                f"extension of {self.type_key}: added attributes must be optional, or existing "
                "entities of that type would become invalid"
            )
        object.__setattr__(self, "attributes", attributes)


@dataclass(frozen=True, slots=True)
class RelationTypeDefinition:
    key: str
    display_name: str
    source_types: frozenset[str]
    target_types: frozenset[str]
    description: str = ""
    attributes: tuple[AttributeDefinition, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        namespace_of(self.key)
        object.__setattr__(
            self, "display_name", require_text(self.display_name, "display_name", max_length=200)
        )
        for name in ("source_types", "target_types"):
            endpoints = frozenset(getattr(self, name))
            if not endpoints:
                raise InvalidValueError(f"{self.key}: {name} must not be empty")
            for key in endpoints:
                namespace_of(key)
            object.__setattr__(self, name, endpoints)
        object.__setattr__(self, "attributes", _attributes(self.attributes, self.key))


@dataclass(frozen=True, slots=True)
class PackageContent:
    """Everything a package version declares. Its fingerprint identifies the content."""

    key: str
    scope: PackageScope
    namespace: str | None
    tenant_id: TenantId | None
    depends_on: tuple[OntologyPackageId, ...] = ()
    entity_types: tuple[EntityTypeDefinition, ...] = ()
    extensions: tuple[EntityTypeExtension, ...] = ()
    relation_types: tuple[RelationTypeDefinition, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", PackageScope(self.scope))
        for name in ("depends_on", "entity_types", "extensions", "relation_types"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        self._check_scope()
        self._check_keys()

    def _check_scope(self) -> None:
        if not isinstance(self.key, str) or not PACKAGE_KEY.fullmatch(self.key):
            raise InvalidValueError(f"package key {self.key!r} is not valid")
        if self.namespace is not None and not NAMESPACE.fullmatch(self.namespace):
            raise InvalidValueError(
                f"namespace {self.namespace!r} must be 2-16 lowercase letters/digits"
            )
        if self.scope is PackageScope.CORE:
            if self.key != CORE_PACKAGE_KEY or self.namespace or self.tenant_id or self.depends_on:
                raise InvalidValueError(
                    f"the core package is {CORE_PACKAGE_KEY!r}, "
                    "with no namespace, tenant or dependencies"
                )
            return
        if self.namespace is None or not self.depends_on:
            raise InvalidValueError(
                f"{self.key}: an extension package needs a namespace and dependencies"
            )
        if self.scope is PackageScope.INDUSTRY and self.tenant_id is not None:
            raise InvalidValueError(f"{self.key}: an industry package belongs to no tenant")
        if self.scope is PackageScope.TENANT and (
            self.tenant_id is None or self.key != TENANT_PACKAGE_KEY
        ):
            raise InvalidValueError(
                f"a tenant package is {TENANT_PACKAGE_KEY!r} and belongs to exactly one tenant"
            )

    def _check_keys(self) -> None:
        count = len(self.entity_types) + len(self.extensions) + len(self.relation_types)
        if count > MAX_DEFINITIONS:
            raise InvalidValueError(
                f"{self.key}: at most {MAX_DEFINITIONS} definitions per package"
            )
        own = [t.key for t in self.entity_types] + [r.key for r in self.relation_types]
        for key in own:
            if namespace_of(key) != self.namespace:
                expected = f"'{self.namespace}.'" if self.namespace else "no namespace prefix"
                raise InvalidValueError(f"{key}: keys of package {self.key} need {expected}")
        type_keys = [t.key for t in self.entity_types]
        extended = [e.type_key for e in self.extensions]
        relation_keys = [r.key for r in self.relation_types]
        for label, keys in (
            ("entity type", type_keys),
            ("extension", extended),
            ("relation", relation_keys),
        ):
            if len(keys) != len(set(keys)):
                raise InvalidValueError(f"{self.key}: duplicate {label} keys")
        if set(type_keys) & set(extended):
            raise InvalidValueError(f"{self.key}: a package cannot extend a type it defines")

    def fingerprint(self) -> str:
        encoded = json.dumps(content_to_json(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _attributes_json(attributes: tuple[AttributeDefinition, ...]) -> dict[str, Any]:
    return {a.name: a.to_json() for a in attributes}


def content_to_json(content: PackageContent) -> dict[str, Any]:
    """Canonical form: definition order does not matter, every field that does is included."""
    return {
        "key": content.key,
        "scope": content.scope.value,
        "namespace": content.namespace,
        "tenant_id": str(content.tenant_id) if content.tenant_id else None,
        "depends_on": sorted(str(d) for d in content.depends_on),
        "description": content.description,
        "entity_types": {
            t.key: {
                "display_name": t.display_name,
                "description": t.description,
                "parent": t.parent,
                "abstract": t.abstract,
                "deprecated": t.deprecated,
                "attributes": _attributes_json(t.attributes),
            }
            for t in content.entity_types
        },
        "extensions": {e.type_key: _attributes_json(e.attributes) for e in content.extensions},
        "relation_types": {
            r.key: {
                "display_name": r.display_name,
                "description": r.description,
                "source_types": sorted(r.source_types),
                "target_types": sorted(r.target_types),
                "deprecated": r.deprecated,
                "attributes": _attributes_json(r.attributes),
            }
            for r in content.relation_types
        },
    }


@dataclass(frozen=True, slots=True)
class PublishedPackage:
    """A package version as published. Never changes once it exists."""

    id: OntologyPackageId
    version: int
    content: PackageContent
    published_at: datetime
    published_by: str

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or self.version < 1:
            raise InvalidValueError("package versions start at 1")
        object.__setattr__(self, "published_at", require_utc(self.published_at, "published_at"))
        object.__setattr__(
            self, "published_by", require_text(self.published_by, "published_by", max_length=100)
        )

    @property
    def key(self) -> str:
        return self.content.key

    @property
    def fingerprint(self) -> str:
        return self.content.fingerprint()

    def label(self) -> str:
        owner = f" (tenant {self.content.tenant_id})" if self.content.tenant_id else ""
        return f"{self.key} v{self.version}{owner}"
