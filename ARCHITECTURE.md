# Architecture (Phase 2)

## Layers

- **domain** — enums, errors, result dataclasses. No FastAPI, Streamlit, LLM, or global user state.
- **services** — `run_analysis`, `evaluate_candidate`, ingestion, statistics, anomalies, harvesting,
  conflicts, portfolio math, `PaperExecutionService`, `OrchestratorSessionService`, `DemoSessionService`,
  `AlpacaSyncService`. Only `PaperExecutionService` may prepare or submit an Alpaca paper SELL.
- **persistence** — SQLAlchemy 2 models, async engine, Alembic migrations.
- **providers** — protocols plus fakes and live adapters (`AlphaVantageProvider`, `CoinGeckoProvider`,
  `FrankfurterProvider`, `AlpacaProvider`). Adapters normalize quotes/FX; they do not implement gates.
- **adapters** — statement storage and `RollingWindowStore` (PostgreSQL local, in-memory fake,
  DynamoDB for `APP_ENV=aws` with the same logical keys).
- **parsers** — deterministic PyMuPDF parsers.
- **mcp** — FastMCP Streamable HTTP at `/mcp`. Thin typed wrappers around application services.
- **agents** — Orchestrator, Document Parsing, ML Analysis, and Eval agents call MCP tools.
  The Eval agent cannot substitute LLM opinion for a rule. The Orchestrator only reports persisted
  candidate statuses.
- **api** — FastAPI health, statements, analyses, paper-order prepare/confirm/refresh, demo sessions,
  orchestrator sessions. Browser and agents submit only server-issued IDs and the confirmation token.
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

- EQUITY / ETF → Alpha Vantage
- CRYPTO → CoinGecko (explicit IDs only)
- FX → Frankfurter
- Paper holdings / orders / fills → Alpaca (`paper=True` is a forced constant, never request-derived)

## Orchestrator sessions vs demo sessions

Demo-session binding protects paper-order confirmation. Orchestrator sessions persist SDK conversation
items per `(user_id, demo_session_id)` with at most one ACTIVE row. Remembered tool output is never
treated as financial source of truth.

## Harvesting and paper execution

Hard gates run before ranking and again at prepare/confirm. FX/CURRENCY/CASH/BANK_BALANCE/UNKNOWN are
never sold. Missing CoinGecko mappings, mismatched Alpaca asset classes, stale quotes, insufficient
quantities, reused tokens, and modified snapshots fail closed. Replacement BUYs are suggestions only.
