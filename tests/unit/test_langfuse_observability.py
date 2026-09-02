from __future__ import annotations

from uuid import uuid4

import pytest

from app.config import Settings
from app.observability import langfuse as adapter


@pytest.fixture(autouse=True)
def reset_langfuse_configuration(monkeypatch):
    monkeypatch.setattr(adapter, "_configured", False)
    monkeypatch.setattr(adapter, "_available", False)


def test_langfuse_disabled_is_a_noop(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+psycopg://finance:finance@postgres:5432/finance_test",
        local_data_dir=tmp_path,
        langfuse_enabled=False,
    )

    reached = False
    with adapter.langfuse_trace(
        settings,
        user_id=uuid4(),
        session_id=uuid4(),
        message="Show my holdings",
        model="test-model",
    ):
        reached = True

    assert reached is True
    assert adapter._configured is False


def test_langfuse_missing_credentials_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_configured", False)
    monkeypatch.setattr(adapter, "_available", False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+psycopg://finance:finance@postgres:5432/finance_test",
        local_data_dir=tmp_path,
        langfuse_enabled=True,
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )

    reached = False
    with adapter.langfuse_trace(
        settings,
        user_id=uuid4(),
        session_id=uuid4(),
        message="Show my holdings",
        model="test-model",
    ):
        reached = True

    assert reached is True
    assert adapter._configured is True
    assert adapter._available is False


def test_exported_correlation_ids_are_hashed():
    raw = "11111111-1111-4111-8111-111111111111"

    anonymous = adapter._anonymous_id(raw)

    assert anonymous != raw
    assert len(anonymous) == 24


class _Observation:
    def __init__(self):
        self.output = None

    def update(self, *, output):
        self.output = output


def test_trace_output_is_redacted_by_default():
    observation = _Observation()
    trace = adapter.LangfuseTrace(observation, capture_content=False)

    trace.set_output("Sensitive portfolio response")

    assert observation.output == {"status": "completed", "assistant_response": "[redacted]"}


def test_trace_output_is_captured_only_when_enabled():
    observation = _Observation()
    trace = adapter.LangfuseTrace(observation, capture_content=True)

    trace.set_output("Portfolio response")

    assert observation.output == {"assistant_response": "Portfolio response"}
