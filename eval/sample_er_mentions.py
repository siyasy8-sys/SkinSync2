"""Writes an unlabeled, stratified sample of ingredient mentions for hand labeling.

    uv run python -m eval.sample_er_mentions [--out eval/data/er_mentions.csv] [--seed N]

gold_ingredient_id is left empty: fill it with the CosIng substance ID (see
`python -m eval.lookup`), NONE, or SKIP. Predictions are never written, so they
can't anchor the labels. Refuses to overwrite a file that already has labels.
"""

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Product
from app.db.session import get_sessionmaker
from app.resolution.cascade import Resolver
from app.resolution.index import PgAliasIndex
from app.resolution.normalize import normalize
from app.resolution.parse import parse_ingredient_list
from eval.er import (
    CSV_FIELDS,
    STRATUM_TARGETS,
    SampledMention,
    assign_stratum,
    mention_id,
    stratified_sample,
)

DEFAULT_OUT = Path("eval/data/er_mentions.csv")
DEFAULT_SEED = 20260926


class LabelsExistError(RuntimeError):
    pass


def has_labels(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(newline="") as f:
        return any((row.get("gold_ingredient_id") or "").strip() for row in csv.DictReader(f))


def write_sample(path: Path, sample: list[SampledMention]) -> None:
    if has_labels(path):
        raise LabelsExistError(f"{path} already has gold labels; refusing to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for m in sorted(sample, key=lambda m: (m.stratum, m.mention_id)):
            writer.writerow(
                {
                    "mention_id": m.mention_id,
                    "product_source_id": m.product_source_id,
                    "position": m.position,
                    "raw_mention": m.raw_mention,
                    "stratum": m.stratum,
                    "gold_ingredient_id": "",
                    "notes": "",
                }
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample ER mentions for labeling")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    with get_sessionmaker()() as session:
        resolver = Resolver(PgAliasIndex(session), get_settings())
        mentions = []
        products = session.execute(
            select(Product.source_id, Product.raw_ingredient_text).order_by(Product.source_id)
        )
        for source_id, text in products:
            for m in parse_ingredient_list(text):
                outcome = resolver.resolve(m.raw, m.suspected_noise)
                mentions.append(
                    SampledMention(
                        mention_id(source_id, m.position, m.raw),
                        source_id,
                        m.position,
                        m.raw,
                        normalize(m.raw),
                        assign_stratum(
                            m.raw,
                            outcome,
                            may_contain=m.may_contain,
                            suspected_noise=m.suspected_noise,
                        ),
                    )
                )
    sample = stratified_sample(mentions, STRATUM_TARGETS, args.seed)
    try:
        write_sample(args.out, sample)
    except LabelsExistError as exc:
        print(exc, file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for sampled in sample:
        counts[sampled.stratum] = counts.get(sampled.stratum, 0) + 1
    print(f"wrote {len(sample)} unlabeled mentions to {args.out}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
