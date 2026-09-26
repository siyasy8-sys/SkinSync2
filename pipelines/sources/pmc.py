"""PubMed Central open-access articles (CC BY / CC0 only) via NCBI E-utilities.

Week 1 stores metadata and the abstract. The full JATS XML is cached so a
later milestone can fill documents.full_text without calling NCBI again.
See pipelines/SOURCES.md.
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from functools import partial
from itertools import zip_longest

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Document
from pipelines.cache import RawCache
from pipelines.http import RateLimiter
from pipelines.upsert import upsert

SOURCE = "pmc"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
ALI_LICENSE_REF = "{http://www.niso.org/schemas/ali/1.0/}license_ref"
LICENSE_FILTER = '"open access"[filter] AND ("cc by license"[filter] OR "cc0 license"[filter])'
# Conservative: accept only plain CC BY (any version) and CC0. Anything else
# (BY-NC, BY-ND, BY-SA, publisher licenses, missing license) is rejected.
CC_BY_RE = re.compile(r"creativecommons\.org/licenses/by/(\d\.\d)", re.IGNORECASE)
CC0_RE = re.compile(r"creativecommons\.org/publicdomain/zero/(\d\.\d)", re.IGNORECASE)
SKIPPED_ABSTRACT_TYPES = {"graphical", "teaser", "author-highlights", "short", "summary"}


class MissingNcbiEmailError(RuntimeError):
    pass


class PmcArticle(BaseModel):
    source: str = SOURCE
    source_id: str  # "PMC1234567"
    pmid: str | None
    title: str
    url: str
    published_at: date | None
    license: str
    abstract: str | None
    full_text: str | None = None  # filled in a later milestone


def normalize_license(license_urls: list[str]) -> str | None:
    """'CC BY 4.0' or 'CC0 1.0' if any URL is an accepted license, else None."""
    for url in license_urls:
        if m := CC_BY_RE.search(url):
            return f"CC BY {m.group(1)}"
        if m := CC0_RE.search(url):
            return f"CC0 {m.group(1)}"
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _text(el: ET.Element | None) -> str:
    return _clean("".join(el.itertext())) if el is not None else ""


def _abstract(meta: ET.Element) -> str | None:
    for abstract in meta.findall("abstract"):
        if abstract.get("abstract-type") in SKIPPED_ABSTRACT_TYPES:
            continue
        sections = abstract.findall("sec")
        if sections:
            parts = []
            for sec in sections:
                heading = _text(sec.find("title"))
                body = " ".join(_text(p) for p in sec.findall(".//p"))
                parts.append(f"{heading}: {body}" if heading else body)
            text = "\n".join(p for p in parts if p)
        else:
            text = " ".join(_text(p) for p in abstract.findall(".//p")) or _text(abstract)
        if text:
            return text
    return None


def _published(meta: ET.Element) -> date | None:
    candidates = meta.findall("pub-date")
    # Prefer the electronic publication date; fall back to the first with a year.
    candidates.sort(key=lambda d: d.get("pub-type") != "epub" and d.get("date-type") != "pub")
    for pub in candidates:
        year = _text(pub.find("year"))
        if year.isdigit():
            month = _text(pub.find("month"))
            day = _text(pub.find("day"))
            try:
                return date(
                    int(year),
                    int(month) if month.isdigit() else 1,
                    int(day) if day.isdigit() else 1,
                )
            except ValueError:
                return date(int(year), 1, 1)
    return None


def parse_article(article: ET.Element) -> PmcArticle | None:
    """None if the article lacks an ID or title, or isn't CC BY / CC0."""
    meta = article.find("front/article-meta")
    if meta is None:
        return None
    ids = {el.get("pub-id-type"): _text(el) for el in meta.findall("article-id")}
    pmc = ids.get("pmcid") or ids.get("pmc") or ids.get("pmcaid") or ""
    pmcid = pmc if pmc.upper().startswith("PMC") else f"PMC{pmc}" if pmc else ""
    title = _text(meta.find("title-group/article-title"))
    license_urls = [
        url
        for lic in meta.findall("permissions/license")
        for url in [lic.get(XLINK_HREF, ""), *(_text(r) for r in lic.iter(ALI_LICENSE_REF))]
        if url
    ]
    license_name = normalize_license(license_urls)
    if not (pmcid and title and license_name):
        return None
    return PmcArticle(
        source_id=pmcid.upper(),
        pmid=ids.get("pmid") or None,
        title=title,
        url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid.upper()}/",
        published_at=_published(meta),
        license=license_name,
        abstract=_abstract(meta),
    )


def parse_efetch(payload: bytes) -> list[PmcArticle | None]:
    """One entry per <article> in an efetch response; None marks a rejected article."""
    root = ET.fromstring(payload)  # trusted source; expat guards against entity expansion
    articles = [root] if root.tag == "article" else root.findall("article")
    return [parse_article(a) for a in articles]


def _eutils_params(settings: Settings) -> dict[str, str]:
    if not settings.ncbi_email:
        raise MissingNcbiEmailError(
            "NCBI_EMAIL is not set. NCBI asks E-utilities callers to identify themselves; "
            "add NCBI_EMAIL=<your email> to .env."
        )
    params = {"tool": settings.ncbi_tool, "email": settings.ncbi_email}
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key
    return params


def _get(
    client: httpx.Client, limiter: RateLimiter, url: str, params: dict[str, str | int]
) -> bytes:
    limiter.wait()
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.content


def search_query(seed: str) -> str:
    return f'"{seed.lower()}"[tiab] AND skin AND {LICENSE_FILTER}'


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch_pmc(
    client: httpx.Client,
    cache: RawCache,
    settings: Settings,
    seeds: tuple[str, ...],
    limit: int,
) -> tuple[list[PmcArticle], int]:
    """Returns (accepted articles, number rejected by the license re-check).

    IDs are picked round-robin across seeds so every seed is represented.
    """
    base = _eutils_params(settings)
    limiter = RateLimiter(settings.ncbi_requests_per_second)
    esearch = f"{settings.ncbi_eutils_url}/esearch.fcgi"
    efetch = f"{settings.ncbi_eutils_url}/efetch.fcgi"

    per_seed: list[list[str]] = []
    for seed in seeds:
        params: dict[str, str | int] = {
            **base,
            "db": "pmc",
            "term": search_query(seed),
            "retmax": settings.pmc_hits_per_seed,
            "retmode": "json",
            "sort": "relevance",
        }
        payload = cache.fetch(
            f"esearch-{_slug(seed)}.json", partial(_get, client, limiter, esearch, params)
        )
        per_seed.append(json.loads(payload)["esearchresult"].get("idlist", []))

    ids: list[str] = []
    for round_ in zip_longest(*per_seed):
        for pmc_id in round_:
            if pmc_id and pmc_id not in ids and len(ids) < limit:
                ids.append(pmc_id)

    articles: dict[str, PmcArticle] = {}
    rejected = 0
    for pmc_id in ids:
        params = {**base, "db": "pmc", "id": pmc_id, "retmode": "xml"}
        payload = cache.fetch(f"PMC{pmc_id}.xml", partial(_get, client, limiter, efetch, params))
        for article in parse_efetch(payload):
            if article is None:
                rejected += 1
            else:
                articles.setdefault(article.source_id, article)
    return list(articles.values()), rejected


def load_documents(session: Session, records: list[PmcArticle]) -> tuple[int, int]:
    return upsert(session, Document, [r.model_dump() for r in records], ["source", "source_id"])
