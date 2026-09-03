"""Optional observability adapters. Observability must never own application behavior."""

from app.observability.langfuse import langfuse_trace

__all__ = ["langfuse_trace"]
