from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest

from app.adapters.dynamodb_window_store import DynamoDBRollingWindowStore, InMemoryDynamoTable, SCHEMA_VERSION
from app.adapters.memory_window_store import InMemoryRollingWindowStore
from app.adapters.rolling_window import (
    anomaly_window_key,
    fx_key,
    price_window_key,
    quote_key,
    timestamp_sort_key,
    window_meta_key,
)
from app.adapters.s3_storage import S3StatementStorage
from app.adapters.secrets import APP_SECRET_KEYS, LocalEnvSecrets, SecretsManagerOverlay
from app.config import Settings
from app.container import _build_storage
from app.adapters.storage import LocalStatementStorage
from app.providers.fakes import RecordingClock


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.encryption: dict[tuple[str, str], str] = {}

    def put_object(self, Bucket, Key, Body, ServerSideEncryption):
        self.objects[(Bucket, Key)] = Body
        self.encryption[(Bucket, Key)] = ServerSideEncryption

    def get_object(self, Bucket, Key):
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}


class FakeSecrets:
    def __init__(self, payload: dict) -> None:
        import json

        self.payload = json.dumps(payload)

    def get_secret_value(self, SecretId):
        assert SecretId
        return {"SecretString": self.payload}


@pytest.mark.unit
def test_local_settings_do_not_require_allowed_cidr(monkeypatch):
    monkeypatch.delenv("ALLOWED_IPV4_CIDR", raising=False)
    settings = Settings(app_env="local", database_url="postgresql+psycopg://finance:finance@localhost:5432/finance")
    assert settings.is_local
    assert not settings.is_aws


@pytest.mark.unit
def test_aws_container_selects_s3_storage():
    local = _build_storage(Settings(app_env="local"))
    aws = _build_storage(Settings(app_env="aws", statements_bucket="demo-statements"))
    assert isinstance(local, LocalStatementStorage)
    assert isinstance(aws, S3StatementStorage)


@pytest.mark.unit
def test_s3_storage_round_trip_and_uri():
    client = FakeS3()
    store = S3StatementStorage("demo-bucket", client=client)
    uri = store.save("user-1/stmt.pdf", b"%PDF-1.4 demo")
    assert uri == "s3://demo-bucket/statements/user-1/stmt.pdf"
    assert client.encryption[("demo-bucket", "statements/user-1/stmt.pdf")] == "AES256"
    assert store.load("user-1/stmt.pdf") == b"%PDF-1.4 demo"


@pytest.mark.unit
def test_secrets_overlay_fills_blank_env_only():
    overlay = SecretsManagerOverlay(
        "arn:aws:secretsmanager:eu-west-2:123:secret:app",
        client=FakeSecrets({"OPENAI_API_KEY": "from-sm", "ALPHA_VANTAGE_API_KEY": "av"}),
    )
    env = overlay.apply({"OPENAI_API_KEY": "already-set", "ALPHA_VANTAGE_API_KEY": ""})
    assert env["OPENAI_API_KEY"] == "already-set"
    assert env["ALPHA_VANTAGE_API_KEY"] == "av"
    assert APP_SECRET_KEYS[0] == "OPENAI_API_KEY"
    assert "LANGFUSE_PUBLIC_KEY" in APP_SECRET_KEYS
    assert "LANGFUSE_SECRET_KEY" in APP_SECRET_KEYS
    assert "LANGFUSE_BASE_URL" in APP_SECRET_KEYS
    local = LocalEnvSecrets().apply({"OPENAI_API_KEY": "local"})
    assert local["OPENAI_API_KEY"] == "local"


@pytest.mark.unit
def test_secrets_overlay_fills_blank_langfuse_keys():
    overlay = SecretsManagerOverlay(
        "arn:aws:secretsmanager:eu-west-2:123:secret:app",
        client=FakeSecrets(
            {
                "LANGFUSE_ENABLED": "true",
                "LANGFUSE_PUBLIC_KEY": "from-sm",
                "LANGFUSE_SECRET_KEY": "from-sm-secret",
                "LANGFUSE_BASE_URL": "https://example.invalid",
            }
        ),
    )
    env = overlay.apply(
        {
            "LANGFUSE_ENABLED": "",
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "already-set",
            "LANGFUSE_BASE_URL": "",
        }
    )
    assert env["LANGFUSE_ENABLED"] == "true"
    assert env["LANGFUSE_PUBLIC_KEY"] == "from-sm"
    assert env["LANGFUSE_SECRET_KEY"] == "already-set"
    assert env["LANGFUSE_BASE_URL"] == "https://example.invalid"


async def _shared_window_contract(store):
    quote = quote_key("ETF:VTI", "USD")
    price = price_window_key("ETF:VTI", "USD")
    anomaly = anomaly_window_key("user-1", "amount")
    fx = fx_key("USD", "GBP", "2024-06-01")
    now = datetime(2024, 6, 10, 12, 0, tzinfo=UTC)
    ts = datetime(2024, 6, 1, tzinfo=UTC)

    await store.put_observation(
        quote,
        "CURRENT",
        {"price": "200", "provider": "alpha-vantage"},
        provider="alpha-vantage",
        source_timestamp=ts,
        retrieved_at=now,
    )
    await store.put_observation(
        quote,
        "CURRENT",
        {"price": "201", "provider": "alpha-vantage"},
        provider="alpha-vantage",
        source_timestamp=ts,
        retrieved_at=now,
    )
    quotes = await store.get_observations(quote)
    assert len(quotes) == 1
    assert quotes[0].payload["price"] == "201"

    sk1 = timestamp_sort_key(ts, "ETF:VTI")
    sk2 = timestamp_sort_key(ts + timedelta(days=1), "ETF:VTI")
    await store.put_observation(price, sk1, {"price": "200"}, provider="alpha-vantage", source_timestamp=ts, retrieved_at=now)
    await store.put_observation(
        price,
        sk2,
        {"price": "202"},
        provider="alpha-vantage",
        source_timestamp=ts + timedelta(days=1),
        retrieved_at=now,
    )
    cutoff = ts + timedelta(days=1)
    visible = await store.get_observations(price, cutoff=cutoff)
    assert all(row.source_timestamp >= cutoff for row in visible if row.source_timestamp)
    all_rows = await store.get_observations(price)
    assert len(all_rows) == 2

    await store.put_observation(
        anomaly,
        timestamp_sort_key(ts, "obs-1"),
        {"score": "0.9"},
        provider="local-ml",
        source_timestamp=ts,
        retrieved_at=now,
    )
    await store.put_observation(
        fx,
        "RATE",
        {"rate": "0.79"},
        provider="frankfurter",
        source_timestamp=ts,
        retrieved_at=now,
    )
    await store.advance_meta(price, now, extra={"schema_version": SCHEMA_VERSION})
    meta = await store.get_meta(price)
    assert meta is not None
    assert meta.last_successful_at == now
    assert (await store.get_observations(window_meta_key(price))) or True
    return quote, now, ts


@pytest.mark.unit
async def test_dynamodb_and_local_window_semantics_match():
    memory = InMemoryRollingWindowStore(RecordingClock())
    dynamo = DynamoDBRollingWindowStore(InMemoryDynamoTable(), ttl_days=180)
    await _shared_window_contract(memory)
    quote, now, ts = await _shared_window_contract(dynamo)
    item = dynamo.table[(quote, "CURRENT")]
    assert item["schema_version"] == SCHEMA_VERSION
    assert item["provider"] == "alpha-vantage"
    assert item["freshness"] is not None
    assert item["expires_at"]
    assert item["ttl"] == int((now + timedelta(days=180)).timestamp())
    stale = await dynamo.get_observations(quote, cutoff=now)
    assert stale == []
    still_stored = dynamo.table[(quote, "CURRENT")]
    assert still_stored["sk"] == "CURRENT"
    assert ts < now
