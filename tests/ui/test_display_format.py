from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.ui.display_format import (
    format_calendar_date,
    format_crypto_price,
    format_currency_amount,
    format_ordinary_currency,
    format_price,
    format_quantity,
    format_timestamp,
)

RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ETF_CANDIDATE = {
    "candidate_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "status": "APPROVED",
    "rank": 1,
    "symbol": "VTI",
    "account": "Alex Taxable Brokerage",
    "estimated_loss": "600.00000000",
    "asset_type": "ETF",
    "selected_quantity": "12.000000000000",
    "reference_price": "200.00000000",
    "quote_provider": "fake-alpha-vantage",
    "quote_feed": "iex",
}
CRYPTO_CANDIDATE = {
    "candidate_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    "status": "APPROVED",
    "rank": 2,
    "symbol": "DOGE/USD",
    "account": "Alex Taxable Brokerage",
    "estimated_loss": "12.50000000",
    "asset_type": "CRYPTO",
    "selected_quantity": "0.050000000000",
    "reference_price": "0.15000000",
    "quote_provider": "fake-coingecko",
    "quote_feed": "spot",
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


def _install_backend(monkeypatch, *, get_payloads: dict | None = None) -> MagicMock:
    payloads = get_payloads or {}
    posts = MagicMock(return_value=_FakeResponse(200, {"session_id": "orch"}))

    def fake_get(url: str, **_kwargs):
        path = url.split("localhost:8000", 1)[-1]
        if path.startswith("/api/orchestrator-sessions/active"):
            return _FakeResponse(404, text="Not found")
        for prefix, payload in payloads.items():
            if path.startswith(prefix):
                return _FakeResponse(200, payload)
        return _FakeResponse(200, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", posts)
    return posts


def _open_page(monkeypatch, page: str, **session) -> AppTest:
    _install_backend(monkeypatch, get_payloads=session.pop("get_payloads", None))
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    for key, value in session.items():
        at.session_state[key] = value
    at.run(timeout=20)
    at.sidebar.radio[0].set_value(page)
    at.run(timeout=20)
    return at


@pytest.mark.unit
def test_format_calendar_date_uses_iso_day():
    assert format_calendar_date("2024-06-15T14:55:00+00:00") == "2024-06-15"
    assert format_calendar_date("2024-06-15") == "2024-06-15"
    assert format_calendar_date(date(2024, 6, 15)) == "2024-06-15"
    assert format_calendar_date(datetime(2024, 6, 15, 14, 55, tzinfo=UTC)) == "2024-06-15"
    shifted = datetime(2024, 6, 15, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    assert format_calendar_date(shifted) == "2024-06-14"
    assert format_calendar_date(None) == ""


@pytest.mark.unit
def test_format_timestamp_uses_minute_utc():
    assert format_timestamp("2024-06-15T14:55:00+00:00") == "2024-06-15 14:55 UTC"
    assert format_timestamp("2024-06-15T14:55:00Z") == "2024-06-15 14:55 UTC"
    assert format_timestamp(datetime(2024, 6, 15, 14, 55, 59, tzinfo=UTC)) == "2024-06-15 14:55 UTC"
    shifted = datetime(2024, 6, 15, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    assert format_timestamp(shifted) == "2024-06-14 22:30 UTC"
    assert format_timestamp("2024-06-15") == "2024-06-15"
    assert format_timestamp(None) == ""


@pytest.mark.unit
def test_format_ordinary_currency_uses_two_decimal_places():
    assert format_ordinary_currency("600.00000000") == "600.00"
    assert format_ordinary_currency("12.5") == "12.50"
    assert format_ordinary_currency(Decimal("1234.5")) == "1234.50"
    assert format_ordinary_currency("-42.1") == "-42.10"
    assert format_ordinary_currency("0.01") == "0.01"
    assert "e" not in format_ordinary_currency("123456789012.34").lower()
    assert format_ordinary_currency(None) == ""


@pytest.mark.unit
def test_format_crypto_price_caps_at_six_places_and_strips_zeros():
    assert format_crypto_price("60000.00000000") == "60000"
    assert format_crypto_price("0.15000000") == "0.15"
    assert format_crypto_price("0.123456") == "0.123456"
    assert format_crypto_price("0.1234567") == "0.123457"
    assert format_crypto_price("0.000001") == "0.000001"
    assert "e" not in format_crypto_price("0.000001").lower()
    assert format_price("200.00000000", "ETF") == "200.00"
    assert format_price("0.15000000", "CRYPTO") == "0.15"
    assert format_price("185.00000000", "EQUITY") == "185.00"


@pytest.mark.unit
def test_format_quantity_strips_trailing_zeros_and_keeps_fractions():
    raw = Decimal("12.000000000000")
    assert format_quantity(raw) == "12"
    assert raw == Decimal("12.000000000000")
    assert format_quantity("0.050000000000") == "0.05"
    assert format_quantity("0.123456") == "0.123456"
    assert format_quantity("1E-8") == "0.00000001"
    assert "e" not in format_quantity("1E-8").lower()
    assert format_quantity(None) == ""
    assert format_currency_amount("42.10000000", "USD") == "USD 42.10"


@pytest.mark.unit
def test_holdings_table_formats_copy_of_api_rows(monkeypatch):
    holdings = [
        {
            "symbol": "VTI",
            "name": "Vanguard Total Stock",
            "asset_type": "ETF",
            "quantity": "12.000000000000",
            "account": "Alex Taxable Brokerage",
            "as_of": "2026-08-28T15:00:00+00:00",
        },
        {
            "symbol": "BTC/USD",
            "name": "Bitcoin",
            "asset_type": "CRYPTO",
            "quantity": "0.050000000000",
            "account": "Alex Taxable Brokerage",
            "as_of": "2026-08-28T15:00:00+00:00",
        },
    ]
    at = _open_page(
        monkeypatch,
        "Portfolio overview",
        get_payloads={
            "/api/holdings": {"holdings": holdings},
            "/api/portfolio-insights": {"portfolios": []},
        },
    )
    table = at.dataframe[0].value
    assert list(table["Quantity"]) == ["12", "0.05"]
    assert list(table["As of"]) == ["2026-08-28", "2026-08-28"]
    assert ".000" not in "".join(str(value) for value in table["Quantity"])


@pytest.mark.unit
def test_anomaly_table_formats_date_and_currency_copy(monkeypatch):
    anomalies = [
        {
            "date": "2026-08-15T00:00:00+00:00",
            "merchant": "Coffee Shop",
            "amount": "42.10000000",
            "currency": "USD",
            "normalized_score": "0.9123",
        }
    ]
    at = _open_page(
        monkeypatch,
        "Spending anomalies",
        get_payloads={"/api/anomalies": {"anomalies": anomalies}},
    )
    table = at.dataframe[0].value
    assert list(table["Date"]) == ["2026-08-15"]
    assert list(table["Amount"]) == ["USD 42.10"]


@pytest.mark.unit
def test_candidate_tables_format_copies_without_changing_session_state(monkeypatch):
    approved = [dict(ETF_CANDIDATE), dict(CRYPTO_CANDIDATE)]
    at = _open_page(
        monkeypatch,
        "Tax-loss candidates",
        analysis_run_id=RUN_ID,
        approved_candidates=approved,
        rejected_candidates=[],
    )
    table = at.dataframe[0].value
    assert list(table["Quantity"]) == ["12", "0.05"]
    assert list(table["Estimated loss"]) == ["600.00", "12.50"]
    assert list(table["Reference price"]) == ["200.00", "0.15"]
    stored = at.session_state.approved_candidates
    assert stored[0]["selected_quantity"] == "12.000000000000"
    assert stored[0]["estimated_loss"] == "600.00000000"
    assert stored[0]["reference_price"] == "200.00000000"
    assert stored[1]["selected_quantity"] == "0.050000000000"
    assert stored[1]["reference_price"] == "0.15000000"

    details = _open_page(
        monkeypatch,
        "Evaluation details",
        approved_candidates=[dict(ETF_CANDIDATE)],
        rejected_candidates=[],
    )
    fields = {row["Field"]: row["Value"] for _, row in details.dataframe[0].value.iterrows()}
    assert fields["Quantity"] == "12"
    assert fields["Reference price"] == "200.00"
    assert details.session_state.approved_candidates[0]["selected_quantity"] == "12.000000000000"
    assert details.session_state.approved_candidates[0]["reference_price"] == "200.00000000"
