"""EU CosIng ingredients, via the search API behind the CosIng web app.

See pipelines/SOURCES.md for the license (CC BY 4.0) and access caveats.
"""

import json
import re
from functools import partial
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Ingredient
from pipelines.cache import RawCache
from pipelines.http import RateLimiter, send
from pipelines.upsert import upsert

RESTRICTION_FIELDS = (
    "annexNo",
    "refNo",
    "cosmeticRestriction",
    "maximumConcentration",
    "wordingOfConditions",
    "otherRestrictions",
    "sccsOpinion",
)
EMPTY_VALUES = {"", "-"}


class CosingIngredient(BaseModel):
    cosing_id: str
    inci_name: str
    functions: list[str]
    cas_number: str | None
    restrictions: dict[str, list[str]] | None


def _values(metadata: dict[str, Any], key: str) -> list[str]:
    raw = metadata.get(key) or []
    values = raw if isinstance(raw, list) else [raw]
    return [str(v).strip() for v in values if str(v).strip() not in EMPTY_VALUES]


def parse_ingredient(metadata: dict[str, Any]) -> CosingIngredient | None:
    """Maps one search-result `metadata` dict to a record; None if it isn't a usable ingredient."""
    if _values(metadata, "itemType") != ["ingredient"]:
        return None
    ids = _values(metadata, "substanceId")
    names = _values(metadata, "inciName")
    if not ids or not names:
        return None
    restrictions = {f: vals for f in RESTRICTION_FIELDS if (vals := _values(metadata, f))}
    cas = _values(metadata, "casNo")
    return CosingIngredient(
        cosing_id=ids[0],
        inci_name=names[0],
        functions=_values(metadata, "functionName"),
        cas_number=cas[0] if cas and cas[0].strip(" /-") else None,
        restrictions=restrictions or None,
    )


def validate_search_response(payload: bytes) -> None:
    """Raises ValueError unless the payload is a search result (not an error body)."""
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError(f"unexpected CosIng response: {str(data)[:200]}")


def parse_search_response(payload: bytes) -> list[CosingIngredient]:
    data = json.loads(payload)
    records = (parse_ingredient(r.get("metadata", {})) for r in data.get("results", []))
    return [r for r in records if r is not None]


def _search(
    client: httpx.Client,
    limiter: RateLimiter,
    settings: Settings,
    *,
    text: str,
    must: list[dict[str, Any]],
    page_size: int,
    page_number: int,
) -> bytes:
    request = client.build_request(
        "POST",
        settings.cosing_search_url,
        params={
            "apiKey": settings.cosing_api_key,
            "text": text,
            "pageSize": page_size,
            "pageNumber": page_number,
        },
        files={"query": (None, json.dumps({"bool": {"must": must}}), "application/json")},
    )
    return send(
        client,
        limiter,
        request,
        max_retries=settings.http_max_retries,
        backoff_seconds=settings.http_backoff_seconds,
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch_cosing(
    client: httpx.Client, cache: RawCache, settings: Settings, seeds: tuple[str, ...]
) -> list[CosingIngredient]:
    """A few pages of active ingredients plus an exact-name lookup for each seed.

    Deduped on CosIng substance ID, first occurrence wins.
    """
    limiter = RateLimiter(settings.cosing_requests_per_second)
    active_ingredient = [{"term": {"itemType": "ingredient"}}, {"term": {"status": "Active"}}]
    payloads: list[bytes] = []
    for page in range(1, settings.cosing_sample_pages + 1):
        payloads.append(
            cache.fetch(
                f"page-{page:03d}.json",
                partial(
                    _search,
                    client,
                    limiter,
                    settings,
                    text="*",
                    must=active_ingredient,
                    page_size=settings.cosing_page_size,
                    page_number=page,
                ),
                validate=validate_search_response,
            )
        )
    for seed in seeds:
        payloads.append(
            cache.fetch(
                f"seed-{_slug(seed)}.json",
                partial(
                    _search,
                    client,
                    limiter,
                    settings,
                    text="*",
                    must=[{"term": {"itemType": "ingredient"}}, {"term": {"inciName": seed}}],
                    page_size=5,
                    page_number=1,
                ),
                validate=validate_search_response,
            )
        )

    unique: dict[str, CosingIngredient] = {}
    for payload in payloads:
        for record in parse_search_response(payload):
            unique.setdefault(record.cosing_id, record)
    return list(unique.values())


def load_ingredients(session: Session, records: list[CosingIngredient]) -> tuple[int, int]:
    return upsert(session, Ingredient, [r.model_dump() for r in records], ["cosing_id"])
