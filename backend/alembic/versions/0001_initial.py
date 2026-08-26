"""Enable the PostgreSQL vector extension required by the initial schema.

Revision ID: 0001_initial
Revises: None
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Do not drop a shared database extension during application rollback.
    pass
