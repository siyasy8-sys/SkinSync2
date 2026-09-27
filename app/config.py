from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables (and .env locally)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_env: str = "local"

    # Ingestion: shared
    seed_coverage_min: int = 10  # seeds below this are reported as LOW
    data_dir: Path = Path("data/raw")
    user_agent: str = "SkinSync/0.1 (+https://github.com/siyasy8-sys/SkinSync2)"
    http_timeout_seconds: float = 60.0
    http_max_retries: int = 4
    http_backoff_seconds: float = 1.0

    # EU CosIng: the search API used by the CosIng web app. The URL and public key are
    # published in https://ec.europa.eu/growth/tools-databases/cosing/assets/env-json-config.json
    cosing_search_url: str = "https://webgate.ec.europa.eu/es/search-api/rest/search"
    cosing_api_key: str = "285a77fd-1257-4271-8507-f0c6b2961203"
    cosing_requests_per_second: float = 1.0
    cosing_page_size: int = 100
    cosing_sample_pages: int = 3
    cosing_inventory_page_size: int = 200  # the API's maximum

    # Open Beauty Facts: official daily dump (API scraping is not allowed)
    obf_dump_url: str = "https://static.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz"
    obf_sample_limit: int = 300
    obf_min_per_seed: int = 10
    obf_prefer_english: bool = True
    obf_category_pattern: str = r"skin|face|facial|moistur|serum|sun|cleanser|lotion|cream"

    # Entity resolution (see SPEC.md -> Entity resolution). Tune against eval/data/er_mentions.csv.
    er_fuzzy_scorer: str = "token_sort_ratio"  # any rapidfuzz.fuzz scorer name
    er_fuzzy_threshold: float = 92.0  # accept at or above this score...
    er_fuzzy_margin: float = 3.0  # ...and at least this far ahead of the next ingredient
    er_review_floor: float = 80.0  # [floor, threshold): queued as below_threshold
    er_candidate_k: int = 20  # trigram candidates fetched per query
    er_queue_candidates: int = 5  # candidates stored with each queued mention

    # NCBI E-utilities (PMC open-access subset)
    ncbi_eutils_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ncbi_email: str | None = None
    ncbi_api_key: str | None = None
    ncbi_tool: str = "skinsync"
    pmc_papers_per_seed: int = 20
    # Extra title/abstract names per seed, used only in the PMC query. Keys are INCI
    # seed names. Synonyms may be broader than the seed (e.g. ceramides as a class).
    pmc_seed_synonyms: dict[str, tuple[str, ...]] = {
        "CERAMIDE NP": ("ceramide np", "ceramide", "ceramides"),
    }
    # A paper qualifies only if its title/abstract mentions the seed, a skin term,
    # and a dermatology/cosmetic term. "skin" anywhere in the text was too loose.
    pmc_skin_terms: tuple[str, ...] = ("skin", "cutaneous", "dermal", "epidermal", "facial")
    pmc_topic_terms: tuple[str, ...] = (
        "dermatolog*",
        "cosmetic*",
        "cosmeceutical*",
        "topical*",
        "skincare",
        '"skin care"',
        "acne",
        "photoaging",
        "hyperpigmentation",
        "sunscreen*",
    )

    @property
    def ncbi_requests_per_second(self) -> float:
        # NCBI allows 3 req/s without an API key and 10 req/s with one. Pacing exactly
        # at the limit still triggers 429s from timing jitter, so stay a little under.
        return 8.0 if self.ncbi_api_key else 2.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
