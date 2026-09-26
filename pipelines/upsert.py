from collections.abc import Sequence
from typing import Any

from sqlalchemy import Boolean, ColumnElement, func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.base import Base


def upsert(
    session: Session,
    model: type[Base],
    rows: Sequence[dict[str, Any]],
    conflict_cols: Sequence[str],
) -> tuple[int, int]:
    """INSERT ... ON CONFLICT DO UPDATE. Returns (inserted, updated).

    Rows must be unique on `conflict_cols`: Postgres rejects a statement that
    updates the same row twice. Callers dedupe first.
    """
    if not rows:
        return 0, 0
    stmt = insert(model).values(list(rows))
    updates: dict[str, ColumnElement[Any]] = {
        col: stmt.excluded[col] for col in rows[0] if col not in conflict_cols
    }
    updates["updated_at"] = func.now()
    # xmax = 0 only for freshly inserted rows, which tells inserts and updates apart.
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=list(conflict_cols), set_=updates
    ).returning(literal_column("xmax = 0", type_=Boolean))
    flags: Sequence[bool] = session.execute(upsert_stmt).scalars().all()
    inserted = sum(1 for flag in flags if flag)
    return inserted, len(flags) - inserted
