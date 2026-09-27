import json
import re
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.sources.cosing import (
    InventoryError,
    fetch_cosing,
    fetch_cosing_inventory,
    parse_ingredient,
    parse_search_response,
)
from tests.conftest import FIXTURES


def test_parse_search_response_maps_fields_and_skips_non_ingredients() -> None:
    records = parse_search_response((FIXTURES / "cosing_search.json").read_bytes())

    assert [r.cosing_id for r in records] == ["35499", "79036", "83218"]
    niacinamide = records[0]
    assert niacinamide.inci_name == "NIACINAMIDE"
    assert niacinamide.cas_number == "98-92-0"
    assert niacinamide.functions == ["SMOOTHING"]
    assert niacinamide.restrictions is None  # empty lists aren't restrictions


def test_parse_keeps_multi_value_cas_raw_and_drops_placeholder_dash() -> None:
    records = {
        r.cosing_id: r
        for r in parse_search_response((FIXTURES / "cosing_search.json").read_bytes())
    }

    assert records["79036"].cas_number == "97660-24-7 / 8038-93-5"
    assert records["83218"].cas_number is None
    assert records["83218"].restrictions == {"annexNo": ["III"], "maximumConcentration": ["2 %"]}


def test_parse_ingredient_requires_id_and_name() -> None:
    assert parse_ingredient({"itemType": ["ingredient"], "inciName": ["X"]}) is None
    assert parse_ingredient({"itemType": ["ingredient"], "substanceId": ["1"]}) is None


def test_fetch_sends_api_key_and_filter_and_uses_cache(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    body = (FIXTURES / "cosing_search.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=body)

    seeds = ("NIACINAMIDE",)
    with make_client(settings, httpx.MockTransport(handler)) as client:
        records = fetch_cosing(client, RawCache(settings.data_dir, "cosing"), settings, seeds)
        again = fetch_cosing(client, RawCache(settings.data_dir, "cosing"), settings, seeds)

    assert len(requests) == 2  # one page + one seed; the second run is all cache hits
    page, seed = requests
    assert page.url.params["apiKey"] == settings.cosing_api_key
    assert page.url.params["pageSize"] == str(settings.cosing_page_size)
    assert '{"term": {"status": "Active"}}' in page.content.decode()
    assert json.dumps({"term": {"inciName": "NIACINAMIDE"}}) in seed.content.decode()
    # Both responses contain the same records; dedupe on substance ID.
    assert [r.cosing_id for r in records] == ["35499", "79036", "83218"]
    assert again == records


def test_error_body_is_rejected_and_not_cached(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "Service unavailable"})

    with make_client(settings, httpx.MockTransport(handler)) as client, pytest.raises(ValueError):
        fetch_cosing(client, RawCache(settings.data_dir, "cosing"), settings, ())

    assert not list(settings.data_dir.glob("cosing/*/*.json"))


def _inventory_handler(
    ids: list[str], *, reported_total: int | None = None, partition_total: int | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """Fake search API: filters `ids` by the substanceId range (compared as text)."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(re.search(rb'\{"bool".*\}', request.content).group())  # type: ignore[union-attr]
        ranges = [m["range"]["substanceId"] for m in query["bool"]["must"] if "range" in m]
        hits = ids
        if ranges:
            lo, hi = ranges[0].get("gte"), ranges[0].get("lt")
            hits = [i for i in ids if (lo is None or i >= str(lo)) and (hi is None or i < str(hi))]
        size = int(request.url.params["pageSize"])
        page = int(request.url.params["pageNumber"])
        chunk = hits[(page - 1) * size : page * size]
        total = len(hits)
        if not ranges and reported_total is not None:
            total = reported_total
        if ranges and partition_total is not None:
            total = partition_total
        results = [
            {"metadata": {"itemType": ["ingredient"], "substanceId": [i], "inciName": [f"X{i}"]}}
            for i in chunk
        ]
        return httpx.Response(200, json={"totalResults": total, "results": results})

    return handler


def test_inventory_fetches_every_partition_and_checks_completeness(settings: Settings) -> None:
    ids = ["101", "1020", "305", "9001", "99"] + [f"5{n:03d}" for n in range(7)]
    small_pages = settings.model_copy(update={"cosing_inventory_page_size": 3})

    with make_client(small_pages, httpx.MockTransport(_inventory_handler(ids))) as client:
        records, duplicates = fetch_cosing_inventory(
            client, RawCache(small_pages.data_dir, "cosing"), small_pages
        )

    assert sorted(r.cosing_id for r in records) == sorted(ids)
    assert duplicates == 0


def test_inventory_dedupes_identical_duplicate_documents(settings: Settings) -> None:
    ids = ["101", "101", "305"]  # the index returns 101 twice, identically
    with make_client(settings, httpx.MockTransport(_inventory_handler(ids))) as client:
        records, duplicates = fetch_cosing_inventory(
            client, RawCache(settings.data_dir, "cosing"), settings
        )

    assert sorted(r.cosing_id for r in records) == ["101", "305"]
    assert duplicates == 1


def test_inventory_requests_a_stable_sort(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    inner = _inventory_handler(["101"])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return inner(request)

    with make_client(settings, httpx.MockTransport(handler)) as client:
        fetch_cosing_inventory(client, RawCache(settings.data_dir, "cosing"), settings)

    sort = json.dumps([{"field": "substanceId", "order": "ASC"}]).encode()
    assert all(sort in r.content for r in requests)


def test_inventory_fails_when_partitions_miss_records(settings: Settings) -> None:
    handler = _inventory_handler(["101", "305"], reported_total=3)  # API claims 3 hits
    with (
        make_client(settings, httpx.MockTransport(handler)) as client,
        pytest.raises(InventoryError, match="expected 3"),
    ):
        fetch_cosing_inventory(client, RawCache(settings.data_dir, "cosing"), settings)


def test_inventory_fails_when_a_partition_exceeds_the_paging_window(settings: Settings) -> None:
    handler = _inventory_handler(["101"], partition_total=10_001)
    with (
        make_client(settings, httpx.MockTransport(handler)) as client,
        pytest.raises(InventoryError, match="paging window"),
    ):
        fetch_cosing_inventory(client, RawCache(settings.data_dir, "cosing"), settings)


def test_parse_keeps_status_and_other_names() -> None:
    record = parse_ingredient(
        {
            "itemType": ["ingredient"],
            "substanceId": ["1"],
            "inciName": ["AQUA"],
            "status": ["Active"],
            "inciUsaName": ["WATER"],
            "innName": [],
            "phEurName": ["-"],
        }
    )

    assert record is not None
    assert record.status == "Active"
    assert record.names == {"inci_usa": ["WATER"]}
