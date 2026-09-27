"""Entity-resolution evaluation logic: strata, dev/test split, and metrics.

Kept free of I/O so it can be unit-tested. Gold labels live in
eval/data/er_mentions.csv and are written by hand, never generated.
"""

import hashlib
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.resolution.cascade import Outcome, Resolved

GOLD_NONE = "NONE"  # the mention is not an ingredient, or has no CosIng entry
GOLD_SKIP = "SKIP"  # the labeler couldn't tell; excluded from metrics

CSV_FIELDS = [
    "mention_id",
    "product_source_id",
    "position",
    "raw_mention",
    "stratum",
    "gold_ingredient_id",
    "notes",
]

# Sampling targets (~300). Strata are assigned in this order; each mention gets one.
STRATUM_TARGETS: dict[str, int] = {
    "slash": 40,
    "parenthetical": 30,
    "salt_ester": 40,
    "misspelling_suspect": 40,
    "non_english": 30,
    "trade_name_suspect": 30,
    "may_contain_or_noise": 20,
    "random": 70,
}

_SALT_ESTER = re.compile(
    r"\b(?:sodium|disodium|potassium|calcium|magnesium|zinc|ammonium)\b|"
    r"\w+yl (?:acetate|palmitate|propionate|linoleate|nicotinate|succinate|stearate|laurate|"
    r"oleate|myristate|benzoate|salicylate)\b",
    re.IGNORECASE,
)
_NON_ENGLISH_WORDS = re.compile(
    r"\b(?:eau|huile|extrait|feuille|fleur|graine|acide|beurre|wasser|ol|öl|aceite|agua|"
    r"olio|estratto|acqua|glycerine végétale|hyaluronique|sheabutter)\b",
    re.IGNORECASE,
)
_TRADEMARK = re.compile(r"[®™©]")


@dataclass(frozen=True)
class SampledMention:
    mention_id: str
    product_source_id: str
    position: int
    raw_mention: str
    normalized: str
    stratum: str


def mention_id(product_source_id: str, position: int, raw: str) -> str:
    return hashlib.sha1(f"{product_source_id}|{position}|{raw}".encode()).hexdigest()[:12]


def assign_stratum(raw: str, outcome: Outcome, *, may_contain: bool, suspected_noise: bool) -> str:
    """One stratum per mention, first rule wins. Uses the cascade only to find likely
    misspellings (fuzzy or review-band) and trade names; predictions never reach the CSV."""
    if may_contain or suspected_noise:
        return "may_contain_or_noise"
    if "/" in raw or "\\" in raw:
        return "slash"
    if "(" in raw or "[" in raw:
        return "parenthetical"
    if _SALT_ESTER.search(raw):
        return "salt_ester"
    if (isinstance(outcome, Resolved) and outcome.stage == "fuzzy") or (
        not isinstance(outcome, Resolved) and outcome.reason == "below_threshold"
    ):
        return "misspelling_suspect"
    if _NON_ENGLISH_WORDS.search(raw) or any(ord(c) > 127 and c.isalpha() for c in raw):
        return "non_english"
    if _TRADEMARK.search(raw) or (
        not isinstance(outcome, Resolved) and outcome.reason == "no_match" and len(raw.split()) <= 3
    ):
        return "trade_name_suspect"
    return "random"


def stratified_sample(
    mentions: Sequence[SampledMention], targets: dict[str, int], seed: int
) -> list[SampledMention]:
    """Distinct normalized mentions: up to `targets[stratum]` from each stratum, then a
    random fill (random stratum plus other strata's leftovers) sized to cover any
    shortfall. Deterministic for a given seed."""
    rng = random.Random(seed)
    by_stratum: defaultdict[str, list[SampledMention]] = defaultdict(list)
    seen: set[str] = set()
    for m in mentions:  # one representative per normalized text
        if m.normalized and m.normalized not in seen:
            seen.add(m.normalized)
            by_stratum[m.stratum].append(m)

    chosen: list[SampledMention] = []
    shortfall = 0
    leftovers: list[SampledMention] = []
    for stratum, target in targets.items():
        if stratum == "random":
            continue
        pool = sorted(by_stratum[stratum], key=lambda m: m.mention_id)
        rng.shuffle(pool)
        chosen.extend(pool[:target])
        leftovers.extend(pool[target:])
        shortfall += max(target - len(pool), 0)
    random_pool = sorted(by_stratum["random"] + leftovers, key=lambda m: m.mention_id)
    rng.shuffle(random_pool)
    # Mentions picked in this fill keep their real stratum; per-stratum metrics stay honest.
    chosen.extend(random_pool[: targets.get("random", 0) + shortfall])
    return chosen


def split_of(mention_id_: str, dev_share: float = 0.7) -> str:
    """Deterministic dev/test split by hashing the mention ID."""
    bucket = int(hashlib.sha1(mention_id_.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "dev" if bucket < dev_share else "test"


@dataclass(frozen=True)
class Judgement:
    gold: str  # a CosIng ID or GOLD_NONE
    predicted: str | None  # a CosIng ID, or None if unresolved
    stage: str | None  # resolving stage, or None
    reason: str | None  # unresolved reason, or None
    stratum: str


def _prf(items: Iterable[Judgement]) -> dict[str, float]:
    items = list(items)
    tp = sum(1 for j in items if j.predicted is not None and j.predicted == j.gold)
    fp = sum(1 for j in items if j.predicted is not None and j.predicted != j.gold)
    positives = sum(1 for j in items if j.gold != GOLD_NONE)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / positives if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "n": len(items),
        "tp": tp,
        "fp": fp,
        "fn": positives - tp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def compute_metrics(judgements: Sequence[Judgement]) -> dict[str, Any]:
    """Precision = correct / resolved (resolving a NONE mention is a false positive).
    Recall = correct / mentions with a real gold ingredient. Plus per-stage and
    per-stratum breakdowns."""
    per_stage: dict[str, dict[str, float]] = {}
    for stage in sorted({j.stage for j in judgements if j.stage}):
        stage_items = [j for j in judgements if j.stage == stage]
        correct = sum(1 for j in stage_items if j.predicted == j.gold)
        per_stage[stage] = {
            "resolved": len(stage_items),
            "precision": round(correct / len(stage_items), 4),
        }
    strata = sorted({j.stratum for j in judgements})
    return {
        "overall": _prf(judgements),
        "per_stage": per_stage,
        "unresolved": dict(Counter(j.reason for j in judgements if j.predicted is None)),
        "per_stratum": {s: _prf(j for j in judgements if j.stratum == s) for s in strata},
    }


def pick_threshold(
    sweep: Sequence[tuple[float, dict[str, float]]], min_precision: float
) -> float | None:
    """Highest-F1 threshold among those meeting the precision floor; ties -> higher threshold."""
    eligible = [(t, m) for t, m in sweep if m["precision"] >= min_precision]
    if not eligible:
        return None
    return max(eligible, key=lambda tm: (tm[1]["f1"], tm[0]))[0]
