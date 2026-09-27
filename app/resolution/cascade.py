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
from app.resolution.normalize import Variant, VariantKind, compact, normalize, variants

StageName = Literal["exact", "fuzzy", "embedding", "llm"]
Reason = Literal["no_match", "ambiguous", "below_threshold", "suspected_noise"]

# Exact hits on a derived form are slightly less certain than on the label text itself.
EXACT_CONFIDENCE: dict[VariantKind, float] = {
    "full": 1.0,
    "no_paren": 0.98,
    "paren": 0.95,
    "slash_part": 0.95,
    "clause": 0.95,
}
# A match found only after removing spaces/hyphens ("sod ium hyaluronate").
COMPACT_CONFIDENCE = 0.97
# Derived variants (parentheses, slash parts, clauses) are only trusted on short mentions;
# on a whole unseparated list or a paragraph they would pluck out an arbitrary name.
DERIVED_KINDS: frozenset[VariantKind] = frozenset({"paren", "slash_part", "clause"})
INCI_TIEBREAK_CONFIDENCE = 0.95


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


def _inci_tiebreak(hits: dict[int, str]) -> int | None:
    """The one ingredient whose official INCI name is this text, if exactly one is.

    "water" is the INCI name of WATER and the US name of AQUA; the INCI entry wins.
    """
    inci = [i for i, source in hits.items() if source == "inci"]
    return inci[0] if len(inci) == 1 else None


def exact_stage(ctx: MentionContext) -> Outcome | None:
    """Exact alias lookup over the mention's variants.

    1. The label text itself naming one ingredient wins outright.
    2. Otherwise the variants corroborate each other: "Titanium Dioxide (CI 77891)" and
       "Water/Aqua" give two names for one thing, so the answer is the single ingredient
       that every variant with a hit points to.
    3. A single name shared by several ingredients falls back to the one whose official
       INCI name it is (if enabled); anything else is ambiguous and stops the cascade.
    """
    usable = [
        v
        for v in ctx.variants
        if v.kind not in DERIVED_KINDS
        or len(ctx.normalized.split()) <= ctx.settings.er_max_words_for_parts
    ]
    hits: list[tuple[Variant, dict[int, str]]] = []
    via_compact: set[str] = set()
    for v in usable:
        found = ctx.index.lookup(v.text)
        if not found:
            found = ctx.index.lookup_compact(compact(v.text))
            if found:
                via_compact.add(v.text)
        if found:
            hits.append((v, found))
    if not hits:
        return None
    first_variant, first_hits = hits[0]

    def confidence(variant: Variant) -> float:
        base = EXACT_CONFIDENCE[variant.kind]
        return min(base, COMPACT_CONFIDENCE) if variant.text in via_compact else base

    if first_variant.kind == "full" and len(first_hits) == 1:
        (ingredient_id,) = first_hits
        return Resolved(
            ingredient_id, "exact", confidence(first_variant), "full", first_variant.text
        )

    common = set(first_hits).intersection(*(h for _, h in hits[1:]))
    if len(common) == 1:
        (ingredient_id,) = common
        return Resolved(
            ingredient_id,
            "exact",
            confidence(first_variant),
            first_variant.kind,
            first_variant.text,
        )
    if not common:
        # Variants disagree. The most faithful non-slash variant naming one ingredient wins,
        # as the old behaviour did; disagreeing slash parts stay ambiguous.
        for variant, found in hits:
            if variant.kind != "slash_part" and len(found) == 1:
                (ingredient_id,) = found
                return Resolved(
                    ingredient_id,
                    "exact",
                    EXACT_CONFIDENCE[variant.kind],
                    variant.kind,
                    variant.text,
                )
        pool = {i: first_variant.text for _, found in hits for i in found}
    else:
        if ctx.settings.er_inci_tiebreak:
            winner = _inci_tiebreak({i: first_hits[i] for i in common})
            if winner is not None:
                return Resolved(
                    winner,
                    "exact",
                    min(confidence(first_variant), INCI_TIEBREAK_CONFIDENCE),
                    first_variant.kind,
                    first_variant.text,
                )
        pool = {i: first_variant.text for i in common}
    scored = [(i, alias, 100.0) for i, alias in sorted(pool.items())]
    return Unresolved("ambiguous", _candidates(ctx, scored), final=True)


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
    short = len(ctx.normalized.split()) <= s.er_max_words_for_parts
    queries = [
        v.text
        for v in ctx.variants
        if v.kind in ("full", "no_paren") or (short and v.kind == "clause")
    ]
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

    @property
    def distinct_resolved(self) -> int:
        """Distinct normalized mentions resolved so far (each runs the cascade once)."""
        return len(self._memo)

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
