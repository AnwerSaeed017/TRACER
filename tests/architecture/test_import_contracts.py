"""The dependency rules in pyproject.toml ([tool.importlinter]) hold for the whole package.

Architecture that is only written down erodes one convenient import at a time. This test
makes a forbidden import fail the build like any other bug.

import-linter runs in its own process: in-process it reconfigures logging globally, which
disabled loggers that later tests rely on.
"""

import os
import subprocess
import sys

from tests.support import PROJECT_ROOT


def test_import_contracts_hold() -> None:
    result = subprocess.run(  # fixed command, no user input
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports; raise SystemExit(lint_imports())",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        # import-linter prints Unicode box drawing; Windows would decode it as cp1252 and fail.
        env=os.environ | {"PYTHONIOENCODING": "utf-8"},
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
