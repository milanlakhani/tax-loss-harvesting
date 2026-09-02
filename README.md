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
| Backend (FastAPI + agents only) | http://localhost:8000 |
| Health | http://localhost:8000/health |
| Readiness (Postgres + MCP) | http://localhost:8000/health/ready |
| MCP Streamable HTTP (Compose service `mcp`, not published to the host) | `http://mcp:8001/mcp` inside the Compose network |
| Streamlit UI (separate container) | http://localhost:8501 |
| PostgreSQL | localhost:5432 user/password/db `finance` |

The **mcp** container is the FastMCP Streamable HTTP server (`0.0.0.0:8001`, path `/mcp`). The backend
does not mount `/mcp`. Agents in the backend are the MCP clients and use
`MCP_SERVER_URL=http://mcp:8001/mcp`. Streamlit and the browser never call MCP. Port 8001 is not
published to the host by default. For local inspection only:

```bash
docker compose -f docker-compose.yml -f docker-compose.debug-mcp.yml up --build
```

that overlay binds `127.0.0.1:8001` only.

The backend container runs `alembic upgrade head` then Uvicorn. MCP, Streamlit, and PostgreSQL are
separate Compose services. Backend liveness is `GET /health` (MCP down does not restart the API in a
loop). `GET /health/ready` reports MCP unavailability with HTTP 503.

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
uvicorn app.mcp.asgi:app --host 127.0.0.1 --port 8001
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
5. Upload bank and brokerage statement PDFs (a folder or multiple files), ask statement questions, review anomalies, drift, and **approved vs
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

## Current-demo presentation data

Historical 2024 fixtures remain the regression default (`DEMO_MODE=historical`). For a
presentation against today's book, generate or refresh the current-demo dataset. The command is
idempotent: it replaces previously generated current/historical demo statements and does not
touch uploaded records (`demo_dataset` IS NULL).

```bash
python -m app.jobs.seed --mode current --as-of today
```

Set `DEMO_MODE=current` so analysis uses the persisted seed as-of (the same date as the generated
statements). Generated current-demo statements are stored with `is_synthetic=true` and
`demo_dataset=current`. That explicit marker—not filename, user ID, or `APP_ENV`—is what allows
`DEMO_STATEMENT_MAX_AGE_DAYS` (default 20) to accept statement period-ends slightly behind as-of.
Uploaded statements keep the conservative freshness policy. The 20-day allowance does not shorten
the wash-sale window or skip incomplete-history checks.

Before a demonstration, run the presentation-readiness check against the same database:

```bash
python -m app.jobs.presentation_check
```

Exit code 0 means current-demo statements exist, match the analysis as-of, are within the age
allowance, cover the wash-sale window, and are not mixed with historical 2024 fixtures.

Alpaca EQUITY/ETF last-session prices remain valid after the close, overnight, on weekends, and
on market holidays. A paper order submitted while the cash session is closed may show status
`QUEUED` until the next eligible session. Live trading is not enabled.

## Data-source ownership

| Data | Owner |
| --- | --- |
| Tax lots, evaluations, candidate status | PostgreSQL application services |
| EQUITY / ETF current quotes | Alpaca Market Data (`iex` by default; `sip`, `delayed_sip`, or `otc` if configured) |
| EQUITY / ETF historical price windows | Alpha Vantage (returns, volatility, drawdown, rapid-decline) |
| CRYPTO current and historical prices | CoinGecko (explicit IDs, e.g. BTC/USD → bitcoin) |
| FX conversion | Frankfurter (no API key; weekend uses last earlier valid date) |
| Paper holdings, quantities, tradability, orders, fills | Alpaca Trading (`TradingClient(..., paper=True)` always) |
| Rolling windows | `RollingWindowStore`: PostgreSQL when `APP_ENV=local`; DynamoDB when `APP_ENV=aws` |

Current EQUITY/ETF quotes come from Alpaca Market Data, not Alpha Vantage. That quote is stored
with provider, feed, source timestamp, retrieval timestamp, and freshness. An Alpaca **fill**
(`fill_price` on `paper_orders`) is a separate record from the Alpaca **market quote** used as
the evaluation/reference price. Missing current quotes fail closed: the application does not
substitute a fill, a position average, or an Alpha Vantage historical close.

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
python -m app.verify_integrations --provider alpaca-market-data
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

## AWS deployment

See [infrastructure/README.md](infrastructure/README.md). `ALLOWED_IPV4_CIDR` is required for CDK
only; local Compose does not use it. Inbound WhatsApp is disabled on AWS because the ALB stays
CIDR-restricted.

## Layout

See `ARCHITECTURE.md` and `REQUIREMENTS_MATRIX.md`.
