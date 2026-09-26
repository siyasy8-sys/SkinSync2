# CLAUDE.md — SkinSync 2.0

SkinSync 2.0 is a deployed RAG service that answers skincare ingredient questions with cited evidence.
**Read `SPEC.md` before starting any task.** It is the source of truth for architecture, data model, and milestones.
If a request conflicts with SPEC.md, stop and ask rather than guessing.

## How we work

- Work one milestone (from SPEC.md → Milestones) or one clearly scoped task at a time. Never build ahead.
- Start every task with a short plan: files you will create or change, and how you will test them. Wait for approval on anything touching infra, the database schema, or more than ~5 files.
- Finish every task by running the checks below. Then summarize what you did, what is left, and any decisions I should review.
- Explain non-obvious design choices in the summary (thresholds, index types, chunk sizes). I need to be able to defend them in interviews.
- Keep commits small and focused, with conventional messages (`feat:`, `fix:`, `test:`, `infra:`, `docs:`).

## Stack

Python 3.12 · FastAPI + Pydantic v2 · PostgreSQL 16 + pgvector · SQLAlchemy 2.0 + Alembic · RapidFuzz ·
Apache Airflow · Docker · GKE Autopilot + Cloud SQL · Terraform · GitHub Actions · Streamlit (demo UI)

Do not add new frameworks or services (for example LangChain, LlamaIndex, a separate vector DB, or Redis) without asking first.
The stack is intentionally small.

## Repo layout

```
app/            FastAPI service
  api/          routes (/ask, /analyze-routine, /analyze-image, /ingredients, /health, /ready)
  retrieval/    hybrid search, rerank, query router
  resolution/   entity-resolution cascade
  generation/   prompts, LLM client, citation validator
  db/           SQLAlchemy models, session
pipelines/      Airflow DAGs and ingestion code (DAGs stay thin; logic lives in importable modules)
eval/           eval harness and metrics (gold datasets live in eval/data/ — see below)
migrations/     Alembic migrations
infra/          Terraform (infra/terraform) and Kubernetes/Helm manifests (infra/k8s)
ui/             Streamlit demo
tests/          pytest, mirrors app/ and pipelines/
```

## Commands

```bash
uv sync                               # install dependencies
docker compose up -d db               # local Postgres + pgvector on port 5433 (pgvector/pgvector:pg16 image)
uv run alembic upgrade head           # apply migrations
uv run uvicorn app.main:app --reload  # run API locally
uv run pytest                         # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy app pipelines eval
uv run python -m eval.run --suite smoke   # quick eval on the fixed test index (required from Week 4 on)
```

All of these must pass before a task counts as done. The eval smoke command is required from Week 4 on, once the eval harness exists.

## Code conventions

- Type hints everywhere; mypy strict for `app/`. Pydantic models for every request, response, and LLM structured output.
- Every module gets unit tests. Mock external APIs (LLM, embeddings, PubMed) in tests. No network calls in `pytest`.
- Database changes go through Alembic migrations only. Never edit the schema by hand.
- Keep functions small and pure where possible, especially in `resolution/` and `retrieval/`, so they are easy to test and ablate.
- Make thresholds and model names config values (`app/config.py`, env vars), never literals scattered through code.
- Log with structured JSON. Include a request ID, and log timings for each pipeline stage (resolve, search, rerank, generate).

## RAG rules (non-negotiable)

- Answers use only retrieved context. Every claim must cite a chunk ID, and the validator drops claims with missing or invalid citations.
- When evidence is weak or missing, return "insufficient evidence". Never fill gaps from model knowledge.
- The entity-resolution cascade never guesses. Unresolved mentions go to the review queue.
- Only rows in `interactions` with `reviewed = true` are used at query time.
- Keep the medical scope guard and disclaimer from SPEC.md → Safety intact in every answer path.

## Evaluation

- `eval/data/` holds hand-labeled gold sets (questions, unanswerable questions, ingredient mentions).
  **Do not create, generate, or edit gold labels.** I write them myself. You may write loaders, validators, and metric code.
- Any change to retrieval, resolution, or prompts must include before/after eval numbers in the summary.
- The CI eval gate fails if faithfulness or Recall@8 drops more than 3 points from the stored baseline.

## Data and licensing

- Use only the public sources listed in SPEC.md → Data sources. Record each source's license in `pipelines/SOURCES.md` before ingesting it.
- Store full text only where the license allows (for example, the PMC open-access subset). Otherwise store metadata and a link.
- Pipelines must be idempotent and incremental, and each run writes a row to `ingestion_runs`.

## Security and infra

- Never commit secrets. Local secrets go in `.env` (git-ignored). Cloud secrets go in Secret Manager via Workload Identity.
- **Never run `terraform apply`, `kubectl apply` against the cloud cluster, or anything that creates billable resources.**
  Write the code and show me the `terraform plan` output. I run applies myself.
- Don't store user-uploaded images or routines, and don't log IP addresses.
- This is a personal project on public data only. Don't reference or reproduce any employer code, internal tools, or data.