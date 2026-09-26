import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.main import create_app

FIXTURES = Path(__file__).parent / "pipelines" / "fixtures"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated settings: no .env, cache in tmp_path, no real rate limiting."""
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused",
        data_dir=tmp_path / "raw",
        cosing_requests_per_second=1000,
        cosing_sample_pages=1,
        ncbi_email="test@example.com",
        ncbi_api_key="k",
    )


def _unavailable(reason: str) -> None:
    # CI sets REQUIRE_DB=1 so DB tests fail loudly instead of silently skipping.
    if os.environ.get("REQUIRE_DB") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture
def db_session_factory() -> Iterator[sessionmaker[Session]]:
    """Sessions bound to one connection whose outer transaction is rolled back.

    Code under test can call commit(); with create_savepoint those commits only
    release savepoints, so nothing persists after the test.
    """
    try:
        engine = create_engine(get_settings().database_url)
        has_tables = inspect(engine).has_table("ingredients")
    except (ValidationError, OperationalError) as exc:
        _unavailable(f"database unavailable: {exc.__class__.__name__}")
    if not has_tables:
        engine.dispose()
        _unavailable("tables missing; run `uv run alembic upgrade head`")
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
