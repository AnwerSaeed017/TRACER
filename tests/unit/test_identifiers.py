from uuid import uuid4

import pytest

from tracer.shared.errors import InvalidValueError
from tracer.shared.identifiers import EntityId, ProductId, SupplierId


def test_ids_of_different_kinds_never_compare_equal() -> None:
    raw = uuid4()
    assert SupplierId(raw) == SupplierId(raw)
    # mypy itself rejects this comparison as impossible; the ignore proves the runtime agrees.
    assert SupplierId(raw) != ProductId(raw)  # type: ignore[comparison-overlap]
    assert len({SupplierId(raw), ProductId(raw)}) == 2


def test_new_ids_are_unique() -> None:
    assert SupplierId.new() != SupplierId.new()


def test_parse_round_trips_through_text() -> None:
    supplier = SupplierId.new()
    assert SupplierId.parse(str(supplier)) == supplier


@pytest.mark.parametrize("raw", ["", "SUP-001", "1234"])
def test_parse_rejects_non_uuid_text(raw: str) -> None:
    with pytest.raises(InvalidValueError, match="SupplierId must be a UUID"):
        SupplierId.parse(raw)


def test_id_must_wrap_a_uuid() -> None:
    with pytest.raises(InvalidValueError):
        SupplierId("not-a-uuid")  # type: ignore[arg-type]


def test_base_id_cannot_be_used_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        EntityId(uuid4())


def test_ids_are_immutable() -> None:
    supplier = SupplierId.new()
    with pytest.raises(AttributeError):
        supplier.value = uuid4()  # type: ignore[misc]


def test_repr_names_the_kind() -> None:
    assert repr(ProductId.new()).startswith("ProductId(")
