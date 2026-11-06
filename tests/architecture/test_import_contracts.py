"""The dependency rules in pyproject.toml ([tool.importlinter]) hold for the whole package.

Architecture that is only written down erodes one convenient import at a time. This test
makes a forbidden import fail the build like any other bug.
"""

from importlinter.cli import lint_imports


def test_import_contracts_hold() -> None:
    assert lint_imports() == 0
