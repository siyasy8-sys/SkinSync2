from app.config import Settings
from app.resolution.cascade import Resolved, Resolver, Unresolved, exact_stage, fuzzy_stage
from app.resolution.index import IngredientRef, InMemoryAliasIndex

NAMES = {
    1: "aqua",
    2: "glycerin",
    3: "sodium hyaluronate",
    4: "hydrolyzed sodium hyaluronate",
    5: "caprylic/capric triglyceride",
    6: "ci 77891",
    7: "titanium dioxide",
    8: "tocopherol",
    9: "tocopheryl acetate",
    10: "phenoxyethanol",
    11: "zea mays starch",
    12: "parfum",
}
EXTRA_ALIASES = {"water": {1}, "fragrance": {12}, "shared name": {2, 8}, "ceteth-2": {13}}


def _index() -> InMemoryAliasIndex:
    aliases: dict[str, set[int]] = {name: {i} for i, name in NAMES.items()}
    aliases.update(EXTRA_ALIASES)
    refs = {i: IngredientRef(i, str(1000 + i), name.upper()) for i, name in NAMES.items()}
    refs[13] = IngredientRef(13, "1013", "CETETH-2")
    return InMemoryAliasIndex(aliases, refs)


def _resolver(**overrides: object) -> Resolver:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused", **overrides)  # type: ignore[arg-type]
    return Resolver(_index(), settings)


def _resolved_id(mention: str, resolver: Resolver | None = None) -> int | None:
    outcome = (resolver or _resolver()).resolve(mention)
    return outcome.ingredient_id if isinstance(outcome, Resolved) else None


def test_exact_match_on_label_text() -> None:
    outcome = _resolver().resolve("GLYCERIN")
    assert outcome == Resolved(2, "exact", 1.0, "full", "glycerin")


def test_slashed_inci_name_matches_whole_before_splitting() -> None:
    outcome = _resolver().resolve("Caprylic/ Capric Triglyceride")
    assert isinstance(outcome, Resolved)
    assert (outcome.ingredient_id, outcome.variant) == (5, "full")


def test_parenthetical_forms() -> None:
    assert _resolved_id("CI 77891 (Titanium Dioxide)") == 6  # the INCI outside wins
    assert _resolved_id("Zea Mays (Corn) Starch") == 11
    assert _resolved_id("Tocopherol (Sunflower Vitamin E)") == 8


def test_slash_synonyms_that_agree_resolve() -> None:
    outcome = _resolver().resolve("Aqua/Water/Eau")
    assert isinstance(outcome, Resolved)
    assert (outcome.ingredient_id, outcome.variant, outcome.confidence) == (1, "slash_part", 0.95)


def test_slash_parts_naming_different_ingredients_are_ambiguous() -> None:
    outcome = _resolver().resolve("Glycerin/Parfum")
    assert isinstance(outcome, Unresolved)
    assert outcome.reason == "ambiguous"
    assert {c.ingredient_id for c in outcome.candidates} == {2, 12}


def test_alias_shared_by_two_ingredients_is_ambiguous_and_stops() -> None:
    ctx_outcome = _resolver().resolve("Shared Name")
    assert isinstance(ctx_outcome, Unresolved)
    assert ctx_outcome.reason == "ambiguous"
    assert ctx_outcome.final


def test_fuzzy_resolves_misspellings_above_threshold() -> None:
    outcome = _resolver().resolve("Glycerine")
    assert isinstance(outcome, Resolved)
    assert (outcome.ingredient_id, outcome.stage) == (2, "fuzzy")
    assert 0.92 <= outcome.confidence < 1.0
    assert _resolved_id("Phenoxyeth - anol") == 10  # line-wrap break


def test_token_set_subset_trap_is_why_we_use_token_sort() -> None:
    # "Sodium Hyaluronate Crosspolymer" is a different ingredient. token_set_ratio treats a
    # subset as a perfect match (100) and would silently resolve it to sodium hyaluronate.
    mention = "Sodium Hyaluronate Crosspolymer"

    assert _resolved_id(mention) is None  # token_sort (default): not accepted

    token_set = _resolver(er_fuzzy_scorer="token_set_ratio").resolve(mention)
    assert token_set == Resolved(3, "fuzzy", 1.0, "full", "sodium hyaluronate")  # the false match


def test_ester_is_not_resolved_to_parent() -> None:
    assert _resolved_id("Tocopheryl Acetate") == 9
    assert _resolved_id("Tocopheryl Acetat") == 9  # misspelled ester still maps to the ester


def test_review_band_and_no_match_keep_candidates() -> None:
    band = _resolver(er_fuzzy_threshold=99).resolve("Glycerine")
    assert isinstance(band, Unresolved)
    assert band.reason == "below_threshold"
    assert band.candidates[0].cosing_id == "1002"

    none = _resolver().resolve("Kommer senere")
    assert isinstance(none, Unresolved)
    assert none.reason == "no_match"


def test_margin_rule_blocks_near_ties() -> None:
    resolver = _resolver(er_fuzzy_threshold=50, er_fuzzy_margin=50)
    outcome = resolver.resolve("Glycerine")
    assert isinstance(outcome, Unresolved)
    assert outcome.reason == "ambiguous"
    assert not outcome.final  # a near-tie leaves room for later stages (Week 3)


def test_suspected_noise_reason_only_when_unresolved() -> None:
    resolver = _resolver()
    noisy = resolver.resolve("Distributed by Rosebud Company", suspected_noise=True)
    assert isinstance(noisy, Unresolved)
    assert noisy.reason == "suspected_noise"
    assert isinstance(resolver.resolve("Glycerin", suspected_noise=True), Resolved)


def test_stages_are_independently_callable_and_ablatable() -> None:
    exact_only = _resolver()
    exact_only.stages = (exact_stage,)
    assert isinstance(exact_only.resolve("Glycerine"), Unresolved)

    fuzzy_only = _resolver()
    fuzzy_only.stages = (fuzzy_stage,)
    assert _resolved_id("Glycerin", fuzzy_only) == 2


def test_results_are_memoized_per_normalized_text() -> None:
    resolver = _resolver()
    assert resolver.resolve("GLYCERIN") is resolver.resolve(" glycerin. ")
