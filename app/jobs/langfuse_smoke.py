"""python -m app.jobs.langfuse_smoke

Opt-in live check against the configured Langfuse project. Not part of pytest.
Does not print credentials, account IDs, or PDF contents.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from uuid import uuid4

from app.config import get_settings
from app.observability.langfuse import (
    ORCHESTRATOR_TRACE_NAME,
    _anonymous_id,
    configure_langfuse,
    langfuse_trace,
)

SMOKE_PROMPT = "Reply with the single word ok."
_POLL_SECONDS = 45.0


def _contains_secret(text: str, *secrets: str | None) -> bool:
    return any(secret and secret in text for secret in secrets)


def _public_host(base_url: str) -> str:
    return (base_url or "").strip() or "https://cloud.langfuse.com"


def _trace_matches(item: object, *, name: str, session_hash: str) -> bool:
    payload = getattr(item, "__dict__", None) or {}
    item_name = str(getattr(item, "name", "") or payload.get("name") or "")
    session = str(
        getattr(item, "session_id", None)
        or getattr(item, "sessionId", None)
        or payload.get("session_id")
        or payload.get("sessionId")
        or ""
    )
    return item_name == name and session == session_hash


def _list_traces(client) -> list:
    listing = client.api.trace.list(limit=50)
    return list(getattr(listing, "data", None) or [])


def wait_for_smoke_trace(client, *, session_hash: str, timeout: float = _POLL_SECONDS):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            for item in _list_traces(client):
                if _trace_matches(item, name=ORCHESTRATOR_TRACE_NAME, session_hash=session_hash):
                    return item
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    if last_error is not None:
        raise RuntimeError("Langfuse trace list failed after flush") from last_error
    raise RuntimeError("No matching Langfuse trace was visible after flush")


async def _run_synthetic_agent(settings, session_id: str) -> str:
    from agents import Agent, Runner

    with langfuse_trace(
        settings,
        user_id="langfuse-smoke-user",
        session_id=session_id,
        message=SMOKE_PROMPT,
        model=settings.openai_model,
    ) as trace:
        agent = Agent(
            name="LangfuseSmoke",
            instructions="You only reply with the single word ok.",
            model=settings.openai_model,
        )
        result = await Runner.run(agent, SMOKE_PROMPT)
        reply = str(result.final_output)
        trace.set_output(reply)
    return reply


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in Langfuse auth and trace export check")
    parser.parse_args(argv)
    settings = get_settings()
    secrets = (settings.langfuse_public_key, settings.langfuse_secret_key, settings.openai_api_key)
    host = _public_host(settings.langfuse_base_url)

    if not settings.langfuse_enabled:
        print("Langfuse smoke check skipped: LANGFUSE_ENABLED is not true.")
        return 2
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        print("Langfuse smoke check skipped: public/secret keys are incomplete.")
        return 2
    if not settings.openai_api_key:
        print("Langfuse smoke check skipped: OPENAI_API_KEY is required for the synthetic agent turn.")
        return 2

    if not configure_langfuse(settings):
        print("Langfuse smoke check failed: instrumentation did not start.")
        return 1

    from langfuse import get_client

    client = get_client()
    try:
        authenticated = client.auth_check()
    except Exception:
        print("Langfuse authentication failed. Credentials were not displayed.")
        return 1
    if not authenticated:
        print("Langfuse authentication failed. Credentials were not displayed.")
        return 1
    print(f"Langfuse authentication succeeded for host {host}.")

    session_id = f"langfuse-smoke-{uuid4()}"
    session_hash = _anonymous_id(session_id)
    try:
        asyncio.run(_run_synthetic_agent(settings, session_id))
        client.flush()
        item = wait_for_smoke_trace(client, session_hash=session_hash)
    except Exception as exc:
        message = str(exc)
        if _contains_secret(message, *secrets):
            print("Langfuse smoke check failed during export. Credentials were not displayed.")
        else:
            print(f"Langfuse smoke check failed during export: {message}")
        return 1

    trace_id = str(getattr(item, "id", None) or getattr(item, "trace_id", None) or "unknown")
    if _contains_secret(trace_id, *secrets):
        trace_id = "redacted"
    print(f"Langfuse smoke check exported trace {trace_id} named {ORCHESTRATOR_TRACE_NAME}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
