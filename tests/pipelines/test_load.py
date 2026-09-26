import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Document, IngestionRun, Ingredient, Product
from pipelines.ingest import RunOptions, _maybe_prune
from pipelines.runs import RunStats, track_run
from pipelines.sources.cosing import CosingIngredient, load_ingredients
from pipelines.sources.obf import ObfProduct, load_products
from pipelines.sources.pmc import PmcArticle, load_documents
from pipelines.upsert import prune

pytestmark = pytest.mark.db


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_loaders_are_idempotent(db_session_factory: sessionmaker[Session]) -> None:
    ingredient = CosingIngredient(
        cosing_id="t-1",
        inci_name="NIACINAMIDE",
        functions=["SMOOTHING"],
        cas_number="98-92-0",
        restrictions=None,
    )
    product = ObfProduct(
        source_id="t-1",
        name="Cream",
        brand=None,
        category="face-creams",
        raw_ingredient_text="Aqua, Niacinamide",
    )
    article = PmcArticle(
        source_id="PMCt1",
        pmid=None,
        title="T",
        url="u",
        published_at=None,
        license="CC BY 4.0",
        abstract="a",
    )
    with db_session_factory() as session:
        before = [_count(session, m) for m in (Ingredient, Product, Document)]
        assert load_ingredients(session, [ingredient]) == (1, 0)
        assert load_products(session, [product]) == (1, 0)
        assert load_documents(session, [article]) == (1, 0)

        renamed = ingredient.model_copy(update={"functions": ["SMOOTHING", "SKIN CONDITIONING"]})
        assert load_ingredients(session, [renamed]) == (0, 1)
        assert load_products(session, [product]) == (0, 1)
        assert load_documents(session, [article]) == (0, 1)

        after = [_count(session, m) for m in (Ingredient, Product, Document)]
        assert after == [b + 1 for b in before]
        row = session.scalars(select(Ingredient).where(Ingredient.cosing_id == "t-1")).one()
        assert row.functions == ["SMOOTHING", "SKIN CONDITIONING"]
        # None must be SQL NULL, not JSON null, so "IS NULL" filters work.
        assert session.scalar(
            select(Ingredient.restrictions.is_(None)).where(Ingredient.cosing_id == "t-1")
        )


def test_track_run_records_success(db_session_factory: sessionmaker[Session]) -> None:
    with track_run(db_session_factory, "test_dag") as stats:
        stats.counts["inserted"] = 3

    with db_session_factory() as session:
        run = session.scalars(select(IngestionRun).where(IngestionRun.dag == "test_dag")).one()
    assert run.status == "success"
    assert run.counts == {"inserted": 3}
    assert run.finished_at is not None


def test_track_run_records_failure_and_reraises(db_session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="boom"), track_run(db_session_factory, "test_fail"):
        raise ValueError("boom")

    with db_session_factory() as session:
        run = session.scalars(select(IngestionRun).where(IngestionRun.dag == "test_fail")).one()
    assert run.status == "failed"
    assert run.errors == ["ValueError: boom"]


def _product(source_id: str, source: str = "open_beauty_facts") -> ObfProduct:
    return ObfProduct(
        source=source,
        source_id=source_id,
        name="P",
        brand=None,
        category=None,
        raw_ingredient_text="Aqua",
    )


def _source_ids(session: Session, source: str) -> set[str]:
    rows = session.scalars(
        select(Product.source_id).where(
            Product.source == source, Product.source_id.startswith("prune-")
        )
    )
    return set(rows)


def test_prune_deletes_only_this_sources_rows_outside_the_sample(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as session:
        load_products(
            session,
            [_product("prune-keep"), _product("prune-old"), _product("prune-other", "other")],
        )
        pruned = prune(session, Product, "open_beauty_facts", {"prune-keep"} | _others(session))

        assert pruned == 1
        assert _source_ids(session, "open_beauty_facts") == {"prune-keep"}
        assert _source_ids(session, "other") == {"prune-other"}


def _others(session: Session) -> set[str]:
    """Rows from earlier real ingests must survive this test's prune."""
    rows = session.scalars(
        select(Product.source_id).where(
            Product.source == "open_beauty_facts", ~Product.source_id.startswith("prune-")
        )
    )
    return set(rows)


def test_prune_refuses_an_empty_sample(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory() as session, pytest.raises(ValueError, match="empty"):
        prune(session, Product, "open_beauty_facts", set())


def test_maybe_prune_skips_when_the_fetch_was_incomplete(
    db_session_factory: sessionmaker[Session],
) -> None:
    stats = RunStats()
    stats.counts["fetch_errors"] = 1
    with db_session_factory() as session:
        load_products(session, [_product("prune-a"), _product("prune-b")])
        _maybe_prune(
            session, stats, RunOptions(prune=True), Product, "open_beauty_facts", {"prune-a"}
        )

        assert _source_ids(session, "open_beauty_facts") == {"prune-a", "prune-b"}
    assert "pruned" not in stats.counts
    assert stats.errors == ["prune skipped: the fetch was incomplete"]


def test_maybe_prune_is_a_no_op_without_the_flag(
    db_session_factory: sessionmaker[Session],
) -> None:
    stats = RunStats()
    with db_session_factory() as session:
        load_products(session, [_product("prune-a"), _product("prune-b")])
        _maybe_prune(session, stats, RunOptions(), Product, "open_beauty_facts", {"prune-a"})

        assert _source_ids(session, "open_beauty_facts") == {"prune-a", "prune-b"}
    assert stats.errors == []
