from __future__ import annotations

import os
import secrets

import httpx
import streamlit as st

from app.ui.confirm_state import confirm_button_enabled

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
PAPER_BANNER = "SIMULATED PAPER TRADE - NO REAL MONEY"
DEFAULT_USER = "11111111-1111-4111-8111-111111111111"


def _ensure_demo_session() -> str:
    if "demo_session_token" not in st.session_state or not st.session_state.demo_session_token:
        st.session_state.demo_session_token = secrets.token_urlsafe(32)
        try:
            httpx.post(
                f"{BACKEND}/api/demo-sessions",
                json={"user_id": DEFAULT_USER, "token": st.session_state.demo_session_token},
                timeout=10.0,
            )
        except httpx.HTTPError:
            pass
    return st.session_state.demo_session_token


def _headers() -> dict[str, str]:
    return {"X-Demo-Session": _ensure_demo_session()}


def _get(path: str):
    try:
        response = httpx.get(f"{BACKEND}{path}", headers=_headers(), timeout=15.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"Backend unavailable: {exc}")
        return None


def _post(path: str, json: dict | None = None, files=None):
    try:
        response = httpx.post(f"{BACKEND}{path}", headers=_headers(), json=json, files=files, timeout=30.0)
        if response.status_code >= 400:
            st.error(response.text)
            return None
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"Backend unavailable: {exc}")
        return None


def _resume_orchestrator() -> None:
    if st.session_state.get("orchestrator_session_id"):
        return
    data = _get("/api/orchestrator-sessions/active")
    if data and data.get("session_id"):
        st.session_state.orchestrator_session_id = data["session_id"]
        return
    created = _post("/api/orchestrator-sessions", json={})
    if created:
        st.session_state.orchestrator_session_id = created.get("session_id")


def _reset_orchestrator() -> None:
    created = _post("/api/orchestrator-sessions/reset", json={})
    if created:
        st.session_state.orchestrator_session_id = created.get("session_id")
        st.session_state.chat_log = []


def render_confirm_panel(
    *,
    snapshot: dict | None,
    paper_enabled: bool,
    approved: bool,
    checkbox_key: str = "review_checkbox",
    submitted_key: str = "order_submitted",
) -> None:
    st.warning(PAPER_BANNER)
    if not snapshot:
        st.info("Prepare an approved candidate to see the read-only order snapshot.")
        return
    display = {key: value for key, value in snapshot.items() if key != "token"}
    st.subheader("Read-only order snapshot")
    st.json(display)
    checked = st.checkbox(
        "I reviewed the account, asset, SELL action, and quantity and understand that a simulated Alpaca paper order will be submitted.",
        value=False,
        key=checkbox_key,
    )
    enabled = confirm_button_enabled(
        checked=checked,
        paper_enabled=paper_enabled,
        prepared=bool(snapshot.get("token")),
        approved=approved,
        submitted=bool(st.session_state.get(submitted_key)),
    )

    def _mark_submitted() -> None:
        st.session_state[submitted_key] = True

    st.button(
        "Confirm paper sale",
        disabled=not enabled,
        key="confirm_paper_sale",
        on_click=_mark_submitted if enabled else None,
    )
    if st.session_state.get(submitted_key) and not st.session_state.get("confirm_request_sent"):
        st.session_state["confirm_request_sent"] = True
        st.info("Submitting simulated paper order…")
        result = _post(
            f"/api/candidates/{snapshot['candidate_id']}/confirm",
            json={"token": snapshot["token"]},
        )
        if result:
            st.session_state.last_order = result
            st.success(f"Provider order {result.get('provider_order_id')} status {result.get('status')}")
        else:
            st.error("Confirmation failed. Prepare again after correcting the error. No silent retry.")


def main() -> None:
    st.set_page_config(page_title="Tax-loss harvesting demo", layout="wide")
    st.title("Tax-loss harvesting demonstration")
    st.caption(
        "Server-bound demo session. This is not authentication. AWS ALB IP allowlisting is separate."
    )
    _ensure_demo_session()
    _resume_orchestrator()
    page = st.sidebar.radio(
        "Pages",
        [
            "Portfolio overview",
            "Bank statement upload",
            "Statement questions",
            "Spending anomalies",
            "Portfolio drift",
            "Tax-loss candidates",
            "Evaluation details",
            "Paper orders",
        ],
    )
    if st.sidebar.button("Reset conversation"):
        _reset_orchestrator()
    if st.sidebar.button("Close conversation"):
        _post("/api/orchestrator-sessions/close", json={})
        st.session_state.orchestrator_session_id = None

    if page == "Portfolio overview":
        st.write(_get("/api/holdings") or "Holdings are loaded from PostgreSQL on each request.")
        if st.button("Refresh holdings via MCP-backed API"):
            st.session_state.pop("holdings_cache", None)
        st.caption("Conversation memory is never used as financial source of truth.")
    elif page == "Bank statement upload":
        uploaded = st.file_uploader("Bank statement PDF", type=["pdf"])
        if uploaded and st.button("Ingest statement"):
            files = {"file": (uploaded.name, uploaded.getvalue(), "application/pdf")}
            st.write(_post("/api/statements", files=files))
    elif page == "Statement questions":
        question = st.text_input("Ask about statements (Enter sends chat, never confirms an order)")
        if st.button("Ask") and question:
            reply = _post("/api/orchestrator-sessions/chat", json={"message": question})
            st.write(reply)
            st.caption("Answers come from MCP tools on this request, not remembered tool output.")
    elif page == "Spending anomalies":
        st.write("Anomalies are loaded from persisted IsolationForest scores via the backend.")
    elif page == "Portfolio drift":
        st.write("Drift and warnings are computed by application services, not the UI.")
    elif page == "Tax-loss candidates":
        st.subheader("Approved")
        st.write(st.session_state.get("approved_candidates") or [])
        st.subheader("Rejected")
        st.write(st.session_state.get("rejected_candidates") or [])
    elif page == "Evaluation details":
        st.write(st.session_state.get("evaluation_details") or "Select a candidate after analysis.")
    elif page == "Paper orders":
        candidate_id = st.text_input("Candidate ID (server-issued)", key="prepare_candidate_id")
        if st.button("Prepare paper order") and candidate_id:
            prepared = _post(f"/api/candidates/{candidate_id}/prepare")
            if prepared:
                st.session_state.prepared_snapshot = prepared
                st.session_state.paper_enabled = bool(prepared.get("paper_orders_enabled"))
                st.session_state.candidate_approved = prepared.get("approval_status") == "APPROVED"
                st.session_state.order_submitted = False
        render_confirm_panel(
            snapshot=st.session_state.get("prepared_snapshot"),
            paper_enabled=bool(st.session_state.get("paper_enabled", False)),
            approved=bool(st.session_state.get("candidate_approved", False)),
        )
        last = st.session_state.get("last_order")
        if last and st.button("Refresh paper-order status"):
            st.write(_post(f"/api/paper-orders/{last['order_id']}/refresh"))


if __name__ == "__main__":
    main()
