from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.unit
def test_confirm_paper_sale_button_enforces_checkbox_and_guards():
    snapshot = {
        "candidate_id": "11111111-1111-4111-8111-111111111111",
        "token": "server-token",
        "side": "SELL",
        "symbol": "VTI",
        "asset_type": "ETF",
        "quantity": "12",
        "alpaca_alias": "conservative-demo",
        "reference_price": "200.00",
        "reference_timestamp": "2024-06-15T14:55:00+00:00",
        "estimated_proceeds": "2400.00",
        "basis": "3000.00",
        "estimated_loss": "600.00",
        "approval_status": "APPROVED",
        "environment": "SIMULATED PAPER TRADE - NO REAL MONEY",
    }
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    at.session_state["prepared_snapshot"] = snapshot
    at.session_state["paper_enabled"] = True
    at.session_state["candidate_approved"] = True
    at.session_state["order_submitted"] = False
    at.run(timeout=10)
    at.sidebar.radio[0].set_value("Paper orders")
    at.run(timeout=10)
    assert "SIMULATED PAPER TRADE - NO REAL MONEY" in at.warning[0].value
    confirm = next(b for b in at.button if b.label == "Confirm paper sale")
    assert confirm.disabled is True
    at.checkbox[0].check()
    at.run(timeout=10)
    confirm = next(b for b in at.button if b.label == "Confirm paper sale")
    assert confirm.disabled is False
    confirm.click()
    at.run(timeout=10)
    confirm = next(b for b in at.button if b.label == "Confirm paper sale")
    assert confirm.disabled is True

    at2 = AppTest.from_file("app/ui/streamlit_app.py")
    at2.session_state["demo_session_token"] = "test-demo-session"
    at2.session_state["orchestrator_session_id"] = "already-resumed"
    at2.session_state["prepared_snapshot"] = snapshot
    at2.session_state["paper_enabled"] = False
    at2.session_state["candidate_approved"] = True
    at2.run(timeout=10)
    at2.sidebar.radio[0].set_value("Paper orders")
    at2.run(timeout=10)
    at2.checkbox[0].check()
    at2.run(timeout=10)
    confirm2 = next(b for b in at2.button if b.label == "Confirm paper sale")
    assert confirm2.disabled is True


@pytest.mark.unit
def test_prepare_button_is_clearly_disabled_without_approved_candidate():
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    at.session_state["approved_candidates"] = []
    at.run(timeout=10)
    at.sidebar.radio[0].set_value("Paper orders")
    at.run(timeout=10)

    prepare = next(b for b in at.button if b.label == "Prepare paper order")
    assert prepare.disabled is True
    assert any("Run Portfolio analysis first" in message.value for message in at.info)
