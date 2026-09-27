"""Entity-resolution pipeline (no network).

    uv run python -m pipelines.resolve aliases    # rebuild ingredient_aliases + relation candidates
    uv run python -m pipelines.resolve products   # resolve every product's ingredient list

Each command is one tracked run (an `ingestion_runs` row) in one transaction.
"""

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import (
    Ingredient,
    IngredientAlias,
    IngredientRelation,
    Product,
    ProductIngredient,
    ResolutionQueue,
)
from app.db.session import get_sessionmaker
from app.resolution.aliases import build_aliases
from app.resolution.cascade import Resolved, Resolver
from app.resolution.index import PgAliasIndex
from app.resolution.normalize import normalize
from app.resolution.parse import parse_ingredient_list
from app.resolution.relations import find_relations
from pipelines.runs import RunStats, track_run

RULE_SOURCE = "rule"
BATCH = 5000


def _insert_batches(session: Session, model: Any, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH):
        session.execute(insert(model), rows[start : start + BATCH])


def build_alias_table(session: Session, stats: RunStats) -> None:
    """Rebuilds CosIng-derived aliases and rule-based relation candidates.

    Aliases from other sources (e.g. Week 3 LLM decisions) and reviewed
    relations are kept.
    """
    ingredients = session.execute(
        select(Ingredient.id, Ingredient.inci_name, Ingredient.names, Ingredient.cas_number)
    ).all()
    rows: list[dict[str, Any]] = []
    for ingredient_id, inci_name, names, cas_number in ingredients:
        for alias in build_aliases(inci_name, names or {}, cas_number):
            rows.append(
                {
                    "alias": alias.alias,
                    "ingredient_id": ingredient_id,
                    "source": alias.source,
                    "confidence": alias.confidence,
                }
            )
    cosing_sources = ["inci", "inci_usa", "inn", "ph_eur", "glossary", "cas"]
    session.execute(delete(IngredientAlias).where(IngredientAlias.source.in_(cosing_sources)))
    _insert_batches(session, IngredientAlias, rows)
    stats.counts.update(f"aliases:{r['source']}" for r in rows)
    owners: defaultdict[str, set[int]] = defaultdict(set)
    for r in rows:
        owners[r["alias"]].add(r["ingredient_id"])
    stats.counts["aliases_shared_by_several_ingredients"] = sum(len(v) > 1 for v in owners.values())

    by_name: defaultdict[str, set[int]] = defaultdict(set)
    for ingredient_id, inci_name, _, _ in ingredients:
        by_name[normalize(inci_name)].add(ingredient_id)
    unique = {name: next(iter(ids)) for name, ids in by_name.items() if len(ids) == 1}
    relations = [
        {
            "ingredient_id": unique[r.ingredient],
            "related_id": unique[r.related],
            "kind": r.kind,
            "source": RULE_SOURCE,
            "reviewed": False,
        }
        for r in find_relations(set(unique))
    ]
    session.execute(
        delete(IngredientRelation).where(
            IngredientRelation.source == RULE_SOURCE, IngredientRelation.reviewed.is_(False)
        )
    )
    if relations:
        session.execute(pg_insert(IngredientRelation).values(relations).on_conflict_do_nothing())
    stats.counts.update(f"relations:{r['kind']}" for r in relations)


def resolve_products(
    session: Session,
    settings: Settings,
    stats: RunStats,
    product_ids: list[int] | None = None,
) -> None:
    """Replaces product_ingredients and refreshes open queue rows (all products by default).

    Queue rows a reviewer has already handled (status != open) are kept.
    """
    resolver = Resolver(PgAliasIndex(session), settings)
    query = select(Product.id, Product.raw_ingredient_text)
    if product_ids is not None:
        query = query.where(Product.id.in_(product_ids))
    products = session.execute(query).all()
    resolved_rows: dict[tuple[int, int, int], dict[str, Any]] = {}
    queue_rows: dict[tuple[int, int, str], dict[str, Any]] = {}
    for product_id, text in products:
        for mention in parse_ingredient_list(text):
            stats.counts["mentions"] += 1
            outcome = resolver.resolve(mention.raw, mention.suspected_noise)
            if isinstance(outcome, Resolved):
                stats.counts[f"resolved:{outcome.stage}"] += 1
                key = (product_id, mention.position, outcome.ingredient_id)
                resolved_rows.setdefault(
                    key,
                    {
                        "product_id": product_id,
                        "position": mention.position,
                        "ingredient_id": outcome.ingredient_id,
                        "mention": mention.raw,
                        "stage": outcome.stage,
                        "match_confidence": outcome.confidence,
                        "may_contain": mention.may_contain,
                    },
                )
                continue
            stats.counts[f"queued:{outcome.reason}"] += 1
            normalized = normalize(mention.raw)
            queue_rows.setdefault(
                (product_id, mention.position, normalized),
                {
                    "product_id": product_id,
                    "position": mention.position,
                    "mention": mention.raw,
                    "normalized": normalized,
                    "reason": outcome.reason,
                    "candidates": [
                        {
                            "cosing_id": c.cosing_id,
                            "inci_name": c.inci_name,
                            "alias": c.alias,
                            "score": c.score,
                        }
                        for c in outcome.candidates
                    ],
                },
            )

    product_ids = [p for p, _ in products]
    session.execute(delete(ProductIngredient).where(ProductIngredient.product_id.in_(product_ids)))
    _insert_batches(session, ProductIngredient, list(resolved_rows.values()))
    session.execute(
        delete(ResolutionQueue).where(
            ResolutionQueue.product_id.in_(product_ids), ResolutionQueue.status == "open"
        )
    )
    rows = list(queue_rows.values())
    for start in range(0, len(rows), BATCH):
        session.execute(
            pg_insert(ResolutionQueue).values(rows[start : start + BATCH]).on_conflict_do_nothing()
        )
    stats.counts["products"] = len(products)
    stats.counts["distinct_mentions"] = resolver.distinct_resolved


def run(session_factory: sessionmaker[Session], settings: Settings, command: str) -> RunStats:
    dag = {"aliases": "build_aliases", "products": "resolve_products"}[command]
    with track_run(session_factory, dag) as stats, session_factory() as session:
        if command == "aliases":
            build_alias_table(session, stats)
        else:
            resolve_products(session, settings, stats)
        session.commit()
    return stats


def summary(stats: RunStats) -> str:
    counts = dict(sorted(stats.counts.items()))
    lines = [json.dumps(counts)]
    mentions = counts.get("mentions", 0)
    if mentions:
        resolved = sum(v for k, v in counts.items() if k.startswith("resolved:"))
        lines.append(f"resolved {resolved}/{mentions} mentions ({resolved / mentions:.1%})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Entity-resolution pipeline")
    parser.add_argument("command", choices=["aliases", "products"])
    args = parser.parse_args(argv)
    print(summary(run(get_sessionmaker(), get_settings(), args.command)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
