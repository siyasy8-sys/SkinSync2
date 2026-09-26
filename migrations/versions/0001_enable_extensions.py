"""enable vector and pg_trgm extensions

Revision ID: 0001
Revises:
Create Date: 2026-09-26

vector: embedding column + HNSW index on chunks.
pg_trgm: trigram index on ingredient_aliases.alias for fuzzy matching.
Created here (not in a docker init script) because Cloud SQL requires
extensions to be enabled with SQL, so local and cloud follow one path.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
