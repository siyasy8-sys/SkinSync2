"""Open Beauty Facts products, sampled from the official daily JSONL dump.

OBF doesn't allow bulk scraping of its API, so we stream the dump and keep a
seeded sample: each seed active in at least `obf_min_per_seed` products where
the data allows, English product names first. See pipelines/SOURCES.md (ODbL).
"""

import hashlib
import json
import re
import zlib
from collections import Counter
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Product
from pipelines.cache import RawCache
from pipelines.upsert import upsert

SOURCE = "open_beauty_facts"
# Only these fields are cached, which keeps the sample file small.
KEPT_FIELDS = (
    "code",
    "product_name",
    "product_name_en",
    "brands",
    "categories_tags",
    "ingredients_text",
    "ingredients_text_en",
    "lang",
)


class ObfProduct(BaseModel):
    source: str = SOURCE
    source_id: str
    name: str
    brand: str | None
    category: str | None
    raw_ingredient_text: str


@dataclass
class ObfFetchResult:
    products: list[ObfProduct]
    english: int
    per_seed: dict[str, int] = field(default_factory=dict)


def _text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_product(record: dict[str, Any], category_re: re.Pattern[str]) -> ObfProduct | None:
    """None unless the record has a barcode, name, ingredient text and a skincare category."""
    code = _text(record, "code")
    name = _text(record, "product_name_en", "product_name")
    ingredients = _text(record, "ingredients_text_en", "ingredients_text")
    if not (code and name and ingredients):
        return None
    tags = record.get("categories_tags") or []
    # Tags look like "en:face-creams"; the last matching tag is the most specific.
    matching = [t.split(":", 1)[-1] for t in tags if category_re.search(t.split(":", 1)[-1])]
    if not matching:
        return None
    brands = _text(record, "brands")
    return ObfProduct(
        source_id=code,
        name=name,
        brand=brands.split(",")[0].strip() if brands else None,
        category=matching[-1],
        raw_ingredient_text=ingredients,
    )


def iter_dump_records(client: httpx.Client, url: str) -> Generator[dict[str, Any]]:
    """Streams and gunzips the dump, yielding one JSON record per line.

    The ~100 MB file is decompressed as it streams, never held in memory.
    """
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    buffer = b""
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            buffer += decompressor.decompress(chunk)
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                if line.strip():
                    yield json.loads(line)
    buffer += decompressor.flush()
    if buffer.strip():
        yield json.loads(buffer)


def is_english(record: dict[str, Any]) -> bool:
    return _text(record, "product_name_en") is not None or record.get("lang") == "en"


def seed_patterns(seeds: tuple[str, ...]) -> dict[str, re.Pattern[str]]:
    """Case-insensitive whole-word match, so "tocopherol" doesn't match "tocopheryl".

    A sampling heuristic only; entity resolution (Week 2) does the real matching.
    """
    return {
        seed: re.compile(rf"(?<![a-z0-9]){re.escape(seed.lower())}(?![a-z0-9])") for seed in seeds
    }


def matched_seeds(ingredient_text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    text = ingredient_text.lower()
    return {seed for seed, pattern in patterns.items() if pattern.search(text)}


def select_sample(
    candidates: list[tuple[dict[str, Any], ObfProduct]],
    seeds: tuple[str, ...],
    *,
    min_per_seed: int,
    limit: int,
    prefer_english: bool,
) -> list[dict[str, Any]]:
    """Deterministic seeded sample from eligible records (in dump order, deduped).

    1. Each seed gets products until it appears in `min_per_seed` of them (or
       runs out); a product counts for every seed it contains.
    2. The rest is filled up to `limit`.
    English products go first in both steps when `prefer_english` is set.
    """
    patterns = seed_patterns(seeds)
    ordered = sorted(
        candidates, key=lambda c: prefer_english and not is_english(c[0])
    )  # stable sort: dump order within each language group
    seeds_of = {p.source_id: matched_seeds(p.raw_ingredient_text, patterns) for _, p in ordered}
    chosen: dict[str, dict[str, Any]] = {}
    coverage: Counter[str] = Counter()

    def take(record: dict[str, Any], product: ObfProduct) -> None:
        chosen[product.source_id] = record
        coverage.update(seeds_of[product.source_id])

    for seed in seeds:
        for record, product in ordered:
            if coverage[seed] >= min_per_seed or len(chosen) >= limit:
                break
            if seed in seeds_of[product.source_id] and product.source_id not in chosen:
                take(record, product)
    for record, product in ordered:
        if len(chosen) >= limit:
            break
        if product.source_id not in chosen:
            take(record, product)
    return list(chosen.values())


def _sample_dump(client: httpx.Client, settings: Settings, seeds: tuple[str, ...]) -> bytes:
    """Scans the whole dump: rare seeds may only appear near the end."""
    category_re = re.compile(settings.obf_category_pattern)
    candidates: dict[str, tuple[dict[str, Any], ObfProduct]] = {}
    for record in iter_dump_records(client, settings.obf_dump_url):
        product = parse_product(record, category_re)
        if product is not None and product.source_id not in candidates:
            candidates[product.source_id] = ({k: record.get(k) for k in KEPT_FIELDS}, product)
    sample = select_sample(
        list(candidates.values()),
        seeds,
        min_per_seed=settings.obf_min_per_seed,
        limit=settings.obf_sample_limit,
        prefer_english=settings.obf_prefer_english,
    )
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sample).encode()


def _sample_key(settings: Settings, seeds: tuple[str, ...]) -> str:
    """Hashes every selection parameter, so changing one never reuses an old sample."""
    params = json.dumps(
        [
            settings.obf_sample_limit,
            settings.obf_min_per_seed,
            settings.obf_prefer_english,
            settings.obf_category_pattern,
            seeds,
        ]
    )
    return f"sample-{hashlib.sha1(params.encode()).hexdigest()[:8]}.jsonl"


def fetch_obf(
    client: httpx.Client, cache: RawCache, settings: Settings, seeds: tuple[str, ...]
) -> ObfFetchResult:
    payload = cache.fetch(
        _sample_key(settings, seeds), lambda: _sample_dump(client, settings, seeds)
    )
    category_re = re.compile(settings.obf_category_pattern)
    products: dict[str, ObfProduct] = {}
    english = 0
    for line in payload.decode().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        product = parse_product(record, category_re)
        if product and product.source_id not in products:
            products[product.source_id] = product
            english += is_english(record)
    patterns = seed_patterns(seeds)
    coverage: Counter[str] = Counter()
    for product in products.values():
        coverage.update(matched_seeds(product.raw_ingredient_text, patterns))
    return ObfFetchResult(
        products=list(products.values()),
        english=english,
        per_seed={seed: coverage[seed] for seed in seeds},
    )


def load_products(session: Session, records: list[ObfProduct]) -> tuple[int, int]:
    return upsert(session, Product, [r.model_dump() for r in records], ["source", "source_id"])
