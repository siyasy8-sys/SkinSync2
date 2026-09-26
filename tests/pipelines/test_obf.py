import gzip
import json
import re

import httpx

from app.config import Settings
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.sources.obf import (
    ObfProduct,
    fetch_obf,
    matched_seeds,
    parse_product,
    seed_patterns,
    select_sample,
)

CATEGORY_RE = re.compile(r"skin|face|moistur|cream")


def _record(code: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "code": code,
        "product_name": f"Product {code}",
        "brands": "Brand A, Parent Co",
        "categories_tags": ["en:beauty", "en:face-care", "en:face-creams"],
        "ingredients_text": "Aqua, Niacinamide, Glycerin",
        "unrelated_field": "dropped from cache",
    }
    record.update(overrides)
    return record


def test_parse_product_maps_fields() -> None:
    product = parse_product(_record("1", ingredients_text_en="Water, Niacinamide"), CATEGORY_RE)

    assert product is not None
    assert product.source == "open_beauty_facts"
    assert product.source_id == "1"
    assert product.brand == "Brand A"
    assert product.category == "face-creams"  # last matching tag = most specific
    assert product.raw_ingredient_text == "Water, Niacinamide"  # English text preferred


def test_parse_product_rejects_missing_ingredients_or_non_skincare() -> None:
    assert parse_product(_record("1", ingredients_text="  "), CATEGORY_RE) is None
    assert parse_product(_record("2", product_name=None), CATEGORY_RE) is None
    assert parse_product(_record("3", categories_tags=["en:shampoos"]), CATEGORY_RE) is None


def _candidate(
    code: str, ingredients: str, *, english: bool = True
) -> tuple[dict[str, object], ObfProduct]:
    record = _record(code, ingredients_text=ingredients, lang="en" if english else "fr")
    product = parse_product(record, CATEGORY_RE)
    assert product is not None
    return record, product


def _codes(sample: list[dict[str, object]]) -> list[object]:
    return [r["code"] for r in sample]


def test_whole_word_seed_match() -> None:
    patterns = seed_patterns(("TOCOPHEROL", "RETINOL"))

    assert matched_seeds("Aqua, Tocopheryl Acetate", patterns) == set()
    assert matched_seeds("AQUA, TOCOPHEROL, RETINOL.", patterns) == {"TOCOPHEROL", "RETINOL"}
    assert matched_seeds("Hydroxypinacolone Retinoate", patterns) == set()


def test_seed_quota_prefers_english_then_fills_to_limit() -> None:
    candidates = [
        _candidate("fr-ret", "Aqua, Retinol", english=False),
        _candidate("plain-1", "Aqua, Glycerin"),
        _candidate("en-ret", "Aqua, Retinol"),
        _candidate("plain-2", "Aqua"),
    ]

    sample = select_sample(candidates, ("RETINOL",), min_per_seed=1, limit=3, prefer_english=True)

    # Quota: English retinol product first. Fill: remaining English in dump order.
    assert _codes(sample) == ["en-ret", "plain-1", "plain-2"]


def test_rare_seed_takes_everything_available_including_non_english() -> None:
    candidates = [
        _candidate("en-aze", "Azelaic Acid"),
        _candidate("fr-aze", "Azelaic Acid", english=False),
        _candidate("plain", "Aqua"),
    ]

    sample = select_sample(
        candidates, ("AZELAIC ACID",), min_per_seed=10, limit=10, prefer_english=True
    )

    assert _codes(sample)[:2] == ["en-aze", "fr-aze"]


def test_a_product_counts_for_every_seed_it_contains() -> None:
    candidates = [
        _candidate("both", "Niacinamide, Retinol"),
        _candidate("nia", "Niacinamide"),
        _candidate("ret", "Retinol"),
    ]

    sample = select_sample(
        candidates, ("NIACINAMIDE", "RETINOL"), min_per_seed=1, limit=1, prefer_english=True
    )

    assert _codes(sample) == ["both"]


def test_selection_is_deterministic() -> None:
    candidates = [_candidate(str(i), "Niacinamide" if i % 3 else "Aqua") for i in range(20)]

    def run(c: list[tuple[dict[str, object], ObfProduct]]) -> list[object]:
        return _codes(
            select_sample(c, ("NIACINAMIDE",), min_per_seed=4, limit=8, prefer_english=True)
        )

    first, second = run(candidates), run(list(candidates))

    assert first == second


def test_fetch_scans_dump_caches_sample_and_reports_coverage(settings: Settings) -> None:
    lines = [
        _record("1", lang="fr"),
        _record("2", categories_tags=["en:shampoos"]),  # filtered out
        _record("1"),  # duplicate barcode
        _record("3", ingredients_text="Aqua, Retinol", lang="en"),
        _record("4", lang="en"),
    ]
    dump = gzip.compress("\n".join(json.dumps(r) for r in lines).encode())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=dump)

    small = settings.model_copy(update={"obf_sample_limit": 2, "obf_min_per_seed": 1})
    seeds = ("RETINOL", "NIACINAMIDE")
    with make_client(small, httpx.MockTransport(handler)) as client:
        result = fetch_obf(client, RawCache(small.data_dir, "obf"), small, seeds)
        cached = fetch_obf(client, RawCache(small.data_dir, "obf"), small, seeds)

    # Retinol quota first (product 3), then English fill (product 4) up to the limit.
    assert [p.source_id for p in result.products] == ["3", "4"]
    assert result.english == 2
    assert result.per_seed == {"RETINOL": 1, "NIACINAMIDE": 1}
    assert cached.products == result.products
    assert len(calls) == 1
    sample = next(small.data_dir.glob("obf/*/sample-*.jsonl")).read_text()
    assert "unrelated_field" not in sample

    # A different selection parameter must not reuse the cached sample.
    bigger = small.model_copy(update={"obf_sample_limit": 3})
    with make_client(bigger, httpx.MockTransport(handler)) as client:
        fetch_obf(client, RawCache(bigger.data_dir, "obf"), bigger, seeds)
    assert len(calls) == 2
