from __future__ import annotations


def confirm_button_enabled(
    *,
    checked: bool,
    paper_enabled: bool,
    prepared: bool,
    approved: bool,
    submitted: bool,
) -> bool:
    if submitted:
        return False
    if not checked:
        return False
    if not paper_enabled or not prepared or not approved:
        return False
    return True


def paper_submit_feedback(result: dict) -> tuple[str, str]:
    """Return (kind, message) for a paper-order confirm/refresh payload."""
    status = str(result.get("status") or "")
    if result.get("queued") or status == "QUEUED":
        return (
            "info",
            result.get("queue_reason")
            or "The US equity market is closed. This simulated paper order is queued for the next eligible trading session.",
        )
    return "success", f"Provider order {result.get('provider_order_id')} status {status}"
