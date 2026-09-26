"""Writes one `ingestion_runs` row per pipeline run, in its own transaction,
so a failed load still leaves a `failed` row behind."""

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IngestionRun


@dataclass
class RunStats:
    counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


@contextmanager
def track_run(session_factory: sessionmaker[Session], dag: str) -> Iterator[RunStats]:
    with session_factory() as session:
        run = IngestionRun(dag=dag, status="running")
        session.add(run)
        session.commit()
        run_id = run.id

    stats = RunStats()
    status = "failed"
    try:
        yield stats
        status = "success"
    except Exception as exc:
        stats.errors.append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        with session_factory() as session:
            row = session.get_one(IngestionRun, run_id)
            row.status = status
            row.finished_at = func.now()
            row.counts = dict(stats.counts)
            row.errors = stats.errors
            session.commit()
