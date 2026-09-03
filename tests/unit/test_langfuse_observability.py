from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import langfuse_smoke as smoke
from app.main import create_app
from app.observability import langfuse as adapter
from tests.unit.test_mcp_split import REPO, _compose_services


@pytest.fixture(autouse=True)
def reset_langfuse_configuration(monkeypatch):
    monkeypatch.setattr(adapter, "_configured", False)
    monkeypatch.setattr(adapter, "_available", False)
    monkeypatch.setattr(adapter, "_instrument_calls", 0)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "database_url": "postgresql+psycopg://finance:finance@postgres:5432/finance_test",
        "local_data_dir": tmp_path,
    }
    values.update(overrides)
    return Settings(**values)


def _enabled(tmp_path, **overrides) -> Settings:
    values = {
        "langfuse_enabled": True,
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "langfuse_base_url": "https://cloud.langfuse.com",
    }
    values.update(overrides)
    return _settings(tmp_path, **values)


class _Observation:
    def __init__(self):
        self.output = None

    def update(self, *, output):
        self.output = output


class _Ctx:
    def __init__(self, value=None):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        return False


def _install_instrumentation_stubs(monkeypatch, *, already_instrumented: bool = False):
    captured: dict = {"instrument_calls": 0, "processors": 0, "trace_config": None}

    langfuse_mod = types.ModuleType("langfuse")
    langfuse_mod.get_client = lambda: object()
    oi = types.ModuleType("openinference")
    oi_instr = types.ModuleType("openinference.instrumentation")

    class TraceConfig:
        def __init__(self, **kwargs):
            captured["trace_config"] = kwargs

    oi_instr.TraceConfig = TraceConfig
    oi_agents = types.ModuleType("openinference.instrumentation.openai_agents")

    class OpenAIAgentsInstrumentor:
        _is_instrumented_by_opentelemetry = already_instrumented

        def instrument(self, config=None):
            captured["instrument_calls"] += 1
            captured["instrument_config"] = config

    oi_agents.OpenAIAgentsInstrumentor = OpenAIAgentsInstrumentor
    otel = types.ModuleType("opentelemetry")
    otel_trace = types.ModuleType("opentelemetry.trace")

    class Provider:
        def add_span_processor(self, processor):
            captured["processors"] += 1

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
    return captured


def _install_trace_runtime(monkeypatch, *, observation=None, boom: Exception | None = None):
    captured: dict = {}
    observation = observation or _Observation()

    def start_as_current_observation(**kwargs):
        captured["observation"] = kwargs
        return _Ctx(observation)

    def propagate_attributes(**kwargs):
        captured["attributes"] = kwargs
        return _Ctx()

    class Client:
        def start_as_current_observation(self, **kwargs):
            return start_as_current_observation(**kwargs)

    langfuse_mod = types.ModuleType("langfuse")
    langfuse_mod.propagate_attributes = propagate_attributes
    if boom is None:
        langfuse_mod.get_client = lambda: Client()
    else:

        def fail_client():
            raise boom

        langfuse_mod.get_client = fail_client
    monkeypatch.setitem(sys.modules, "langfuse", langfuse_mod)
    return captured


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
    assert adapter._instrument_calls == 0


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
    assert adapter._instrument_calls == 0


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
    assert adapter._anonymous_id(raw) == anonymous


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


def test_trace_input_is_redacted_unless_explicitly_captured():
    assert adapter._trace_input("Show my holdings", capture_content=False) == {
        "user_message": "[redacted]",
        "content_capture": "disabled",
    }
    assert adapter._trace_input("Show my holdings", capture_content=True) == {
        "user_message": "Show my holdings"
    }


def test_string_metadata_encodes_booleans():
    encoded = adapter._string_metadata({"content_capture": False, "langfuse_host_configured": True, "model": "m"})
    assert encoded == {
        "content_capture": "false",
        "langfuse_host_configured": "true",
        "model": "m",
    }
    assert all(isinstance(value, str) for value in encoded.values())


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
        _enabled(tmp_path),
        user_id="u",
        session_id="s",
        message="holdings",
        model="m",
    ):
        reached = True
    assert reached is True


def test_observation_update_failure_does_not_fail_the_turn():
    class Boom:
        def update(self, *, output):
            raise RuntimeError("exporter down")

    adapter.LangfuseTrace(Boom(), capture_content=False).set_output("secret reply")


def test_configure_sdk_exception_is_fail_open(tmp_path, monkeypatch):
    langfuse_mod = types.ModuleType("langfuse")

    def boom():
        raise RuntimeError("network")

    langfuse_mod.get_client = boom
    monkeypatch.setitem(sys.modules, "langfuse", langfuse_mod)
    settings = _enabled(tmp_path)
    assert adapter._configure(settings) is False
    assert adapter._configured is True
    assert adapter._available is False
    reached = False
    with adapter.langfuse_trace(settings, user_id="u", session_id="s", message="holdings", model="m"):
        reached = True
    assert reached is True


def test_configure_overwrites_empty_env_with_settings_host(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "")
    captured = _install_instrumentation_stubs(monkeypatch)

    settings = _enabled(
        tmp_path,
        app_env="aws",
        langfuse_base_url="https://example.invalid",
        langfuse_tracing_environment="",
    )
    assert adapter.configure_langfuse(settings) is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-test"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-test"
    assert os.environ["LANGFUSE_BASE_URL"] == "https://example.invalid"
    assert os.environ["LANGFUSE_TRACING_ENVIRONMENT"] == "aws"
    assert adapter._available is True
    assert captured["instrument_calls"] == 1
    assert captured["processors"] == 1
    assert captured["trace_config"] == {
        "hide_inputs": True,
        "hide_outputs": True,
        "hide_llm_invocation_parameters": True,
        "hide_llm_tools": True,
    }


def test_single_initialization_does_not_reinstrument(tmp_path, monkeypatch):
    captured = _install_instrumentation_stubs(monkeypatch)
    settings = _enabled(tmp_path)
    assert adapter.configure_langfuse(settings) is True
    assert adapter.configure_langfuse(settings) is True
    assert captured["instrument_calls"] == 1
    assert adapter._instrument_calls == 1


def test_duplicate_instrumentation_is_skipped_after_reset(tmp_path, monkeypatch):
    captured = _install_instrumentation_stubs(monkeypatch)
    settings = _enabled(tmp_path)
    assert adapter._configure(settings) is True
    monkeypatch.setattr(adapter, "_configured", False)
    assert adapter._configure(settings) is True
    assert captured["instrument_calls"] == 1
    assert captured["processors"] == 1
    assert adapter._instrument_calls == 1


def test_already_instrumented_openai_agents_sdk_is_not_patched_again(tmp_path, monkeypatch):
    captured = _install_instrumentation_stubs(monkeypatch, already_instrumented=True)
    settings = _enabled(tmp_path)
    assert adapter._configure(settings) is True
    assert captured["instrument_calls"] == 0
    assert captured["processors"] == 0
    assert adapter._instrument_calls == 0
    assert adapter._available is True


def test_explicit_content_capture_disables_openinference_redaction(tmp_path, monkeypatch):
    captured = _install_instrumentation_stubs(monkeypatch)
    settings = _enabled(tmp_path, langfuse_capture_content=True)
    assert adapter._configure(settings) is True
    assert captured["trace_config"] == {
        "hide_inputs": False,
        "hide_outputs": False,
        "hide_llm_invocation_parameters": False,
        "hide_llm_tools": False,
    }


def test_trace_uses_hashed_user_and_session_identifiers(tmp_path, monkeypatch):
    _install_instrumentation_stubs(monkeypatch)
    captured = _install_trace_runtime(monkeypatch)
    user_id = "11111111-1111-4111-8111-111111111111"
    session_id = "22222222-2222-4222-8222-222222222222"
    settings = _enabled(tmp_path)
    adapter._configure(settings)
    monkeypatch.setattr(adapter, "_configured", True)
    monkeypatch.setattr(adapter, "_available", True)

    reached = False
    with adapter.langfuse_trace(
        settings,
        user_id=user_id,
        session_id=session_id,
        message="Show my holdings",
        model="gpt-4.1-mini",
    ) as trace:
        reached = True
        trace.set_output("holdings reply")

    assert reached is True
    assert captured["observation"]["name"] == adapter.ORCHESTRATOR_TRACE_NAME
    assert captured["observation"]["as_type"] == "agent"
    assert captured["observation"]["input"]["user_message"] == "[redacted]"
    attributes = captured["attributes"]
    assert attributes["user_id"] == adapter._anonymous_id(user_id)
    assert attributes["session_id"] == adapter._anonymous_id(session_id)
    assert user_id not in attributes["user_id"]
    assert session_id not in attributes["session_id"]
    assert attributes["trace_name"] == adapter.ORCHESTRATOR_TRACE_NAME
    assert attributes["tags"] == ["northstar", "chat", "orchestrator"]
    assert attributes["metadata"]["content_capture"] == "disabled"
    assert attributes["metadata"]["model"] == "gpt-4.1-mini"
    assert all(isinstance(value, str) for value in attributes["metadata"].values())


def test_application_error_is_not_swallowed_by_langfuse(tmp_path, monkeypatch):
    _install_instrumentation_stubs(monkeypatch)
    _install_trace_runtime(monkeypatch)
    settings = _enabled(tmp_path)
    adapter._configure(settings)
    monkeypatch.setattr(adapter, "_configured", True)
    monkeypatch.setattr(adapter, "_available", True)
    with pytest.raises(RuntimeError, match="chat failed"):
        with adapter.langfuse_trace(
            settings, user_id="u", session_id="s", message="holdings", model="m"
        ):
            raise RuntimeError("chat failed")


def test_lifespan_configures_langfuse_for_real_settings(tmp_path, monkeypatch):
    called: list[Settings] = []
    monkeypatch.setattr("app.main.configure_langfuse", lambda settings: called.append(settings) or False)
    settings = _settings(tmp_path)
    container = MagicMock()
    container.settings = settings
    app = create_app(container)
    with TestClient(app):
        pass
    assert called == [settings]


def test_lifespan_skips_mock_settings(monkeypatch):
    called = []
    monkeypatch.setattr("app.main.configure_langfuse", lambda settings: called.append(settings) or False)
    app = create_app(MagicMock())
    with TestClient(app):
        pass
    assert called == []


def test_compose_keeps_langfuse_credentials_on_backend_only():
    services = _compose_services(REPO / "docker-compose.yml")
    backend = services["backend"]
    mcp = services["mcp"]
    ui = services["ui"]
    assert 'LANGFUSE_ENABLED: "false"' in mcp
    assert 'LANGFUSE_PUBLIC_KEY: ""' in mcp
    assert 'LANGFUSE_SECRET_KEY: ""' in mcp
    assert 'LANGFUSE_ENABLED: "false"' in ui
    assert 'LANGFUSE_PUBLIC_KEY: ""' in ui
    assert "LANGFUSE_ENABLED: \"false\"" not in backend
    assert "LANGFUSE_PUBLIC_KEY" not in backend
    assert "LANGFUSE_SECRET_KEY" not in backend
    assert "env_file:" in backend


def test_aws_stack_source_injects_langfuse_only_on_backend():
    text = (REPO / "infrastructure" / "stacks" / "tlh_stack.py").read_text(encoding="utf-8")
    assert "LANGFUSE_BACKEND_SECRET_KEYS" in text
    secret_example = (REPO / "infrastructure" / "app-secret.example.json").read_text(encoding="utf-8")
    assert '"LANGFUSE_PUBLIC_KEY": ""' in secret_example
    assert '"LANGFUSE_SECRET_KEY": ""' in secret_example
    assert "pk-lf-" not in secret_example
    assert "sk-lf-" not in secret_example


def test_eval_hook_is_outside_langfuse_trace():
    source = (REPO / "app" / "agents" / "runner.py").read_text(encoding="utf-8")
    llm_section = source.split("async def _run_llm")[0]
    assert llm_section.index("with langfuse_trace(") < llm_section.index("invoked = await _ensure_eval")
    assert llm_section.index("trace.set_output(reply)") < llm_section.index("invoked = await _ensure_eval")


def test_pytest_ini_excludes_jobs_from_default_suite():
    ini = (REPO / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in ini


def test_smoke_skips_when_disabled(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "get_settings", lambda: _settings(tmp_path, langfuse_enabled=False))
    assert smoke.main([]) == 2
    assert "skipped" in capsys.readouterr().out.lower()


def test_smoke_skips_when_credentials_incomplete(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        smoke,
        "get_settings",
        lambda: _enabled(tmp_path, langfuse_public_key=None, openai_api_key="sk-test-openai"),
    )
    assert smoke.main([]) == 2
    assert "incomplete" in capsys.readouterr().out.lower()


def test_smoke_does_not_print_credentials_on_export_failure(tmp_path, monkeypatch, capsys):
    settings = _enabled(tmp_path, openai_api_key="sk-openai-secret")
    monkeypatch.setattr(smoke, "get_settings", lambda: settings)
    monkeypatch.setattr(smoke, "configure_langfuse", lambda _settings: True)

    class Client:
        def auth_check(self):
            return True

        def flush(self):
            return None

    langfuse_mod = types.ModuleType("langfuse")
    langfuse_mod.get_client = lambda: Client()
    monkeypatch.setitem(sys.modules, "langfuse", langfuse_mod)

    async def fake_run(_settings, _session_id):
        raise RuntimeError(f"failed {settings.langfuse_secret_key} {settings.openai_api_key}")

    monkeypatch.setattr(smoke, "_run_synthetic_agent", fake_run)

    assert smoke.main([]) == 1
    output = capsys.readouterr().out
    assert settings.langfuse_secret_key not in output
    assert settings.openai_api_key not in output
    assert "Credentials were not displayed" in output


def test_wait_for_smoke_trace_matches_hashed_session():
    session_id = "langfuse-smoke-session"
    session_hash = adapter._anonymous_id(session_id)

    class Item:
        name = adapter.ORCHESTRATOR_TRACE_NAME
        session_id = session_hash
        id = "trace-1"

    class Client:
        class api:
            class trace:
                @staticmethod
                def list(limit=50):
                    return types.SimpleNamespace(data=[Item()])

    found = smoke.wait_for_smoke_trace(Client(), session_hash=session_hash, timeout=0.01)
    assert found.id == "trace-1"


def test_smoke_success_flushes_and_reports_trace_name(tmp_path, monkeypatch, capsys):
    settings = _enabled(tmp_path, openai_api_key="sk-openai-secret")
    flushed = []
    monkeypatch.setattr(smoke, "get_settings", lambda: settings)
    monkeypatch.setattr(smoke, "configure_langfuse", lambda _settings: True)

    class Client:
        def auth_check(self):
            return True

        def flush(self):
            flushed.append(True)

    langfuse_mod = types.ModuleType("langfuse")
    langfuse_mod.get_client = lambda: Client()
    monkeypatch.setitem(sys.modules, "langfuse", langfuse_mod)

    async def fake_run(_settings, _session_id):
        return "ok"

    monkeypatch.setattr(smoke, "_run_synthetic_agent", fake_run)
    monkeypatch.setattr(
        smoke,
        "wait_for_smoke_trace",
        lambda client, *, session_hash, timeout=45: types.SimpleNamespace(id="trace-1"),
    )
    assert smoke.main([]) == 0
    output = capsys.readouterr().out
    assert flushed == [True]
    assert adapter.ORCHESTRATOR_TRACE_NAME in output
    assert "authentication succeeded" in output.lower()
    assert settings.langfuse_secret_key not in output
    assert settings.openai_api_key not in output
    assert "pk-test" not in output
