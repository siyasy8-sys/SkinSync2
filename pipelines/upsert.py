from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import Boolean, ColumnElement, delete, func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Document, Product


def upsert(
    session: Session,
    model: type[Base],
    rows: Sequence[dict[str, Any]],
    conflict_cols: Sequence[str],
    batch_size: int = 1000,
) -> tuple[int, int]:
    """INSERT ... ON CONFLICT DO UPDATE, in batches. Returns (inserted, updated).

    Rows must be unique on `conflict_cols`: Postgres rejects a statement that
    updates the same row twice. Callers dedupe first. Batching keeps each
    statement under Postgres's 65,535 bind-parameter limit.
    """
    inserted = updated = 0
    for start in range(0, len(rows), batch_size):
        batch = list(rows[start : start + batch_size])
        stmt = insert(model).values(batch)
        updates: dict[str, ColumnElement[Any]] = {
            col: stmt.excluded[col] for col in batch[0] if col not in conflict_cols
        }
        updates["updated_at"] = func.now()
        # xmax = 0 only for freshly inserted rows, which tells inserts and updates apart.
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_cols), set_=updates
        ).returning(literal_column("xmax = 0", type_=Boolean))
        flags: Sequence[bool] = session.execute(upsert_stmt).scalars().all()
        batch_inserted = sum(1 for flag in flags if flag)
        inserted += batch_inserted
        updated += len(flags) - batch_inserted
    return inserted, updated


def prune(
    session: Session, model: type[Product] | type[Document], source: str, keep: Collection[str]
) -> int:
    """Deletes rows of `source` whose source_id isn't in `keep`. Returns the count.

    Refuses an empty `keep`, which would wipe the whole source.
    """
    if not keep:
        raise ValueError(f"refusing to prune every {source} row: the new sample is empty")
    stmt = delete(model).where(model.source == source, model.source_id.not_in(list(keep)))
    result = session.execute(stmt)
    return int(result.rowcount)  # type: ignore[attr-defined]
