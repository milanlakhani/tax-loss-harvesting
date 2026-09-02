# Architecture (Phase 2)

## Layers

- **domain** — enums, errors, result dataclasses. No FastAPI, Streamlit, LLM, or global user state.
- **services** — `run_analysis`, `evaluate_candidate`, ingestion, statistics, anomalies, harvesting,
  conflicts, portfolio math, `PaperExecutionService`, `OrchestratorSessionService`, `DemoSessionService`,
  `AlpacaSyncService`. Only `PaperExecutionService` may prepare or submit an Alpaca paper SELL.
- **persistence** — SQLAlchemy 2 models, async engine, Alembic migrations.
- **providers** — protocols plus fakes and live adapters (`AlpacaMarketDataProvider` for current
  EQUITY/ETF quotes, `AlphaVantageProvider` for EQUITY/ETF history, `CoinGeckoProvider`,
  `FrankfurterProvider`, `AlpacaProvider` for trading). Adapters normalize quotes/FX; they do not
  implement gates.
- **adapters** — statement storage (`LocalStatementStorage` for `APP_ENV=local`, `S3StatementStorage`
  for `APP_ENV=aws`) and `RollingWindowStore` (PostgreSQL local, in-memory fake, DynamoDB for
  `APP_ENV=aws` with the same logical keys). DynamoDB TTL is physical cleanup only; queries still
  apply the configured cutoff.
- **parsers** — deterministic PyMuPDF parsers.
- **mcp** — standalone FastMCP Streamable HTTP process (`/mcp` on port 8001). Thin typed wrappers
  around application services. Not mounted on FastAPI. Not reachable from the browser or Streamlit.
- **agents** — Orchestrator, Document Parsing, ML Analysis, and Eval agents in the backend call MCP
  over `MCP_SERVER_URL` (Compose: `http://mcp:8001/mcp`; AWS sidecar: `http://127.0.0.1:8001/mcp`).
  The Eval agent cannot substitute LLM opinion for a rule. The Orchestrator only reports persisted
  candidate statuses. MCP unavailability is fail-closed and cannot skip safety evaluation.
- **api** — FastAPI health, statements, analyses, paper-order prepare/confirm/refresh, demo sessions,
  orchestrator sessions. FastAPI does **not** serve `/mcp`. Browser and agents submit only server-issued IDs and the confirmation token.
- **ui** — Streamlit (separate Compose container). Confirmation requires an unchecked review box and
  a disabled Confirm button until guards pass.
- **jobs** — CLI wrappers with no financial logic.
- **demo_data** — PDF generator and seed command. Not exposed via API, MCP, or UI.

## Application entry point

`run_analysis(user_id, trigger, as_of, idempotency_key)` remains the analysis service used by FastAPI
and the CLI.

`PaperExecutionService.prepare(candidate_id, demo_session_token)` and `.confirm(candidate_id, token,
demo_session_token)` are the only paper-order paths. Confirmation with `ENABLE_PAPER_ORDERS=false`
never calls Alpaca.

## Rolling windows

Logical keys (identical in local PostgreSQL and AWS DynamoDB):

- `QUOTE#ASSET#CURRENCY`
- `PRICE_WINDOW#ASSET#CURRENCY` (timestamp sort key)
- `ANOMALY_WINDOW#USER#FEATURE` (timestamp + stable observation id)
- `FX#BASE#QUOTE#DATE`
- `WINDOW_META#WINDOW_KEY` (last-successful timestamp, bounds, `schema_version=window_v1`)

`WindowSyncService` fetches only history after the saved timestamp minus configured overlap.
Metadata does not advance on a failed or partial refresh. Analysis consumes stored windows.

## Provider routing

- EQUITY / ETF **current quotes** → Alpaca Market Data (`alpaca-market-data`, feed `iex` by default;
  configurable `sip` / `delayed_sip` / `otc`). Stored with provider, feed, source timestamp,
  retrieval timestamp, and freshness.
- EQUITY / ETF **historical windows** → Alpha Vantage (`TIME_SERIES_DAILY` closes). Used for
  returns, volatility, drawdown, and rapid-decline analysis. Never used as a substitute current quote.
- CRYPTO current and historical prices → CoinGecko (explicit IDs only; API key mandatory in AWS)
- FX → Frankfurter (no API key)
- Paper holdings, quantities, orders, and **fills** → Alpaca Trading (`paper=True` is a forced
  constant). Fill prices are stored on `paper_orders.fill_price`, separately from the Alpaca
  market-data quote used as `reference_price` / `quote_provider`.

A missing current quote fails closed (`UNAVAILABLE_QUOTE` / `STALE_QUOTE`). The application does
not substitute an order fill, a position average price, or an Alpha Vantage historical close.
Alpaca EQUITY/ETF quotes use Alpaca's market clock and calendar: during an open session the
configured intraday freshness limit applies; when the market is closed, the latest trade from
the most recently completed session is accepted and recorded as informational
`MARKET_CLOSED_USING_LAST_PRICE` context, not as a candidate rejection. Paper orders submitted
outside eligible trading hours may be persisted as `QUEUED`.

## AWS demo (Chapter 6)

See `infrastructure/README.md`. CDK provisions a CIDR-restricted ALB, one public-IP Fargate task
with FastAPI, FastMCP, and Streamlit containers (no NAT), isolated RDS, S3, DynamoDB, and Secrets
Manager. MCP listens on task-local `127.0.0.1:8001` and has no ALB listener, target group, or
security-group ingress. `APP_ENV=aws` selects those adapters. `ALLOWED_IPV4_CIDR` is not used locally.

Inbound WhatsApp is disabled on AWS: Meta cannot call a CIDR-restricted ALB, and the ALB must not
be opened to `0.0.0.0/0`. The application WhatsApp path remains read-only.

The public task IP is a cost-saving demo choice, not a production recommendation. Task egress is
PostgreSQL to RDS, HTTPS to providers/AWS APIs/ECR, and DNS — not “ECS-to-RDS only”.

## Orchestrator sessions vs demo sessions

Demo-session binding protects paper-order confirmation. Orchestrator sessions persist SDK conversation
items per `(user_id, demo_session_id)` with at most one ACTIVE row. Remembered tool output is never
treated as financial source of truth.

## Harvesting and paper execution

Hard gates run before ranking and again at prepare/confirm. FX/CURRENCY/CASH/BANK_BALANCE/UNKNOWN are
never sold. Missing CoinGecko mappings, mismatched Alpaca asset classes, stale quotes, insufficient
quantities, reused tokens, and modified snapshots fail closed. Replacement BUYs are suggestions only.
