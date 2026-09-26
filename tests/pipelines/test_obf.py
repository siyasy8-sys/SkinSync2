import gzip
import json
import re

import httpx

from app.config import Settings
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.sources.obf import fetch_obf, parse_product

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


def test_fetch_streams_until_limit_and_caches_sample(settings: Settings) -> None:
    lines = [
        _record("1"),
        _record("2", categories_tags=["en:shampoos"]),  # filtered out
        _record("1"),  # duplicate barcode
        _record("3"),
        _record("4"),
    ]
    dump = gzip.compress("\n".join(json.dumps(r) for r in lines).encode())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=dump)

    with make_client(settings, httpx.MockTransport(handler)) as client:
        products = fetch_obf(client, RawCache(settings.data_dir, "obf"), settings, limit=2)
        cached = fetch_obf(client, RawCache(settings.data_dir, "obf"), settings, limit=2)

    assert [p.source_id for p in products] == ["1", "3"]
    assert cached == products
    assert len(calls) == 1
    sample = next(settings.data_dir.glob("obf/*/sample-2.jsonl")).read_text()
    assert "unrelated_field" not in sample
