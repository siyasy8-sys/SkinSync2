"""entity resolution

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 20:21:14.833492

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("suite", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "metrics", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )
    op.create_table(
        "ingredient_aliases",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_ingredient_aliases_ingredient_id_ingredients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingredient_aliases")),
        sa.UniqueConstraint(
            "alias", "ingredient_id", name=op.f("uq_ingredient_aliases_alias_ingredient_id")
        ),
    )
    op.create_index(
        "ix_ingredient_aliases_alias_trgm",
        "ingredient_aliases",
        ["alias"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"alias": "gin_trgm_ops"},
    )
    op.create_table(
        "ingredient_relations",
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("related_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reviewed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_ingredient_relations_ingredient_id_ingredients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_id"],
            ["ingredients.id"],
            name=op.f("fk_ingredient_relations_related_id_ingredients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "ingredient_id", "related_id", "kind", name=op.f("pk_ingredient_relations")
        ),
    )
    op.create_table(
        "product_ingredients",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("mention", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("may_contain", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_product_ingredients_ingredient_id_ingredients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_ingredients_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "product_id", "position", "ingredient_id", name=op.f("pk_product_ingredients")
        ),
    )
    op.create_table(
        "resolution_queue",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("mention", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column(
            "candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("resolved_ingredient_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_resolution_queue_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_resolution_queue_resolved_ingredient_id_ingredients"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_queue")),
        sa.UniqueConstraint(
            "product_id",
            "position",
            "normalized",
            name=op.f("uq_resolution_queue_product_id_position_normalized"),
        ),
    )
    op.add_column("ingredients", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column(
        "ingredients",
        sa.Column(
            "names", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("ingredients", "names")
    op.drop_column("ingredients", "status")
    op.drop_table("resolution_queue")
    op.drop_table("product_ingredients")
    op.drop_table("ingredient_relations")
    op.drop_index(
        "ix_ingredient_aliases_alias_trgm",
        table_name="ingredient_aliases",
        postgresql_using="gin",
        postgresql_ops={"alias": "gin_trgm_ops"},
    )
    op.drop_table("ingredient_aliases")
    op.drop_table("eval_runs")
