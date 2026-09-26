from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables (and .env locally)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_env: str = "local"

    # Ingestion: shared
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

    # Open Beauty Facts: official daily dump (API scraping is not allowed)
    obf_dump_url: str = "https://static.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz"
    obf_sample_limit: int = 300
    obf_category_pattern: str = r"skin|face|facial|moistur|serum|sun|cleanser|lotion|cream"

    # NCBI E-utilities (PMC open-access subset)
    ncbi_eutils_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ncbi_email: str | None = None
    ncbi_api_key: str | None = None
    ncbi_tool: str = "skinsync"
    pmc_sample_limit: int = 50
    pmc_hits_per_seed: int = 10

    @property
    def ncbi_requests_per_second(self) -> float:
        # NCBI allows 3 req/s without an API key and 10 req/s with one. Pacing exactly
        # at the limit still triggers 429s from timing jitter, so stay a little under.
        return 8.0 if self.ncbi_api_key else 2.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
