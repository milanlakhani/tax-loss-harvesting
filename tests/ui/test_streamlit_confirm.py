from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from streamlit.testing.v1 import AppTest

RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
APPROVED = {
    "candidate_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "status": "APPROVED",
    "rank": 1,
    "symbol": "VTI",
    "account": "Alex Taxable Brokerage",
    "estimated_loss": "600.00",
    "asset_type": "ETF",
    "selected_quantity": "12",
    "reference_price": "200.00",
    "quote_provider": "fake-alpha-vantage",
}
PROTECTED = {
    "candidate_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "status": "NOT_EXECUTABLE",
    "rejection_code": "STALE_QUOTE",
    "explanation": "Quote exceeds configured freshness",
    "symbol": "AGG",
}

SNAPSHOT = {
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


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://localhost:8000/stub")
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> dict:
        return self._payload


def _install_backend(monkeypatch, *, run: dict | None = None, approved: list | None = None, rejected: list | None = None) -> MagicMock:
    posts = MagicMock(return_value=_FakeResponse(200, {"session_id": "orch"}))

    def fake_get(url: str, **_kwargs):
        path = url.split("localhost:8000", 1)[-1]
        if path.startswith("/api/orchestrator-sessions/active"):
            return _FakeResponse(404, text="Not found")
        if path == f"/api/analyses/{RUN_ID}":
            if run is None:
                return _FakeResponse(404, text="Not found")
            return _FakeResponse(200, run)
        if path == f"/api/analyses/{RUN_ID}/candidates/approved":
            return _FakeResponse(200, {"candidates": approved or []})
        if path == f"/api/analyses/{RUN_ID}/candidates/rejected":
            return _FakeResponse(200, {"candidates": rejected or []})
        return _FakeResponse(200, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", posts)
    return posts


def _open_paper_orders(**session) -> AppTest:
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    for key, value in session.items():
        at.session_state[key] = value
    at.run(timeout=10)
    at.sidebar.radio[0].set_value("Paper orders")
    at.run(timeout=10)
    return at


def _prepare_button(at: AppTest):
    return next(button for button in at.button if button.label == "Prepare paper order")


def _info_text(at: AppTest) -> str:
    return " ".join(str(message.value) for message in at.info)


def _error_text(at: AppTest) -> str:
    return " ".join(str(message.value) for message in at.error)


@pytest.mark.unit
def test_confirm_paper_sale_button_enforces_checkbox_and_guards(monkeypatch):
    _install_backend(monkeypatch)
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    at.session_state["prepared_snapshot"] = SNAPSHOT
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
    at2.session_state["prepared_snapshot"] = SNAPSHOT
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
def test_paper_orders_no_analysis_has_run(monkeypatch):
    _install_backend(monkeypatch)
    at = _open_paper_orders(approved_candidates=[])
    prepare = _prepare_button(at)
    assert prepare.disabled is True
    assert prepare.help == "A persisted APPROVED candidate is required before preparation."
    assert "Run Portfolio analysis first. Only opportunities that pass every safety rule can be prepared." in _info_text(at)
    assert "live market" not in _info_text(at).lower()
    assert not any(button.label == "Retry analysis" for button in at.button)


@pytest.mark.unit
def test_paper_orders_analysis_in_progress(monkeypatch):
    _install_backend(monkeypatch, run={"analysis_run_id": RUN_ID, "status": "RUNNING", "failure_reason": None})
    at = _open_paper_orders(analysis_run_id=RUN_ID, approved_candidates=[APPROVED])
    prepare = _prepare_button(at)
    assert prepare.disabled is True
    assert prepare.help == "A persisted APPROVED candidate is required before preparation."
    assert "Portfolio analysis is in progress." in _info_text(at)
    assert not at.selectbox
    assert "Run Portfolio analysis first" not in _info_text(at)


@pytest.mark.unit
def test_paper_orders_completed_with_zero_approved_candidates(monkeypatch):
    _install_backend(
        monkeypatch,
        run={"analysis_run_id": RUN_ID, "status": "COMPLETED", "failure_reason": None},
        approved=[],
        rejected=[PROTECTED],
    )
    at = _open_paper_orders(analysis_run_id=RUN_ID, approved_candidates=[APPROVED])
    prepare = _prepare_button(at)
    assert prepare.disabled is True
    assert prepare.help == "A persisted APPROVED candidate is required before preparation."
    assert (
        "Analysis completed, but no opportunities passed every safety rule. Review Protected decisions for the reasons."
        in _info_text(at)
    )
    assert "Run Portfolio analysis first" not in _info_text(at)
    assert not at.selectbox
    assert at.dataframe
    protected = at.dataframe[0].value
    assert list(protected["Symbol"]) == ["AGG"]
    assert "Stale Quote" in str(protected["Control"].iloc[0])


@pytest.mark.unit
def test_paper_orders_failed_analysis_shows_persisted_failure_and_retry(monkeypatch):
    posts = _install_backend(
        monkeypatch,
        run={"analysis_run_id": RUN_ID, "status": "FAILED", "failure_reason": "window sync failed"},
    )
    at = _open_paper_orders(analysis_run_id=RUN_ID)
    prepare = _prepare_button(at)
    assert prepare.disabled is True
    assert prepare.help == "A persisted APPROVED candidate is required before preparation."
    assert "The analysis stopped safely: window sync failed" in _error_text(at)
    retry = next(button for button in at.button if button.label == "Retry analysis")
    assert retry.disabled is False
    retry.click()
    at.run(timeout=10)
    assert any(
        str(call.args[0]).endswith("/api/analyses")
        for call in posts.call_args_list
        if call.args
    )


@pytest.mark.unit
def test_paper_orders_completed_with_approved_candidates_shows_selector(monkeypatch):
    _install_backend(
        monkeypatch,
        run={"analysis_run_id": RUN_ID, "status": "COMPLETED", "failure_reason": None},
        approved=[APPROVED],
        rejected=[PROTECTED],
    )
    at = _open_paper_orders(analysis_run_id=RUN_ID)
    prepare = _prepare_button(at)
    assert prepare.disabled is False
    assert not prepare.help
    assert at.selectbox
    assert "Approved opportunity" in at.selectbox[0].label
    assert "Run Portfolio analysis first" not in _info_text(at)
    assert at.dataframe
    protected = at.dataframe[0].value
    assert list(protected["Symbol"]) == ["AGG"]
