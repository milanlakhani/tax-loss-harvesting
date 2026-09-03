from __future__ import annotations

import hashlib
import logging
import os
from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator

from app.config import Settings

logger = logging.getLogger(__name__)

ORCHESTRATOR_TRACE_NAME = "run-orchestrator-turn"
_configuration_lock = Lock()
_configured = False
_available = False
_instrument_calls = 0


class LangfuseTrace:
    """Privacy-aware handle for enriching the root agent observation."""

    def __init__(self, observation: Any | None = None, *, capture_content: bool = False) -> None:
        self._observation = observation
        self._capture_content = capture_content

    def set_output(self, reply: str) -> None:
        if self._observation is None:
            return
        output = {"assistant_response": reply} if self._capture_content else {
            "status": "completed",
            "assistant_response": "[redacted]",
        }
        try:
            self._observation.update(output=output)
        except Exception:
            logger.exception("Optional Langfuse trace output could not be recorded")


def _anonymous_id(value: object) -> str:
    """Create a stable correlation ID without exporting the local user/session UUID."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


def _string_metadata(values: dict[str, Any]) -> dict[str, str]:
    """Langfuse v4 propagate_attributes metadata must be string-to-string."""
    encoded: dict[str, str] = {}
    for key, value in values.items():
        if value is True:
            encoded[key] = "true"
        elif value is False:
            encoded[key] = "false"
        else:
            encoded[key] = str(value)
    return encoded


def _trace_input(message: str, *, capture_content: bool) -> dict[str, str]:
    if capture_content:
        return {"user_message": message}
    return {"user_message": "[redacted]", "content_capture": "disabled"}


def configure_langfuse(settings: Settings) -> bool:
    """Instrument the OpenAI Agents SDK once per process. Failures never block the caller."""
    return _configure(settings)


def _configure(settings: Settings) -> bool:
    """Configure OpenInference once; return False without affecting the caller on any failure."""
    global _configured, _available, _instrument_calls
    if not settings.langfuse_enabled:
        return False
    if _configured:
        return _available

    with _configuration_lock:
        if _configured:
            return _available
        try:
            if not settings.langfuse_public_key or not settings.langfuse_secret_key:
                logger.warning("Langfuse is enabled but credentials are incomplete; tracing remains disabled")
                return False

            from langfuse import get_client
            from openinference.instrumentation import TraceConfig
            from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
            from opentelemetry import trace as otel_trace
            from opentelemetry.sdk.trace import SpanProcessor

            class _ConfiguredModelProcessor(SpanProcessor):
                """Promote the configured model to Langfuse's first-class generation field."""

                def on_start(self, span, parent_context=None) -> None:
                    # OpenAI Agents may rename spans after start, so this cannot depend on
                    # the initial span name. Langfuse applies model names to generations.
                    span.set_attribute("langfuse.observation.model.name", settings.openai_model)

            privacy = TraceConfig(
                hide_inputs=not settings.langfuse_capture_content,
                hide_outputs=not settings.langfuse_capture_content,
                hide_llm_invocation_parameters=not settings.langfuse_capture_content,
                hide_llm_tools=not settings.langfuse_capture_content,
            )
            host = (settings.langfuse_base_url or "").strip() or "https://cloud.langfuse.com"
            environment = (settings.langfuse_tracing_environment or "").strip() or settings.app_env
            os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
            os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
            os.environ["LANGFUSE_BASE_URL"] = host
            os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = environment
            get_client()
            instrumentor = OpenAIAgentsInstrumentor()
            already_instrumented = bool(getattr(instrumentor, "_is_instrumented_by_opentelemetry", False))
            if not already_instrumented and _instrument_calls == 0:
                tracer_provider = otel_trace.get_tracer_provider()
                if hasattr(tracer_provider, "add_span_processor"):
                    tracer_provider.add_span_processor(_ConfiguredModelProcessor())
                instrumentor.instrument(config=privacy)
                _instrument_calls += 1
            _available = True
            return True
        except Exception:
            logger.exception("Optional Langfuse instrumentation could not be initialized")
            return False
        finally:
            _configured = True


@contextmanager
def langfuse_trace(
    settings: Settings,
    *,
    user_id: object,
    session_id: object,
    message: str,
    model: str,
) -> Iterator[LangfuseTrace]:
    """Create one trace per chat turn with nested agent, generation, and tool observations."""
    noop = LangfuseTrace()
    if not _configure(settings):
        yield noop
        return

    try:
        from langfuse import get_client, propagate_attributes

        langfuse = get_client()
        observation_context = langfuse.start_as_current_observation(
            as_type="agent",
            name=ORCHESTRATOR_TRACE_NAME,
            input=_trace_input(message, capture_content=settings.langfuse_capture_content),
        )
        attributes_context = propagate_attributes(
            trace_name=ORCHESTRATOR_TRACE_NAME,
            user_id=_anonymous_id(user_id),
            session_id=_anonymous_id(session_id),
            tags=["northstar", "chat", "orchestrator"],
            metadata=_string_metadata(
                {
                    "feature": "wealth-copilot-chat",
                    "channel": "streamlit-api",
                    "app_environment": settings.app_env,
                    "model": model,
                    "content_capture": "enabled" if settings.langfuse_capture_content else "disabled",
                    "langfuse_host_configured": bool((settings.langfuse_base_url or "").strip()),
                }
            ),
        )
    except Exception:
        logger.exception("Optional Langfuse trace context could not be created")
        yield noop
        return

    application_error: BaseException | None = None
    yielded = False
    try:
        with observation_context as observation:
            with attributes_context:
                try:
                    yielded = True
                    yield LangfuseTrace(
                        observation,
                        capture_content=settings.langfuse_capture_content,
                    )
                except BaseException as exc:
                    application_error = exc
                    raise
    except BaseException:
        # Application errors retain their original behavior. Telemetry failures remain optional.
        if application_error is not None:
            raise
        logger.exception("Optional Langfuse trace could not be finalized")
        if not yielded:
            yield noop
