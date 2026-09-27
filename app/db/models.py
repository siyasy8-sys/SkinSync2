from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Ingredient(Base):
    """One canonical ingredient, sourced from EU CosIng."""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    cosing_id: Mapped[str] = mapped_column(String(32), unique=True)
    inci_name: Mapped[str] = mapped_column(Text)
    functions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    restrictions: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    cas_number: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
    # Other CosIng names (US INCI, INN, Ph. Eur., glossary): alias material.
    names: Mapped[dict[str, list[str]]] = mapped_column(JSONB, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    raw_ingredient_text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Document(Base):
    """One paper or monograph. full_text is only filled when the license allows it."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(64))
    pmid: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date)
    license: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IngestionRun(Base):
    """One row per pipeline run; `dag` matches the Airflow DAG name in SPEC.md."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    dag: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    counts: Mapped[dict[str, int]] = mapped_column(JSONB, server_default="{}")
    errors: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")


class IngredientAlias(Base):
    """Every known name for a canonical ingredient, normalized with app.resolution.normalize."""

    __tablename__ = "ingredient_aliases"
    __table_args__ = (
        UniqueConstraint("alias", "ingredient_id"),
        # Trigram index for the fuzzy stage's candidate search (pg_trgm).
        Index(
            "ix_ingredient_aliases_alias_trgm",
            "alias",
            postgresql_using="gin",
            postgresql_ops={"alias": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    alias: Mapped[str] = mapped_column(Text)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, server_default="1.0")


class ProductIngredient(Base):
    """A resolved mention on a product label. Only confident matches are stored here."""

    __tablename__ = "product_ingredients"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    mention: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(16))
    match_confidence: Mapped[float] = mapped_column(Float)
    may_contain: Mapped[bool] = mapped_column(Boolean, server_default="false")


class ResolutionQueue(Base):
    """Mentions the cascade would not resolve confidently. Never guessed; reviewed by a human
    (or, from Week 3, the LLM adjudicator using the stored candidates)."""

    __tablename__ = "resolution_queue"
    __table_args__ = (UniqueConstraint("product_id", "position", "normalized"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    mention: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(32))
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    resolved_ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredients.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IngredientRelation(Base):
    """Salt/ester links between distinct ingredients. Only reviewed rows are used at query time."""

    __tablename__ = "ingredient_relations"

    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    related_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    reviewed: Mapped[bool] = mapped_column(Boolean, server_default="false")


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    suite: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
