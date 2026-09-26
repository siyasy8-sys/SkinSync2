"""Week 1 sample loaders.

    uv run python -m pipelines.ingest {cosing,obf,pmc,all} [--limit N] [--refresh]

Each source runs as one tracked run (an `ingestion_runs` row) and commits its
rows only if the whole load succeeds. Raw responses come from data/raw/ when
cached; --refresh starts a new snapshot.
"""

import argparse
import json
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.session import get_sessionmaker
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.runs import RunStats, track_run
from pipelines.seeds import SEED_ACTIVES
from pipelines.sources import cosing, obf, pmc

Loader = Callable[[Session, Settings, RunStats, int | None, bool], None]


def _record(stats: RunStats, cache: RawCache, fetched: int, inserted: int, updated: int) -> None:
    stats.counts.update(
        fetched=fetched,
        inserted=inserted,
        updated=updated,
        cache_hits=cache.hits,
        network_requests=cache.misses,
    )


def run_cosing(
    session: Session, settings: Settings, stats: RunStats, limit: int | None, refresh: bool
) -> None:
    cache = RawCache(settings.data_dir, "cosing", refresh=refresh)
    with make_client(settings) as client:
        records = cosing.fetch_cosing(client, cache, settings, SEED_ACTIVES)
    if limit is not None:
        records = records[:limit]
    _record(stats, cache, len(records), *cosing.load_ingredients(session, records))


def run_obf(
    session: Session, settings: Settings, stats: RunStats, limit: int | None, refresh: bool
) -> None:
    cache = RawCache(settings.data_dir, "obf", refresh=refresh)
    with make_client(settings) as client:
        records = obf.fetch_obf(client, cache, settings, limit or settings.obf_sample_limit)
    _record(stats, cache, len(records), *obf.load_products(session, records))


def run_pmc(
    session: Session, settings: Settings, stats: RunStats, limit: int | None, refresh: bool
) -> None:
    cache = RawCache(settings.data_dir, "pubmed", refresh=refresh)
    with make_client(settings) as client:
        records, rejected = pmc.fetch_pmc(
            client, cache, settings, SEED_ACTIVES, limit or settings.pmc_sample_limit
        )
    stats.counts["rejected_license"] = rejected
    _record(stats, cache, len(records), *pmc.load_documents(session, records))


# dag names match the Airflow DAGs in SPEC.md, so Week 5 can reuse them.
SOURCES: dict[str, tuple[str, Loader]] = {
    "cosing": ("ingest_ingredients", run_cosing),
    "obf": ("ingest_products", run_obf),
    "pmc": ("ingest_papers", run_pmc),
}


def run_source(
    session_factory: sessionmaker[Session],
    settings: Settings,
    name: str,
    limit: int | None,
    refresh: bool,
) -> RunStats:
    dag, loader = SOURCES[name]
    with track_run(session_factory, dag) as stats, session_factory() as session:
        loader(session, settings, stats, limit, refresh)
        session.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("source", choices=[*SOURCES, "all"])
    parser.add_argument("--limit", type=int, default=None, help="max records per source")
    parser.add_argument("--refresh", action="store_true", help="ignore cache; new snapshot")
    args = parser.parse_args(argv)

    settings = get_settings()
    session_factory = get_sessionmaker()
    names = list(SOURCES) if args.source == "all" else [args.source]
    for name in names:
        stats = run_source(session_factory, settings, name, args.limit, args.refresh)
        print(json.dumps({"source": name, **stats.counts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
