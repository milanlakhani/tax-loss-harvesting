# Architecture (Phase 1)

## Layers

- **domain** — enums, errors, result dataclasses. No FastAPI, Streamlit, LLM, or global user state.
- **services** — `run_analysis`, `evaluate_candidate`, ingestion, statistics, anomalies, harvesting, conflicts, portfolio math.
- **persistence** — SQLAlchemy 2 models, async engine, Alembic migrations.
- **providers** — typed protocols (`EquityQuoteProvider`, `CryptoQuoteProvider`, `FxProvider`, `ExecutionProvider`) and deterministic fakes. Phase 2 adapters must implement the same protocols.
- **adapters** — local statement storage (`local-data/`, gitignored) and `RollingWindowStore` (PostgreSQL local implementation + in-memory fake).
- **parsers** — deterministic PyMuPDF parsers for `SYNTHETIC_BANK_V1` and `SYNTHETIC_BROKERAGE_V1`.
- **api** — FastAPI health/readiness and an analysis POST that calls `run_analysis`.
- **jobs** — CLI wrappers with no financial logic.
- **demo_data** — PDF generator and seed command. Not exposed via API, MCP, or UI.

## Application entry point

```python
async def run_analysis(
    user_id: UUID,
    *,
    trigger: AnalysisTrigger,
    as_of: datetime,
    idempotency_key: str,
) -> AnalysisRunResult
```

`AnalysisTrigger` includes `SCHEDULED` in the domain, but Phase 1 never invokes a scheduler.

FastAPI and `python -m app.jobs.run_analysis` call this function. The service persists the analysis run first, enforces idempotency, locks `(portfolio_id, as_of_period)` while a run is active, isolates per-portfolio work, persists candidates before `evaluate_candidate`, and does not prepare or submit orders.

## Rolling windows

Logical keys:

- `QUOTE#ASSET#CURRENCY`
- `PRICE_WINDOW#ASSET#CURRENCY` (timestamp sort key)
- `ANOMALY_WINDOW#USER#FEATURE` (timestamp + observation id)
- `FX#BASE#QUOTE#DATE`
- `WINDOW_META#WINDOW_KEY`

Queries always apply a cutoff timestamp (DynamoDB TTL is asynchronous in later phases). Metadata advances only after a complete fetch+write.

## Harvesting

Hard gates run before ranking. FX/CURRENCY/CASH/BANK_BALANCE/UNKNOWN are never candidates. Missing basis is rejected, never inferred. Replacement relationships come from a versioned table. Rapid price decline is a warning only.

Conflict identities use a canonical fingerprint (no analysis-run id, clock, or explanation text) with a unique constraint. Repeats are labelled `STILL_ACTIVE`. Expiry resolves the row; history is kept.

## Demo data

Historical 2024 PDFs are regression fixtures. Current-demo PDFs are a separate set generated from `DEMO_AS_OF_DATE`. Parsers print dates as stored; they do not rewrite calendar years. Current Alpaca paper positions are the quantity authority for live/demo harvesting; statement lots are reconciled and never silently merged with unrelated broker holdings.

Isolation Forest is fit per user with a fixed seed. Features are causal (no future leakage) and versioned (`iforest_features_v1`). Labels live in `anomaly_ground_truth` and are never used for fitting. Insufficient history returns `INSUFFICIENT_HISTORY` and a rule-based fallback.

Normalized score = min-max of `-decision_function` so higher means more anomalous. Flagging uses sklearn's contamination offset (`predict == -1`).
