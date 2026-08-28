# Requirements matrix (Phase 1)

Status is complete only when the behavior is implemented and covered by tests, not stubs.

| Requirement | Implementation | Tests |
| --- | --- | --- |
| Python 3.12, FastAPI, Uvicorn, PostgreSQL, SQLAlchemy 2, Alembic, psycopg, scikit-learn, PyMuPDF, pydantic-settings, pytest, Docker Compose | `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `pytest.ini` | Runtime install; README versions |
| Pin compatible versions; record tested Python/Docker in README | `requirements.txt`, `README.md` | README |
| Backend container + PostgreSQL + Compose; no Streamlit in Phase 1 | `Dockerfile`, `docker-compose.yml` | Compose file has backend+postgres only |
| Alembic migrations | `alembic/`, `alembic.ini` | `alembic upgrade head` in verification |
| `.env.example`, `.gitignore`, README, ARCHITECTURE, this matrix | repo root | files exist |
| Unit, DB-integration, parser, service-integration tests | `tests/unit`, `tests/integration`, `tests/parsers`, `tests/services` | pytest collection |
| Layers: domain, services, persistence, providers, adapters, API, CLI | `app/*` | import graph; analysis tests without FastAPI context |
| Domain/services independent of FastAPI/Streamlit/LLM/global user state | `app/domain`, `app/services` | `test_api_and_cli_share_analysis_service`; direct `run_analysis` |
| `run_analysis(user_id, trigger, as_of, idempotency_key)` | `app/services/analysis.py` | `tests/services/test_analysis.py` |
| Triggers MANUAL, API, SCHEDULED (domain only for scheduled) | `app/domain/enums.py` | enum + API rejects using SCHEDULED as a scheduler |
| FastAPI and CLI call the same service; CLI has no financial logic | `app/api/health.py`, `app/jobs/run_analysis.py` | `test_api_and_cli_share_analysis_service` |
| Persist analysis run before work; trigger, start/finish, status, failure reason | `AnalysisRun` model + `run_analysis` | analysis integration |
| Duplicate idempotency keys return existing or reject mismatch | `run_analysis` unique `(user_id, idempotency_key)` | `test_analysis_gates_conflicts_idempotency_and_routing` |
| Prevent two active analyses per portfolio + as_of period | `portfolio_analysis_locks` partial unique | model + IntegrityError path |
| Isolate portfolio failures | `_analyze_portfolio` per try/except | analysis service |
| Provider protocols for every external dependency | `app/providers/protocols.py`, fakes | routing assertions |
| Persist candidates before `evaluate_candidate` | `HarvestingService.persist_pending_candidates` | analysis integration |
| No order prepare/execute in Phase 1 | tables exist; no submit path | analysis never writes `paper_orders` |
| Completed stages retryable | candidate unique per run/lot; window idempotent puts; score unique | ingest + analysis + window tests |
| CLI `python -m app.jobs.run_analysis --user/--all-users` | `app/jobs/run_analysis.py` | module argparse |
| Persistence model: users through paper_orders (listed entities) | `app/persistence/models.py` | create_all / migrations |
| UUID PKs; UTC timestamps; rule versions; provider names/timestamps; synthetic markers | models | seed + analysis |
| Preserve individual tax lots (no average-cost collapse) | `tax_lots` rows | `test_brokerage_parser_counts`; analysis multiple lots |
| Explicit candidate/prep/order enums | `CandidateStatus`, `PreparationStatus`, `PaperOrderStatus` | harvesting transitions |
| Exactly two PDF families with marker + demo banner on every page | `app/demo_data/pdf_layout.py` | parser page continuity |
| Parser uses marker **and** structure; not banner alone | `detect_format` | `test_rejects_unknown_format_and_banner_only` |
| Bank/brokerage required fields, tables, lots, sales, dividends, realized summaries | generators + parsers | parser tests |
| Deterministic PyMuPDF, not LLM | `app/parsers/*` | parser tests |
| Validate version, headers, columns, page continuity, field counts, dates, currencies, numerics, reconciliation | parsers | parser tests |
| Reject scanned PDFs / unknown / malformed with stable codes; no partial DB write | `ParseErrorCode`; ingest transaction | `test_rejects_scanned_insufficient_text`; ingest rollback |
| Source page + parsing confidence per row | parsed rows / `BankTransaction` | bank parser test |
| Duplicate statement/transaction IDs idempotent | ingest existing lookup | `test_ingest_is_idempotent_and_rolls_back_on_parse_error` |
| Uploads via storage interface in gitignored `local-data` | `LocalStatementStorage`, `.gitignore` | ingest saves files |
| PDF generator is demo-only, not API/MCP/UI | `app/demo_data/` | no generation route in FastAPI |
| Bank txn storage fields including original amount/ccy never overwritten; FX metadata | `BankTransaction` + ingest | `test_original_amount_not_replaced_for_fx` |
| Deterministic query services (spending, income, cash-flow, comparison, category, merchant, largest, balances, anomalies via scores) | `app/services/statistics.py` | `tests/services/test_statistics.py` |
| Stats return value, currency, range, count, sources, low-confidence; no unconverted cross-currency totals | `StatisticalResult` | statistics tests |
| IsolationForest + fixed seed; fallback; drift; 5d/20d/drawdown; risk/targets; lot candidates | anomalies, portfolio, harvesting | unit + analysis tests |
| Explicit scores: raw decision, normalized higher=anomalous, model version, feature set | `AnomalyScore` | `test_isolation_forest_normalized_score_direction_and_thresholds` |
| Ground-truth labels stored separately, never in fit | `anomaly_ground_truth` | threshold test fits without labels |
| Feature contract (listed features), no leakage, per-user fit | `app/services/features.py` | `test_features_do_not_leak_future_transactions`; `test_per_user_histories_are_independent` |
| Configurable contamination, min history, windows, seed | `Settings` | settings + anomaly tests |
| Insufficient history → `INSUFFICIENT_HISTORY` + fallback | `AnomalyService` | `test_insufficient_history_uses_fallback` |
| Precision/recall/FPR/top-k/percentile thresholds on fixtures | `evaluation_metrics` | `test_isolation_forest_normalized_score_direction_and_thresholds` |
| RollingWindowStore + local PG implementation + in-memory fake; DynamoDB contract | `app/adapters/*window*` | `test_window_contract.py`, `test_window_postgres.py` |
| Sync: missing after timestamp, overlap, idempotent identity, prune/cutoff, no meta advance on partial failure | `WindowSyncService` | `test_window_fetch_overlap_idempotent_prune_and_partial_failure` |
| Rapid decline is a warning only | `PriceRisk.rapid_decline_warning` | portfolio module |
| Hard gates 1–12; ineligible types never candidates; missing basis rejected; unknown replacement rejected | `HarvestingService._apply_gates` | analysis integration codes |
| Versioned replacement table; crypto 30-day policy labelled as project policy | `replacement_relationships`; rejection explanation | seed replacements + analysis |
| Cross-run conflict fingerprint unique; STILL_ACTIVE; preserve first_seen; bump last_seen/count; resolve not delete; transactional upsert | `ConflictService`, `candidate_conflict_identities` | analysis conflict assertions; fingerprint unit test |
| Deterministic ranking keys 1–8; unique lot id tie-break | `rank_key` | `test_ranking_stable_after_shuffle` |
| Harvesting target = positive combined net realized (ST/LT independent); educational label | `harvesting_target`; README | brokerage realized tests |
| Target reduction, Decimal partial lots, never exceed lot/mirror qty | `select_against_target` | rank unit tests |
| Persist display-ready approved/rejected evaluation fields | `Evaluation` columns | analysis integration |
| Rejected/below-threshold/expired/non-executable never ranked or prepared | ranking filter | analysis `ranked` vs rejected set |
| Synthetic generator: 6 bank PDFs, 2 brokerage, 2 users, 1 taxable portfolio each, 1 mirror manifest each; fixed seed; mocked prices | `app/demo_data/generate.py` | `test_generate_counts_and_user_isolation` |
| 3 consecutive monthly bank statements/user; ≥75 txn/statement; ≥225/user; ≥3 anomalies/statement; ≥9/user; listed patterns | `bank_generator.py` | seed + parser tests |
| FX counts, refunds, merchants, categories, fees; reconcile balances; no FX lots | bank generator + parser | seed test |
| Brokerage holding/crypto/lot/sale/dividend/gain-loss counts; multi-lot; ST/LT independent; net gain bands | `brokerage_generator.py` | parser + seed tests |
| 14+ calculable loss lots with approved/wash/risk/threshold/replacement mix; missing basis; profitable; mirror; stale quote; reinvest/crypto buys | lot purposes | analysis gate codes |
| Distinct conservative vs growth risk profiles | `seed_risk_and_targets` | analysis risk rejections |
| Mirror manifest fields; planned sale ≤ manifest qty; PG is tax-lot source | `_mirror_payload` | generate payload |
| Tests never call live APIs; patch at provider boundary | `tests/conftest.py` `BlockedSocket`; fakes only | conftest + routing test |
| Current-demo statements relative to `DEMO_AS_OF_DATE` (default 2026-08-28); 2024 PDFs preserved | `build_bank_statements(as_of=)`, `portfolio_*_spec(as_of=)` | `tests/parsers/test_current_demo.py` |
| Interactive local demo may set `DEMO_AS_OF_DATE=today`; tests pin a fixed date | `parse_demo_as_of_date`, `resolve_analysis_as_of` | `tests/unit/test_freshness.py` |
| Current-demo bank history meets `min_history_threshold`; wash/reinvest/scheduled offsets explicit; dividend dates consistent | bank/brokerage generators | current-demo parser tests |
| Current brokerage qty from mapped Alpaca account; reconcile lots; never treat 2024 as current book | `HarvestingService._freshness_or_reconciliation` | `tests/services/test_reconciliation.py` |
| Missing coverage or mismatch fails closed (`DATA_STALE`, `INCOMPLETE_HISTORY`, `POSITION_MISMATCH`); verify qty before order | `app/services/freshness.py` | reconciliation + freshness tests |
| Separate users never share data | ingest user_id filters | seed isolation assert |
| Financial math uses Decimal | services | drift + selection tests |
| Concise data summary | `local-data/data_summary.json` from generator | generate returns summary |
| Health endpoints | `GET /health`, `/health/ready` | FastAPI routes |
