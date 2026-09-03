from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from streamlit.testing.v1 import AppTest

REPO = Path(__file__).resolve().parents[2]
STREAMLIT_APP = (REPO / "app" / "ui" / "streamlit_app.py").read_text(encoding="utf-8")


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


def _install_backend(monkeypatch) -> MagicMock:
    posts = MagicMock(return_value=_FakeResponse(200, {"session_id": "orch"}))

    def fake_get(url: str, **_kwargs):
        path = url.split("localhost:8000", 1)[-1]
        if path.startswith("/api/orchestrator-sessions/active"):
            return _FakeResponse(404, text="Not found")
        return _FakeResponse(200, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", posts)
    return posts


@pytest.mark.unit
def test_whatsapp_is_not_in_streamlit_navigation(monkeypatch):
    _install_backend(monkeypatch)
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    at.run(timeout=10)
    pages = list(at.sidebar.radio[0].options)
    assert "WhatsApp integration" not in pages
    assert "Paper orders" in pages
    assert "Portfolio drift" in pages
    assert "_whatsapp_link" not in STREAMLIT_APP
    assert "_whatsapp_qr" not in STREAMLIT_APP
    assert "import qrcode" not in STREAMLIT_APP


@pytest.mark.unit
def test_allocation_versus_target_bars_are_side_by_side():
    assert (
        'st.bar_chart(chart_rows, x="Asset class", y=["Current %", "Target %"], '
        'color=["#0a8f88", "#9bb4d3"], stack=False)'
    ) in STREAMLIT_APP
    assert "Current %" in STREAMLIT_APP
    assert "Target %" in STREAMLIT_APP
