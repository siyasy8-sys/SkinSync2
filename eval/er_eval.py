"""Scores the entity-resolution cascade against the hand-labeled mentions.

    uv run python -m eval.er_eval [--split all|dev|test] [--sweep] [--ablate] [--no-store]

--sweep: P/R/F1 at thresholds 80..100 on the dev split, and the threshold with the
         best F1 whose precision is >= --min-precision. Tune on dev, report on test.
--ablate: exact-only vs exact+fuzzy, token_sort vs token_set, INCI tie-break off.
"""

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import EvalRun, Ingredient
from app.db.session import get_sessionmaker
from app.resolution.cascade import DEFAULT_STAGES, Resolved, Resolver, Stage, exact_stage
from app.resolution.index import PgAliasIndex
from app.resolution.parse import looks_like_noise
from eval.er import GOLD_NONE, GOLD_SKIP, Judgement, compute_metrics, pick_threshold, split_of
from eval.sample_er_mentions import DEFAULT_OUT


class LabelError(ValueError):
    pass


def load_labeled(path: Path, known_cosing_ids: set[str]) -> list[dict[str, str]]:
    """Rows with a usable gold label. Rejects values that aren't a known CosIng ID,
    NONE or SKIP, so a typo can't silently count as an error or a hit."""
    if not path.exists():
        return []
    rows = []
    with path.open(newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            gold = (row.get("gold_ingredient_id") or "").strip()
            if not gold or gold.upper() == GOLD_SKIP:
                continue
            if gold.upper() == GOLD_NONE:
                row["gold_ingredient_id"] = GOLD_NONE
            elif gold not in known_cosing_ids:
                raise LabelError(f"{path}:{line}: unknown gold_ingredient_id {gold!r}")
            rows.append(row)
    return rows


def judge(
    rows: Sequence[dict[str, str]],
    session: Session,
    settings: Settings,
    stages: tuple[Stage, ...] = DEFAULT_STAGES,
    index: PgAliasIndex | None = None,
) -> list[Judgement]:
    index = index or PgAliasIndex(session)
    resolver = Resolver(index, settings, stages)
    out = []
    for row in rows:
        raw = row["raw_mention"]
        outcome = resolver.resolve(raw, looks_like_noise(raw))
        if isinstance(outcome, Resolved):
            predicted = index.describe(outcome.ingredient_id).cosing_id
            stage, reason = outcome.stage, None
        else:
            predicted, stage, reason = None, None, outcome.reason
        out.append(
            Judgement(
                gold=row["gold_ingredient_id"],
                predicted=predicted,
                stage=stage,
                reason=reason,
                stratum=row["stratum"],
            )
        )
    return out


def _store(session: Session, suite: str, config: dict[str, Any], metrics: dict[str, Any]) -> None:
    session.execute(insert(EvalRun).values(suite=suite, config=config, metrics=metrics))
    session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Entity-resolution evaluation")
    parser.add_argument("--labels", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--ablate", action="store_true")
    parser.add_argument("--min-precision", type=float, default=0.95)
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    with get_sessionmaker()() as session:
        known = set(session.scalars(select(Ingredient.cosing_id)))
        try:
            rows = load_labeled(args.labels, known)
        except LabelError as exc:
            print(exc, file=sys.stderr)
            return 1
        if not rows:
            print(f"0 labeled rows in {args.labels}; fill gold_ingredient_id first.")
            return 0
        split = "dev" if args.sweep else args.split
        if split != "all":
            rows = [r for r in rows if split_of(r["mention_id"]) == split]
        index = PgAliasIndex(session)
        report: dict[str, Any] = {"split": split, "labeled": len(rows)}

        if args.sweep:
            sweep = []
            for threshold in range(80, 101):
                tuned = settings.model_copy(update={"er_fuzzy_threshold": float(threshold)})
                sweep.append(
                    (
                        float(threshold),
                        compute_metrics(judge(rows, session, tuned, index=index))["overall"],
                    )
                )
            report["sweep"] = {t: m for t, m in sweep}
            report["recommended_threshold"] = pick_threshold(sweep, args.min_precision)
        elif args.ablate:
            variants: dict[str, tuple[Settings, tuple[Stage, ...]]] = {
                "exact_only": (settings, (exact_stage,)),
                "exact+fuzzy(token_sort)": (settings, DEFAULT_STAGES),
                "exact+fuzzy(token_set)": (
                    settings.model_copy(update={"er_fuzzy_scorer": "token_set_ratio"}),
                    DEFAULT_STAGES,
                ),
                "no_inci_tiebreak": (
                    settings.model_copy(update={"er_inci_tiebreak": False}),
                    DEFAULT_STAGES,
                ),
            }
            report["ablations"] = {
                name: compute_metrics(judge(rows, session, cfg, stages, index))["overall"]
                for name, (cfg, stages) in variants.items()
            }
        else:
            report["metrics"] = compute_metrics(judge(rows, session, settings, index=index))

        config = {k: v for k, v in settings.model_dump().items() if k.startswith("er_")} | {
            "mode": "sweep" if args.sweep else "ablate" if args.ablate else "score"
        }
        print(json.dumps(report, indent=2))
        if not args.no_store:
            _store(session, "entity_resolution", config, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
