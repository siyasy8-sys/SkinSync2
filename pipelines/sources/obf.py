"""Open Beauty Facts products, sampled from the official daily JSONL dump.

OBF doesn't allow bulk scraping of its API, so we stream the dump and stop
once the sample is full. See pipelines/SOURCES.md (ODbL).
"""

import json
import re
import zlib
from collections.abc import Generator
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
)


class ObfProduct(BaseModel):
    source: str = SOURCE
    source_id: str
    name: str
    brand: str | None
    category: str | None
    raw_ingredient_text: str


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

    Closing the generator early closes the HTTP stream, so we only download
    as much of the (~100 MB) file as the sample needs.
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


def _sample_dump(client: httpx.Client, settings: Settings, limit: int) -> bytes:
    category_re = re.compile(settings.obf_category_pattern)
    seen: set[str] = set()
    kept: list[str] = []
    records = iter_dump_records(client, settings.obf_dump_url)
    try:
        for record in records:
            product = parse_product(record, category_re)
            if product is None or product.source_id in seen:
                continue
            seen.add(product.source_id)
            kept.append(json.dumps({k: record.get(k) for k in KEPT_FIELDS}, ensure_ascii=False))
            if len(kept) >= limit:
                break
    finally:
        records.close()
    return ("\n".join(kept) + "\n").encode()


def fetch_obf(
    client: httpx.Client, cache: RawCache, settings: Settings, limit: int
) -> list[ObfProduct]:
    payload = cache.fetch(f"sample-{limit}.jsonl", lambda: _sample_dump(client, settings, limit))
    category_re = re.compile(settings.obf_category_pattern)
    products: dict[str, ObfProduct] = {}
    for line in payload.decode().splitlines():
        if line.strip() and (product := parse_product(json.loads(line), category_re)):
            products.setdefault(product.source_id, product)
    return list(products.values())


def load_products(session: Session, records: list[ObfProduct]) -> tuple[int, int]:
    return upsert(session, Product, [r.model_dump() for r in records], ["source", "source_id"])
