"""Week 1 sample loaders.

    uv run python -m pipelines.ingest {cosing,obf,pmc,all} [--limit N] [--refresh] [--prune]

Each source runs as one tracked run (an `ingestion_runs` row) and commits its
rows only if the whole load succeeds. Raw responses come from data/raw/ when
cached; --refresh starts a new snapshot.
"""

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Document, Product
from app.db.session import get_sessionmaker
from pipelines.cache import RawCache
from pipelines.http import make_client
from pipelines.runs import RunStats, track_run
from pipelines.seeds import SEED_ACTIVES
from pipelines.sources import cosing, obf, pmc
from pipelines.upsert import prune

SEED_PREFIX = "seed:"


@dataclass(frozen=True)
class RunOptions:
    limit: int | None = None
    refresh: bool = False
    prune: bool = False


Loader = Callable[[Session, Settings, RunStats, RunOptions], None]


def _record(stats: RunStats, cache: RawCache, fetched: int, inserted: int, updated: int) -> None:
    stats.counts.update(
        fetched=fetched,
        inserted=inserted,
        updated=updated,
        cache_hits=cache.hits,
        network_requests=cache.misses,
    )


def _record_seeds(stats: RunStats, per_seed: dict[str, int], minimum: int, what: str) -> None:
    """Per-seed coverage goes into counts; seeds below `minimum` are flagged (non-fatal)."""
    for seed, n in per_seed.items():
        stats.counts[f"{SEED_PREFIX}{seed}"] = n
        if n < minimum:
            stats.errors.append(f"LOW coverage: {seed} has {n} {what} (< {minimum})")


def format_seed_report(counts: Mapping[str, int], minimum: int) -> str:
    rows = [
        (k.removeprefix(SEED_PREFIX), n) for k, n in counts.items() if k.startswith(SEED_PREFIX)
    ]
    width = max((len(seed) for seed, _ in rows), default=0)
    return "\n".join(
        f"  {seed:<{width}}  {n:>4}{'  LOW' if n < minimum else ''}" for seed, n in rows
    )


def _maybe_prune(
    session: Session,
    stats: RunStats,
    opts: RunOptions,
    model: type[Product] | type[Document],
    source: str,
    keep: set[str],
) -> None:
    """With --prune, delete this source's rows that aren't in the new sample.

    Skipped if any fetch failed: a partial sample must never delete good rows.
    Runs in the same transaction as the upsert, so both commit or neither does.
    """
    if not opts.prune:
        return
    if stats.counts["fetch_errors"] or not keep:
        stats.errors.append("prune skipped: the fetch was incomplete")
        return
    stats.counts["pruned"] = prune(session, model, source, keep)


def run_cosing(session: Session, settings: Settings, stats: RunStats, opts: RunOptions) -> None:
    if opts.prune:
        stats.errors.append("prune skipped: not supported for cosing (it isn't resampled)")
    cache = RawCache(settings.data_dir, "cosing", refresh=opts.refresh)
    with make_client(settings) as client:
        records = cosing.fetch_cosing(client, cache, settings, SEED_ACTIVES)
    if opts.limit is not None:
        records = records[: opts.limit]
    _record(stats, cache, len(records), *cosing.load_ingredients(session, records))


def run_obf(session: Session, settings: Settings, stats: RunStats, opts: RunOptions) -> None:
    cache = RawCache(settings.data_dir, "obf", refresh=opts.refresh)
    limit = opts.limit or settings.obf_sample_limit
    with make_client(settings) as client:
        result = obf.fetch_obf(
            client, cache, settings.model_copy(update={"obf_sample_limit": limit}), SEED_ACTIVES
        )
    stats.counts["english_names"] = result.english
    _record_seeds(stats, result.per_seed, settings.seed_coverage_min, "products")
    _record(stats, cache, len(result.products), *obf.load_products(session, result.products))
    keep = {p.source_id for p in result.products}
    _maybe_prune(session, stats, opts, Product, obf.SOURCE, keep)


def run_pmc(session: Session, settings: Settings, stats: RunStats, opts: RunOptions) -> None:
    cache = RawCache(settings.data_dir, "pubmed", refresh=opts.refresh)
    with make_client(settings) as client:
        result = pmc.fetch_pmc(
            client, cache, settings, SEED_ACTIVES, opts.limit or settings.pmc_papers_per_seed
        )
    stats.counts["rejected_license"] = result.rejected_license
    stats.counts["fetch_errors"] = len(result.errors)
    stats.errors.extend(result.errors)
    _record_seeds(stats, result.per_seed, settings.seed_coverage_min, "papers")
    _record(stats, cache, len(result.articles), *pmc.load_documents(session, result.articles))
    keep = {a.source_id for a in result.articles}
    _maybe_prune(session, stats, opts, Document, pmc.SOURCE, keep)


# dag names match the Airflow DAGs in SPEC.md, so Week 5 can reuse them.
SOURCES: dict[str, tuple[str, Loader]] = {
    "cosing": ("ingest_ingredients", run_cosing),
    "obf": ("ingest_products", run_obf),
    "pmc": ("ingest_papers", run_pmc),
}


def run_source(
    session_factory: sessionmaker[Session], settings: Settings, name: str, opts: RunOptions
) -> RunStats:
    dag, loader = SOURCES[name]
    with track_run(session_factory, dag) as stats, session_factory() as session:
        loader(session, settings, stats, opts)
        session.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("source", choices=[*SOURCES, "all"])
    parser.add_argument(
        "--limit", type=int, default=None, help="max records (for pmc: papers per seed active)"
    )
    parser.add_argument("--refresh", action="store_true", help="ignore cache; new snapshot")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete this source's rows that aren't in the new sample (obf, pmc)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    session_factory = get_sessionmaker()
    names = list(SOURCES) if args.source == "all" else [args.source]
    for name in names:
        opts = RunOptions(limit=args.limit, refresh=args.refresh, prune=args.prune)
        stats = run_source(session_factory, settings, name, opts)
        totals = {k: v for k, v in stats.counts.items() if not k.startswith(SEED_PREFIX)}
        print(json.dumps({"source": name, **totals}))
        if report := format_seed_report(stats.counts, settings.seed_coverage_min):
            print(f"per-active coverage (LOW = under {settings.seed_coverage_min}):\n{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
