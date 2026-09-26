# SkinSync 2.0

A retrieval-augmented generation (RAG) service that answers skincare ingredient questions with cited evidence. See [SPEC.md](SPEC.md) for the design.

> Informational only, not medical advice.

## Quickstart

Prerequisites: [uv](https://docs.astral.sh/uv/) and Docker (Docker Desktop or OrbStack).

```bash
cp .env.example .env
uv sync
docker compose up -d db                # Postgres 16 + pgvector on localhost:5433
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
curl localhost:8000/health             # {"status":"ok"}
```

## Checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app pipelines eval
uv run pre-commit install              # run ruff on every commit
```

## License

MIT
