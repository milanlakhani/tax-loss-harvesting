# Tax-loss harvesting demonstration — Phase 1

Phase 1 is a deterministic local core: domain models, PostgreSQL persistence, PDF parsing,
synthetic demo data, Isolation Forest anomaly scoring, tax-lot harvesting gates, FastAPI health
endpoints, and CLI jobs. Live broker/market APIs, the OpenAI Agents SDK, FastMCP, Streamlit, and
paper-order submission are deferred to Phase 2.

## Tested versions

- Python **3.12.13**
- Docker **29.6.2**
- Docker Compose **v5.3.1**
- PostgreSQL **16.6** (Compose image `postgres:16.6-alpine`)

## Startup

```bash
cp .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000
- Health: http://localhost:8000/health
- Readiness (database): http://localhost:8000/health/ready
- PostgreSQL: `localhost:5432` user/password/db `finance`

The backend container runs `alembic upgrade head` before Uvicorn.

On the host (outside Compose), point `DATABASE_URL` at `localhost` rather than the Compose hostname `postgres`:

```bash
set DATABASE_URL=postgresql+psycopg://finance:finance@localhost:5432/finance
```

### Local virtualenv (Windows)

```bash
uv venv .venv --python 3.12
uv pip install -r requirements.txt
docker compose up -d postgres
alembic upgrade head
python -m app.jobs.seed
python -m app.jobs.run_analysis --all-users
uvicorn app.main:app --reload --port 8000
```

## Phase 1 verification workflow

1. `docker compose up --build` (or local uvicorn against Compose Postgres).
2. `GET /health` returns `{"status":"ok","phase":"1"}`.
3. `GET /health/ready` returns `{"status":"ready"}`.
4. `python -m app.jobs.seed` creates two users, six bank PDFs, two brokerage PDFs, manifests.
5. `python -m app.jobs.run_analysis --user 11111111-1111-4111-8111-111111111111`
6. `python -m app.jobs.run_analysis --all-users`
7. `pytest`

The seed command is a development/test-data tool. It is not exposed on FastAPI.

PDF generation lives under `app/demo_data/` and is not an application feature.

## Configuration

See `.env.example`. Phase 1 uses fake providers only. Do not put live secrets in git.

Harvesting target is the positive combined net realized gain after independent short-term and
long-term subtotals. That is a simplified educational ranking target, not a complete tax return.

The 30-day crypto repurchase window is a conservative project policy, not tax law.

## Layout

See `ARCHITECTURE.md` and `REQUIREMENTS_MATRIX.md`.
