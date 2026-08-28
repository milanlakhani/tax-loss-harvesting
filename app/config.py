from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://finance:finance@localhost:5432/finance"

    isolation_forest_seed: int = 42
    isolation_forest_contamination: float = 0.06
    isolation_forest_n_estimators: int = 200
    min_history_threshold: int = 80
    min_loss_threshold: Decimal = Decimal("50.00")
    price_window_days: int = 60
    anomaly_window_days: int = 180
    window_overlap_hours: int = 24
    quote_max_age_minutes: int = 15
    harvest_allow_exceed_target: bool = False
    crypto_repurchase_window_days: int = 30
    wash_sale_window_days: int = 30
    long_term_holding_days: int = 365
    local_data_dir: Path = Path("local-data")
    feature_set_version: str = "iforest_features_v1"
    model_version: str = "IsolationForest-1.6-v1"
    replacement_rule_version: str = "replacement_v1"
    harvesting_rule_version: str = "harvest_gates_v1"
    conflict_fingerprint_version: str = "conflict_fp_v1"
    median_lookback_days: int = 90
    duplicate_proximity_hours: int = 48
    similar_amount_tolerance: Decimal = Decimal("0.02")
    normal_monthly_income_lookback_months: int = 3

    openai_api_key: str | None = None
    alpha_vantage_api_key: str | None = None
    coingecko_api_key: str | None = None
    alpaca_paper: bool = True
    enable_paper_orders: bool = False

    demo_session_signing_secret: str = "change-me"

    @property
    def is_local(self) -> bool:
        return self.app_env.lower() in {"local", "test", "testing"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(settings: Settings) -> Settings:
    global _settings
    _settings = settings
    return _settings
