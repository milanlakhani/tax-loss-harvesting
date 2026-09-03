from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.ui.statement_upload import collect_statement_pdfs
from app.ui.streamlit_app import BACKEND, _ingest_statement_pdf

REPO = Path(__file__).resolve().parents[2]
STREAMLIT_APP = (REPO / "app" / "ui" / "streamlit_app.py").read_text(encoding="utf-8")


class _Upload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


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


def _open_statement_upload(monkeypatch) -> AppTest:
    _install_backend(monkeypatch)
    at = AppTest.from_file("app/ui/streamlit_app.py")
    at.session_state["demo_session_token"] = "test-demo-session"
    at.session_state["orchestrator_session_id"] = "already-resumed"
    at.run(timeout=10)
    at.sidebar.radio[0].set_value("Statement upload")
    at.run(timeout=10)
    return at


@pytest.mark.unit
def test_statement_upload_page_has_no_folder_path(monkeypatch):
    at = _open_statement_upload(monkeypatch)
    captions = " ".join(str(item.value) for item in at.caption)
    assert "Ctrl/Shift" in captions
    assert "Ctrl+A" in captions
    assert "multiple PDF" in captions
    assert "folder path" not in captions.lower()
    assert not at.text_input
    assert "Ingest statements" in {button.label for button in at.button}


@pytest.mark.unit
def test_statement_upload_ui_uses_multi_file_uploader_not_host_path():
    assert "accept_multiple_files=True" in STREAMLIT_APP
    assert 'type=["pdf"]' in STREAMLIT_APP
    assert "Or ingest every PDF in a local folder" not in STREAMLIT_APP
    assert "collect_statement_pdfs(uploads=uploaded)" in STREAMLIT_APP
    assert "folder=folder" not in STREAMLIT_APP
    assert "Ctrl/Shift" in STREAMLIT_APP
    assert "Ctrl+A" in STREAMLIT_APP


@pytest.mark.unit
def test_mixed_selected_pdfs_are_collected_and_posted(monkeypatch):
    items, warnings = collect_statement_pdfs(
        uploads=[
            _Upload("BANK-0.pdf", b"%PDF-1.4 bank"),
            _Upload("BROKERAGE-A.pdf", b"%PDF-1.4 brokerage"),
        ]
    )
    assert warnings == []
    assert [name for name, _ in items] == ["BANK-0.pdf", "BROKERAGE-A.pdf"]

    posted: list[tuple[str, tuple]] = []

    class _Ok:
        status_code = 200

        def json(self):
            return {"status": "ingested", "format": "SYNTHETIC_BANK_V1", "statement_id": "s1"}

    def fake_post(url: str, **kwargs):
        posted.append((url, kwargs["files"]["file"]))
        return _Ok()

    monkeypatch.setattr("app.ui.streamlit_app.httpx.post", fake_post)
    monkeypatch.setattr("app.ui.streamlit_app._headers", lambda: {"X-Demo-Session": "test-demo-session"})
    results = [_ingest_statement_pdf(name, data) for name, data in items]
    assert [row["filename"] for row in results] == ["BANK-0.pdf", "BROKERAGE-A.pdf"]
    assert all(row["ok"] and row["status"] == "ingested" for row in results)
    assert [url for url, _file in posted] == [
        f"{BACKEND}/api/statements",
        f"{BACKEND}/api/statements",
    ]
    assert [file[0] for _url, file in posted] == ["BANK-0.pdf", "BROKERAGE-A.pdf"]
    assert all(file[2] == "application/pdf" for _url, file in posted)
