# Tax-loss harvesting demonstration — Phase 2

Deterministic local core (Phase 1) plus live provider adapters, FastMCP, OpenAI Agents SDK
orchestration, Streamlit, Alpaca paper synchronization, and guarded paper-order execution.

For the optional read-only WhatsApp demonstration, see [the no-extra-cost setup guide](docs/whatsapp-no-cost-setup.md).

Financial logic lives only in application services. Agents, MCP handlers, API routes, Streamlit,
and provider adapters do not reimplement harvesting, wash-sale, risk, or evaluation rules.

## Tested versions

- Python **3.12.13**
- Docker **29.6.2**
- Docker Compose **v5.3.1**
- PostgreSQL **16.6** (Compose image `postgres:16.6-alpine`)
- fastapi==0.115.8, pydantic==2.11.7, httpx==0.28.1, openai-agents==0.2.11, fastmcp==2.11.3, streamlit==1.42.2, alpaca-py==0.40.1

## Startup

```bash
cp .env.example .env
docker compose up --build
```

| Surface | URL |
| --- | --- |
| Backend (FastAPI + agents) | http://localhost:8000 |
| Health | http://localhost:8000/health |
| Readiness | http://localhost:8000/health/ready |
| MCP Streamable HTTP (dedicated container) | http://localhost:8001/ |
| Streamlit UI (separate container) | http://localhost:8501 |
| PostgreSQL | localhost:5432 user/password/db `finance` |

The backend container runs `alembic upgrade head` then Uvicorn. Streamlit is a **separate** Compose
service. PostgreSQL is a third service.

On the host, point `DATABASE_URL` at `localhost` rather than the Compose hostname `postgres`:

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
streamlit run app/ui/streamlit_app.py --server.port 8501
```

## Local demonstration workflow

1. `docker compose up --build` (or local uvicorn + streamlit against Compose Postgres).
2. `GET /health` returns `{"status":"ok","phase":"2"}`.
3. `python -m app.jobs.seed` then `python -m app.jobs.run_analysis --all-users`.
4. Open http://localhost:8501. Streamlit creates an unguessable **server-bound demo session** in
   Streamlit session state. This is **not authentication**. AWS deployments additionally restrict
   ALB access to an operator-supplied IP CIDR.
5. Upload bank statements, ask statement questions, review anomalies, drift, and **approved vs
   rejected** candidates in separate sections.
6. Prepare an approved candidate. Read the server-generated snapshot (account, SELL, asset, type,
   quantity, provider reference price/timestamp, proceeds, basis, estimated loss, rule results,
   `SIMULATED PAPER TRADE - NO REAL MONEY`).
7. Check the review box, then **Confirm paper sale**. The UI sends only candidate ID, the single-use
   token, and the demo-session binding. After confirm, refresh status on demand
   (`POST /api/paper-orders/{order_id}/refresh`). There is no scheduler or hidden polling worker.
8. `pytest`

`ENABLE_PAPER_ORDERS=false` (default): preparation can be displayed; confirmation never calls Alpaca.

Replacement BUY suggestions are display-only and are never submitted.

## Data-source ownership

| Data | Owner |
| --- | --- |
| Tax lots, evaluations, candidate status | PostgreSQL application services |
| EQUITY / ETF quotes and history | Alpha Vantage |
| CRYPTO quotes and history | CoinGecko (explicit IDs, e.g. BTC/USD → bitcoin) |
| FX conversion | Frankfurter (no API key; weekend uses last earlier valid date) |
| Paper holdings, tradability, orders, fills | Alpaca paper API (`TradingClient(..., paper=True)` always) |
| Rolling windows | `RollingWindowStore`: PostgreSQL when `APP_ENV=local`; DynamoDB when `APP_ENV=aws` |

Alpaca never replaces Alpha Vantage, CoinGecko, or Frankfurter for reference prices. Fill prices
from Alpaca are stored separately from evaluation reference quotes.

Paper-account seed purchases are recorded as `PAPER_MIRROR_SETUP` and are never imported into the
synthetic tax ledger.

Conversation memory is **not** source of truth. Balances, transactions, holdings, tax lots, quotes,
evaluations, and candidates are retrieved through MCP tools / services on every authoritative query.

## Demo-session binding

Streamlit stores a random demo-session ID in **server-side** session state. The backend hashes it
and binds it to an order preparation. Confirmation must present the same binding. This prevents
accidental cross-session confirmation. It is not user authentication.

Orchestrator conversation sessions are a separate table (`agent_conversation_sessions`). At most one
ACTIVE session exists per `(user_id, demo_session_id)`. Reset closes the previous row (audit kept)
and starts a new session. A guessed session ID that does not belong to the current user and demo
session returns a generic "Session not found" without revealing whether another session exists.

SDK session mapping: `AgentConversationSession.sdk_session_id` is the OpenAI Agents SDK session
identifier. Conversation items are stored as `{role, content}` rows (`agent_conversation_items`),
capped at 200 items. Credentials, secrets, and PDF bytes are not stored.

## Provider verification (credential-dependent, not part of pytest)

```bash
python -m app.verify_integrations --provider alpha-vantage
python -m app.verify_integrations --provider coingecko
python -m app.verify_integrations --provider frankfurter
python -m app.verify_integrations --provider alpaca --account conservative-demo
python -m app.verify_integrations --all
```

Automated tests mock every HTTP response and SDK call.

## Alpaca paper mirror setup

Run `python -m app.seed_alpaca_paper --portfolio ALIAS --manifest PATH` to preview the
deterministic BUY plan without submitting orders. After review, temporarily enable paper orders
and add `--confirm-paper` to submit. Setup purchases use deterministic client order IDs and are
recorded as `PAPER_MIRROR_SETUP`. Disable paper orders again after the demonstration.

## Configuration

See `.env.example`. Do not commit `.env`. Harvesting target is an educational ranking target, not a
complete tax return. The 30-day crypto repurchase window is project policy, not tax law.

## Layout

See `ARCHITECTURE.md` and `REQUIREMENTS_MATRIX.md`.
