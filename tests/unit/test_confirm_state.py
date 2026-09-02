from __future__ import annotations

from app.ui.confirm_state import confirm_button_enabled, paper_submit_feedback


def test_confirm_button_rules():
    assert confirm_button_enabled(checked=True, paper_enabled=True, prepared=True, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=False, paper_enabled=True, prepared=True, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=False, prepared=True, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=True, prepared=False, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=True, prepared=True, approved=False, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=True, prepared=True, approved=True, submitted=True)


def test_paper_submit_feedback_shows_queued_status():
    kind, message = paper_submit_feedback(
        {
            "status": "QUEUED",
            "queued": True,
            "provider_order_id": "abc",
            "queue_reason": "The US equity market is closed. This simulated paper order was accepted and is queued for the next eligible trading session.",
        }
    )
    assert kind == "info"
    assert "queued" in message.lower()
    success_kind, success_message = paper_submit_feedback(
        {"status": "SUBMITTED", "queued": False, "provider_order_id": "abc"}
    )
    assert success_kind == "success"
    assert "SUBMITTED" in success_message
