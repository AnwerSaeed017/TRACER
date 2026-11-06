"""baseline: an empty starting point.

It creates no tables. It exists so that a freshly migrated database has a known revision,
which the readiness check compares against the code's head revision. The first real
tables arrive in M1.

Revision ID: 0001
Revises:
Create Date: 2026-11-07 01:27:37.126399
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
