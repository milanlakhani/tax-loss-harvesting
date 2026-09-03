from __future__ import annotations

import os
import sys
import types
from uuid import uuid4

import pytest

from app.config import Settings
from app.observability import langfuse as adapter


@pytest.fixture(autouse=True)
def reset_langfuse_configuration(monkeypatch):
    monkeypatch.setattr(adapter, "_configured", False)
    monkeypatch.setattr(adapter, "_available", False)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "database_url": "postgresql+psycopg://finance:finance@postgres:5432/finance_test",
        "local_data_dir": tmp_path,
    }
    values.update(overrides)
    return Settings(**values)


def test_langfuse_disabled_is_a_noop(tmp_path):
    settings = _settings(tmp_path, langfuse_enabled=False)

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
    settings = _settings(tmp_path, langfuse_enabled=True, langfuse_public_key=None, langfuse_secret_key=None)

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


def test_disabled_and_incomplete_config_never_import_langfuse(tmp_path, monkeypatch):
    imported: list[str] = []
    real_import = __import__

    def guarded_import(name, *args, **kwargs):
        imported.append(name)
        if name == "langfuse" or name.startswith("langfuse.") or name.startswith("openinference"):
            raise AssertionError("Langfuse SDK must not be imported without complete credentials")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    disabled = _settings(tmp_path, langfuse_enabled=False)
    with adapter.langfuse_trace(disabled, user_id="u", session_id="s", message="holdings", model="m"):
        pass
    incomplete = _settings(tmp_path, langfuse_enabled=True, langfuse_public_key=None, langfuse_secret_key=None)
    with adapter.langfuse_trace(incomplete, user_id="u", session_id="s", message="holdings", model="m"):
        pass
    assert "langfuse" not in imported


def test_blank_secret_strings_keep_langfuse_disabled(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="aws",
        database_url="postgresql+psycopg://finance:finance@postgres:5432/finance_test",
        local_data_dir=tmp_path,
        langfuse_enabled="",
        langfuse_public_key="",
        langfuse_secret_key="",
        langfuse_base_url="",
        langfuse_tracing_environment="",
        langfuse_capture_content="",
    )
    assert settings.langfuse_enabled is False
    assert settings.langfuse_public_key is None
    assert settings.langfuse_secret_key is None
    assert settings.langfuse_base_url == "https://cloud.langfuse.com"
    assert settings.langfuse_tracing_environment is None
    assert settings.langfuse_capture_content is False
    reached = False
    with adapter.langfuse_trace(settings, user_id="u", session_id="s", message="holdings", model="m"):
        reached = True
    assert reached is True
    assert adapter._available is False


def test_anonymous_id_is_hashed():
    raw = "11111111-1111-4111-8111-111111111111"
    anonymous = adapter._anonymous_id(raw)
    assert anonymous != raw
    assert len(anonymous) == 24
    assert raw not in anonymous


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


def test_telemetry_setup_failure_does_not_fail_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_configured", True)
    monkeypatch.setattr(adapter, "_available", True)
    monkeypatch.setattr(adapter, "_configure", lambda _settings: True)
    real_import = __import__

    def fail_import(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            raise RuntimeError("exporter down")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_import)
    reached = False
    with adapter.langfuse_trace(
        _settings(tmp_path, langfuse_enabled=True, langfuse_public_key="pk", langfuse_secret_key="sk"),
        user_id="u",
        session_id="s",
        message="holdings",
        model="m",
    ):
        reached = True
    assert reached is True


def test_configure_overwrites_empty_env_with_settings_host(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "")

    langfuse_mod = types.ModuleType("langfuse")
    langfuse_mod.get_client = lambda: object()
    oi = types.ModuleType("openinference")
    oi_instr = types.ModuleType("openinference.instrumentation")

    class TraceConfig:
        def __init__(self, **kwargs):
            pass

    oi_instr.TraceConfig = TraceConfig
    oi_agents = types.ModuleType("openinference.instrumentation.openai_agents")

    class OpenAIAgentsInstrumentor:
        def instrument(self, config=None):
            pass

    oi_agents.OpenAIAgentsInstrumentor = OpenAIAgentsInstrumentor
    otel = types.ModuleType("opentelemetry")
    otel_trace = types.ModuleType("opentelemetry.trace")

    class Provider:
        def add_span_processor(self, processor):
            pass

    otel_trace.get_tracer_provider = lambda: Provider()
    otel.trace = otel_trace
    otel_sdk = types.ModuleType("opentelemetry.sdk")
    otel_sdk_trace = types.ModuleType("opentelemetry.sdk.trace")

    class SpanProcessor:
        pass

    otel_sdk_trace.SpanProcessor = SpanProcessor
    for name, module in (
        ("langfuse", langfuse_mod),
        ("openinference", oi),
        ("openinference.instrumentation", oi_instr),
        ("openinference.instrumentation.openai_agents", oi_agents),
        ("opentelemetry", otel),
        ("opentelemetry.trace", otel_trace),
        ("opentelemetry.sdk", otel_sdk),
        ("opentelemetry.sdk.trace", otel_sdk_trace),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    settings = _settings(
        tmp_path,
        app_env="aws",
        langfuse_enabled=True,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        langfuse_base_url="https://example.invalid",
        langfuse_tracing_environment="",
    )
    assert adapter._configure(settings) is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-test"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-test"
    assert os.environ["LANGFUSE_BASE_URL"] == "https://example.invalid"
    assert os.environ["LANGFUSE_TRACING_ENVIRONMENT"] == "aws"
    assert adapter._available is True
