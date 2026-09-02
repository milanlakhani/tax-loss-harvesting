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
    demo_mode: str = "historical"
    demo_as_of_date: str = "2026-08-28"
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
    openai_model: str = "gpt-4.1-mini"
    enable_llm_orchestrator: bool = True
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_capture_content: bool = False
    alpha_vantage_api_key: str | None = None
    coingecko_api_key: str | None = None
    coingecko_api_plan: str = "demo"
    coingecko_api_base_url: str = "https://api.coingecko.com/api/v3"
    alpaca_paper: bool = True
    enable_paper_orders: bool = False
    use_live_providers: bool = False
    alpaca_account_1_name: str = "conservative-demo"
    alpaca_account_1_key: str | None = None
    alpaca_account_1_secret: str | None = None
    alpaca_account_2_name: str = "growth-demo"
    alpaca_account_2_key: str | None = None
    alpaca_account_2_secret: str | None = None
    dynamodb_table: str = "finance-rolling-windows"
    aws_region: str = "eu-west-2"
    paper_prep_ttl_seconds: int = 300
    backend_public_url: str = "http://localhost:8000"
    demo_session_signing_secret: str = "change-me"
    whatsapp_phone_number: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_allowed_senders: str = ""
    whatsapp_default_user_id: str = "11111111-1111-4111-8111-111111111111"
    whatsapp_graph_api_version: str = "v23.0"
    whatsapp_graph_api_base_url: str = "https://graph.facebook.com"

    def whatsapp_sender_allowlist(self) -> set[str]:
        return {
            "".join(character for character in value if character.isdigit())
            for value in self.whatsapp_allowed_senders.split(",")
            if value.strip()
        }

    @property
    def is_local(self) -> bool:
        return self.app_env.lower() in {"local", "test", "testing"}

    @property
    def is_aws(self) -> bool:
        return self.app_env.lower() == "aws"

    def alpaca_credentials(self) -> dict[str, tuple[str, str]]:
        accounts: dict[str, tuple[str, str]] = {}
        if self.alpaca_account_1_name and self.alpaca_account_1_key:
            accounts[self.alpaca_account_1_name] = (self.alpaca_account_1_key, self.alpaca_account_1_secret or "")
        if self.alpaca_account_2_name and self.alpaca_account_2_key:
            accounts[self.alpaca_account_2_name] = (self.alpaca_account_2_key, self.alpaca_account_2_secret or "")
        return accounts


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
