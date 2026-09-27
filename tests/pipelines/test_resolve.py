import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import (
    Ingredient,
    IngredientAlias,
    Product,
    ProductIngredient,
    ResolutionQueue,
)
from app.resolution.index import PgAliasIndex
from pipelines.resolve import resolve_products
from pipelines.runs import RunStats

pytestmark = pytest.mark.db


def _seed(session: Session) -> int:
    ids = {}
    for cosing_id, name in [("t-glyc", "ZZTESTGLYCERIN"), ("t-aqua", "ZZTESTAQUA")]:
        ids[name] = session.scalar(
            insert(Ingredient)
            .values(cosing_id=cosing_id, inci_name=name, functions=[])
            .returning(Ingredient.id)
        )
    session.execute(
        insert(IngredientAlias),
        [
            {"alias": "zztestglycerin", "ingredient_id": ids["ZZTESTGLYCERIN"], "source": "inci"},
            {"alias": "zztestaqua", "ingredient_id": ids["ZZTESTAQUA"], "source": "inci"},
        ],
    )
    product_id: int | None = session.scalar(
        insert(Product)
        .values(
            source="test",
            source_id="t-1",
            name="Test cream",
            raw_ingredient_text="ZZTestAqua, ZZTestGlycerine, Qqqq Unknownium",
        )
        .returning(Product.id)
    )
    assert product_id is not None
    return product_id


def test_trigram_candidates_find_misspellings(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory() as session:
        _seed(session)
        hits = PgAliasIndex(session).candidates("zztestglycerine", 5)
    assert "zztestglycerin" in {h.alias for h in hits}


def test_resolve_products_is_idempotent_and_keeps_reviewed_queue_rows(
    db_session_factory: sessionmaker[Session], settings: Settings
) -> None:
    with db_session_factory() as session:
        product_id = _seed(session)
        resolve_products(session, settings, RunStats(), [product_id])

        rows = session.execute(
            select(ProductIngredient.position, ProductIngredient.stage)
            .where(ProductIngredient.product_id == product_id)
            .order_by(ProductIngredient.position)
        ).all()
        assert [tuple(r) for r in rows] == [(1, "exact"), (2, "fuzzy")]
        queued = session.scalars(
            select(ResolutionQueue).where(ResolutionQueue.product_id == product_id)
        ).all()
        assert [(q.position, q.normalized, q.reason) for q in queued] == [
            (3, "qqqq unknownium", "no_match")
        ]

        # A reviewer marks the queue row; re-running must not reopen or duplicate it.
        session.execute(
            update(ResolutionQueue)
            .where(ResolutionQueue.product_id == product_id)
            .values(status="ignored")
        )
        resolve_products(session, settings, RunStats(), [product_id])

        again = session.execute(
            select(ProductIngredient.position).where(ProductIngredient.product_id == product_id)
        ).all()
        assert len(again) == 2
        statuses = session.scalars(
            select(ResolutionQueue.status).where(ResolutionQueue.product_id == product_id)
        ).all()
        assert statuses == ["ignored"]
