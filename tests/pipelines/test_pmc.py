from collections.abc import Callable
from datetime import date

import httpx
import pytest

from app.config import Settings
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.sources.pmc import (
    LICENSE_FILTER,
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


def _esearch_handler(
    esearch_ids: dict[str, list[str]], requests: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("esearch.fcgi"):
            seed = request.url.params["term"].split('"')[1]
            return httpx.Response(200, json={"esearchresult": {"idlist": esearch_ids[seed]}})
        pmc_id = request.url.params["id"]
        # Serve a one-article set whose PMCID matches the requested ID.
        xml = ARTICLES_XML.decode().replace("PMC1000001", f"PMC{pmc_id}")
        return httpx.Response(200, content=xml.encode())

    return handler


def test_fetch_identifies_to_ncbi_applies_quota_and_caches(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    esearch_ids = {"niacinamide": ["11", "12", "13"], "retinol": ["13", "14"]}
    seeds = ("NIACINAMIDE", "RETINOL")

    with make_client(
        settings, httpx.MockTransport(_esearch_handler(esearch_ids, requests))
    ) as client:
        result = fetch_pmc(client, RawCache(settings.data_dir, "pubmed"), settings, seeds, 2)
        again = fetch_pmc(client, RawCache(settings.data_dir, "pubmed"), settings, seeds, 2)

    for request in requests:
        assert request.url.params["tool"] == "skinsync"
        assert request.url.params["email"] == "test@example.com"
        assert request.url.params["api_key"] == "k"
    esearch = [r for r in requests if r.url.path.endswith("esearch.fcgi")]
    assert {r.url.params["retmax"] for r in esearch} == {"2"}
    efetch_ids = [r.url.params["id"] for r in requests if r.url.path.endswith("efetch.fcgi")]
    # Quota 2 per seed: 11, 12 for niacinamide; 13, 14 for retinol. Each fetched once.
    assert efetch_ids == ["11", "12", "13", "14"]
    assert len(requests) == 6  # the second run is served from cache
    assert result.per_seed == {"NIACINAMIDE": 2, "RETINOL": 2}
    assert again.per_seed == result.per_seed


def test_paper_found_by_two_seeds_is_fetched_once_and_counts_for_both(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    esearch_ids = {"niacinamide": ["21", "22"], "retinol": ["22"]}

    with make_client(
        settings, httpx.MockTransport(_esearch_handler(esearch_ids, requests))
    ) as client:
        result = fetch_pmc(
            client, RawCache(settings.data_dir, "pubmed"), settings, ("NIACINAMIDE", "RETINOL"), 20
        )

    efetch_ids = [r.url.params["id"] for r in requests if r.url.path.endswith("efetch.fcgi")]
    assert efetch_ids == ["21", "22"]
    assert result.per_seed == {"NIACINAMIDE": 2, "RETINOL": 1}


def test_changing_the_query_changes_the_cache_key(settings: Settings) -> None:
    requests: list[httpx.Request] = []
    handler = _esearch_handler({"niacinamide": []}, requests)
    narrower = settings.model_copy(update={"pmc_topic_terms": ("acne",)})

    with make_client(settings, httpx.MockTransport(handler)) as client:
        for s in (settings, settings, narrower):
            fetch_pmc(client, RawCache(s.data_dir, "pubmed"), s, ("NIACINAMIDE",), 20)

    assert len(requests) == 2  # repeat query cached; changed query refetched
    assert len(list(settings.data_dir.glob("pubmed/*/esearch-niacinamide-*.json"))) == 2


def test_fetch_requires_ncbi_email(settings: Settings) -> None:
    no_email = settings.model_copy(update={"ncbi_email": None})
    with (
        make_client(no_email, httpx.MockTransport(lambda r: httpx.Response(500))) as client,
        pytest.raises(MissingNcbiEmailError, match="NCBI_EMAIL"),
    ):
        fetch_pmc(client, RawCache(no_email.data_dir, "pubmed"), no_email, ("X",), 1)


def test_search_query_requires_seed_skin_and_topic_terms_in_title_abstract(
    settings: Settings,
) -> None:
    query = search_query("NIACINAMIDE", settings.pmc_skin_terms, settings.pmc_topic_terms)

    assert query.startswith('"niacinamide"[tiab] AND (skin[tiab] OR cutaneous[tiab]')
    assert "(dermatolog*[tiab] OR cosmetic*[tiab]" in query
    assert '"skin care"[tiab]' in query
    assert " skin AND " not in query  # no unrestricted "skin" anywhere in the text
    assert '"cc by license"[filter]' in query and '"cc0 license"[filter]' in query


def test_error_bodies_with_http_200_are_fetch_errors_not_results(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if "zinc" in request.url.params["term"]:
                return httpx.Response(200, json={"esearchresult": {"ERROR": "Backend failed"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": ["31"]}})
        return httpx.Response(200, content=b"<eFetchResult><ERROR>busy</ERROR></eFetchResult>")

    with make_client(settings, httpx.MockTransport(handler)) as client:
        result = fetch_pmc(
            client,
            RawCache(settings.data_dir, "pubmed"),
            settings.model_copy(update={"http_max_retries": 0}),
            ("ZINC OXIDE", "RETINOL"),
            20,
        )

    assert result.per_seed == {"ZINC OXIDE": 0, "RETINOL": 0}
    assert len(result.errors) == 2
    assert result.errors[0].startswith("esearch ZINC OXIDE: EutilsError: esearch error")
    assert result.errors[1].startswith("PMC31: EutilsError: efetch returned no article: busy")
    cached = {p.name for p in settings.data_dir.glob("pubmed/*/*")}
    assert not any(n.startswith("esearch-zinc") or n == "PMC31.xml" for n in cached)


def test_synonyms_are_ored_in_title_abstract(settings: Settings) -> None:
    query = search_query(
        "CERAMIDE NP",
        settings.pmc_skin_terms,
        settings.pmc_topic_terms,
        settings.pmc_seed_synonyms["CERAMIDE NP"],
    )

    assert query.startswith('("ceramide np"[tiab] OR "ceramide"[tiab] OR "ceramides"[tiab]) AND ')
    assert '"cc by license"[filter]' in query


def test_seeds_without_synonyms_keep_the_exact_previous_query(settings: Settings) -> None:
    # Pinned so that adding synonyms for one seed can't change other seeds' cache keys.
    expected = (
        '"niacinamide"[tiab] AND (skin[tiab] OR cutaneous[tiab] OR dermal[tiab] '
        "OR epidermal[tiab] OR facial[tiab]) AND (dermatolog*[tiab] OR cosmetic*[tiab] "
        'OR cosmeceutical*[tiab] OR topical*[tiab] OR skincare[tiab] OR "skin care"[tiab] '
        "OR acne[tiab] OR photoaging[tiab] OR hyperpigmentation[tiab] OR sunscreen*[tiab]) "
        f"AND {LICENSE_FILTER}"
    )
    assert "NIACINAMIDE" not in settings.pmc_seed_synonyms
    assert (
        search_query("NIACINAMIDE", settings.pmc_skin_terms, settings.pmc_topic_terms) == expected
    )


def test_fetch_uses_configured_synonyms_for_that_seed_only(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def search_only(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"esearchresult": {"idlist": []}})

    with make_client(settings, httpx.MockTransport(search_only)) as client:
        fetch_pmc(
            client, RawCache(settings.data_dir, "pubmed"), settings, ("CERAMIDE NP", "RETINOL"), 20
        )

    terms = [r.url.params["term"] for r in requests]
    assert terms[0].startswith('("ceramide np"[tiab] OR "ceramide"[tiab]')
    assert terms[1].startswith('"retinol"[tiab] AND ')
