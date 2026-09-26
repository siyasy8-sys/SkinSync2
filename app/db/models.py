from datetime import date, datetime
from typing import Any

from sqlalchemy import ARRAY, Date, DateTime, Identity, String, Text, UniqueConstraint, func
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
    restrictions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cas_number: Mapped[str | None] = mapped_column(Text)
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
