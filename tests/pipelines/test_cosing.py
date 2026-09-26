import json

import httpx

from app.config import Settings
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.sources.cosing import fetch_cosing, parse_ingredient, parse_search_response
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
