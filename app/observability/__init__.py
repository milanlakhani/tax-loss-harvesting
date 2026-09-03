"""Optional observability adapters. Observability must never own application behavior."""

from app.observability.langfuse import configure_langfuse, langfuse_trace

__all__ = ["configure_langfuse", "langfuse_trace"]
