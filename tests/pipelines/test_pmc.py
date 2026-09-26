import json
from datetime import date

import httpx
import pytest

from app.config import Settings
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.sources.pmc import (
    MissingNcbiEmailError,
    fetch_pmc,
    normalize_license,
    parse_efetch,
    search_query,
)
from tests.conftest import FIXTURES

ARTICLES_XML = (FIXTURES / "pmc_articles.xml").read_bytes()


def test_parse_efetch_accepts_cc_by_and_cc0_only() -> None:
    by, nc, cc0 = parse_efetch(ARTICLES_XML)

    assert nc is None  # CC BY-NC rejected
    assert by is not None and cc0 is not None
    assert by.source_id == "PMC1000001"
    assert by.pmid == "30000001"
    assert by.title == "Topical niacinamide and the skin barrier"
    assert by.license == "CC BY 4.0"
    assert by.url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC1000001/"
    assert by.published_at == date(2021, 3, 15)  # epub preferred over ppub
    assert by.full_text is None
    assert cc0.source_id == "PMC1000003"
    assert cc0.license == "CC0 1.0"
    assert cc0.published_at == date(2019, 1, 1)  # invalid month falls back to the year


def test_structured_abstract_keeps_headings_and_skips_graphical() -> None:
    by = parse_efetch(ARTICLES_XML)[0]

    assert by is not None
    assert by.abstract == (
        "Background: Niacinamide is a form of vitamin B3.\nResults: Barrier function improved."
    )


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        (["https://creativecommons.org/licenses/by/3.0/"], "CC BY 3.0"),
        (["http://creativecommons.org/publicdomain/zero/1.0/"], "CC0 1.0"),
        (["https://creativecommons.org/licenses/by-nc-nd/4.0/"], None),
        (["https://creativecommons.org/licenses/by-sa/4.0/"], None),
        (["https://example.com/publisher-license"], None),
        ([], None),
    ],
)
def test_normalize_license(urls: list[str], expected: str | None) -> None:
    assert normalize_license(urls) == expected


def test_fetch_identifies_to_ncbi_round_robins_and_caches(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    esearch_ids = {"niacinamide": ["1000001", "1000002"], "retinol": ["1000003", "1000001"]}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("esearch.fcgi"):
            seed = request.url.params["term"].split('"')[1]
            return httpx.Response(200, json={"esearchresult": {"idlist": esearch_ids[seed]}})
        return httpx.Response(200, content=ARTICLES_XML)

    with make_client(settings, httpx.MockTransport(handler)) as client:
        result = fetch_pmc(
            client, RawCache(settings.data_dir, "pubmed"), settings, ("NIACINAMIDE", "RETINOL"), 2
        )
        fetch_pmc(
            client, RawCache(settings.data_dir, "pubmed"), settings, ("NIACINAMIDE", "RETINOL"), 2
        )

    for request in requests:
        assert request.url.params["tool"] == "skinsync"
        assert request.url.params["email"] == "test@example.com"
        assert request.url.params["api_key"] == "k"
    efetch_ids = [r.url.params["id"] for r in requests if r.url.path.endswith("efetch.fcgi")]
    assert efetch_ids == ["1000001", "1000003"]  # round-robin: first hit of each seed
    assert len(requests) == 4  # second run is served from cache
    # Each fixture response holds all three articles; dedupe by PMCID.
    assert {a.source_id for a in result.articles} == {"PMC1000001", "PMC1000003"}
    assert result.rejected_license == 2
    assert result.errors == []


def test_fetch_requires_ncbi_email(settings: Settings) -> None:
    no_email = settings.model_copy(update={"ncbi_email": None})
    with (
        make_client(no_email, httpx.MockTransport(lambda r: httpx.Response(500))) as client,
        pytest.raises(MissingNcbiEmailError, match="NCBI_EMAIL"),
    ):
        fetch_pmc(client, RawCache(no_email.data_dir, "pubmed"), no_email, ("X",), 1)


def test_search_query_filters_open_access_cc_licenses() -> None:
    query = search_query("NIACINAMIDE")
    assert query.startswith('"niacinamide"[tiab] AND skin AND ')
    assert '"cc by license"[filter]' in query and '"cc0 license"[filter]' in query
    json.dumps(query)  # plain string, safe to log


def test_failed_article_fetch_is_recorded_not_fatal(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["1", "2"]}})
        if request.url.params["id"] == "1":
            return httpx.Response(400)
        return httpx.Response(200, content=ARTICLES_XML)

    fast = settings.model_copy(update={"http_max_retries": 0})
    with make_client(fast, httpx.MockTransport(handler)) as client:
        result = fetch_pmc(client, RawCache(fast.data_dir, "pubmed"), fast, ("X",), 2)

    assert len(result.errors) == 1 and result.errors[0].startswith("PMC1: HTTPStatusError")
    assert {a.source_id for a in result.articles} == {"PMC1000001", "PMC1000003"}
    assert not list((fast.data_dir / "pubmed").glob("*/PMC1.xml"))  # failures aren't cached
