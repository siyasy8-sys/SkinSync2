"""The entity-resolution cascade: normalize -> exact alias -> fuzzy -> (Week 3: embedding,
LLM adjudication) -> unresolved. Cheapest stage first; stop at the first confident match.

Every stage is a plain function over a `MentionContext`, so each can be tested and
ablated on its own. The cascade never guesses: anything short of a confident match
comes back as `Unresolved` with a reason and candidates for review.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

from rapidfuzz import fuzz

from app.config import Settings
from app.resolution.index import AliasIndex
from app.resolution.normalize import Variant, VariantKind, normalize, variants

StageName = Literal["exact", "fuzzy", "embedding", "llm"]
Reason = Literal["no_match", "ambiguous", "below_threshold", "suspected_noise"]

# Exact hits on a derived form are slightly less certain than on the label text itself.
EXACT_CONFIDENCE: dict[VariantKind, float] = {
    "full": 1.0,
    "no_paren": 0.98,
    "paren": 0.95,
    "slash_part": 0.95,
}


@dataclass(frozen=True)
class Candidate:
    ingredient_id: int
    cosing_id: str
    inci_name: str
    alias: str
    score: float


@dataclass(frozen=True)
class Resolved:
    ingredient_id: int
    stage: StageName
    confidence: float
    variant: VariantKind
    alias: str


@dataclass(frozen=True)
class Unresolved:
    reason: Reason
    candidates: tuple[Candidate, ...] = ()
    final: bool = False  # True stops the cascade (e.g. an exact alias shared by two ingredients)


Outcome = Resolved | Unresolved


@dataclass(frozen=True)
class MentionContext:
    normalized: str
    variants: tuple[Variant, ...]
    index: AliasIndex
    settings: Settings
    suspected_noise: bool = False


Stage = Callable[[MentionContext], Outcome | None]


def _candidates(
    ctx: MentionContext, scored: Sequence[tuple[int, str, float]]
) -> tuple[Candidate, ...]:
    out = []
    for ingredient_id, alias, score in scored[: ctx.settings.er_queue_candidates]:
        ref = ctx.index.describe(ingredient_id)
        out.append(Candidate(ingredient_id, ref.cosing_id, ref.inci_name, alias, round(score, 1)))
    return tuple(out)


def exact_stage(ctx: MentionContext) -> Outcome | None:
    """First variant (most faithful first) whose alias names exactly one ingredient.

    An alias shared by several ingredients, or slash parts naming different
    ingredients, is ambiguous and stops the cascade.
    """
    slash_hits: dict[int, str] = {}
    for variant in ctx.variants:
        ids = ctx.index.lookup(variant.text)
        if not ids:
            continue
        if variant.kind == "slash_part":
            if len(ids) == 1:
                slash_hits.setdefault(next(iter(ids)), variant.text)
            continue
        if len(ids) == 1:
            (ingredient_id,) = ids
            return Resolved(
                ingredient_id, "exact", EXACT_CONFIDENCE[variant.kind], variant.kind, variant.text
            )
        scored = [(i, variant.text, 100.0) for i in sorted(ids)]
        return Unresolved("ambiguous", _candidates(ctx, scored), final=True)

    if len(slash_hits) == 1:
        ((ingredient_id, alias),) = slash_hits.items()
        return Resolved(ingredient_id, "exact", EXACT_CONFIDENCE["slash_part"], "slash_part", alias)
    if len(slash_hits) > 1:
        scored = [(i, alias, 100.0) for i, alias in slash_hits.items()]
        return Unresolved("ambiguous", _candidates(ctx, scored), final=True)
    return None


@cache
def _scorer(name: str) -> Callable[[str, str], float]:
    scorer: Callable[[str, str], float] = getattr(fuzz, name)
    return scorer


def fuzzy_stage(ctx: MentionContext) -> Outcome:
    """pg_trgm blocks candidates; RapidFuzz scores them.

    Accept only at >= threshold AND a clear margin over the best candidate for a
    different ingredient. Otherwise report the band, with candidates.
    """
    s = ctx.settings
    scorer = _scorer(s.er_fuzzy_scorer)
    queries = [v.text for v in ctx.variants if v.kind in ("full", "no_paren")]
    best: dict[int, tuple[str, float]] = {}
    for query in queries:
        for hit in ctx.index.candidates(query, s.er_candidate_k):
            score = scorer(query, hit.alias)
            if score > best.get(hit.ingredient_id, ("", -1.0))[1]:
                best[hit.ingredient_id] = (hit.alias, score)
    ranked = sorted(
        ((i, alias, score) for i, (alias, score) in best.items()), key=lambda r: (-r[2], r[0])
    )
    if not ranked:
        return Unresolved("no_match")
    top_id, top_alias, top = ranked[0]
    runner_up = ranked[1][2] if len(ranked) > 1 else 0.0
    candidates = _candidates(ctx, ranked)
    if top >= s.er_fuzzy_threshold:
        if top - runner_up >= s.er_fuzzy_margin:
            return Resolved(top_id, "fuzzy", round(top / 100, 3), "full", top_alias)
        return Unresolved("ambiguous", candidates)
    if top >= s.er_review_floor:
        return Unresolved("below_threshold", candidates)
    return Unresolved("no_match", candidates)


# Week 3 stages (embedding match, LLM adjudication) plug in here with the same signature.
DEFAULT_STAGES: tuple[Stage, ...] = (exact_stage, fuzzy_stage)


@dataclass
class Resolver:
    index: AliasIndex
    settings: Settings
    stages: tuple[Stage, ...] = DEFAULT_STAGES
    _memo: dict[tuple[str, bool], Outcome] = field(default_factory=dict)

    def resolve(self, mention: str, suspected_noise: bool = False) -> Outcome:
        normalized = normalize(mention)
        key = (normalized, suspected_noise)
        if key not in self._memo:
            self._memo[key] = self._run(normalized, suspected_noise)
        return self._memo[key]

    def _run(self, normalized: str, suspected_noise: bool) -> Outcome:
        if not normalized:
            return Unresolved("suspected_noise", final=True)
        ctx = MentionContext(
            normalized, tuple(variants(normalized)), self.index, self.settings, suspected_noise
        )
        last: Unresolved = Unresolved("no_match")
        for stage in self.stages:
            outcome = stage(ctx)
            if isinstance(outcome, Resolved):
                return outcome
            if outcome is not None:
                last = outcome
                if outcome.final:
                    break
        if suspected_noise and last.reason in ("no_match", "below_threshold"):
            return Unresolved("suspected_noise", last.candidates, final=True)
        return last
