from __future__ import annotations

import os
import re
import secrets
from io import BytesIO
from html import escape
from urllib.parse import quote
from uuid import uuid4

import httpx
import qrcode
import streamlit as st

from app.ui.confirm_state import confirm_button_enabled, paper_submit_feedback
from app.ui.statement_upload import collect_statement_pdfs

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
PAPER_BANNER = "SIMULATED PAPER TRADE - NO REAL MONEY"
DEFAULT_USER = "11111111-1111-4111-8111-111111111111"


def _whatsapp_link(phone_number: str, message: str) -> str:
    """Build a safe click-to-chat URL without placing credentials in the QR."""
    digits = re.sub(r"\D", "", phone_number)
    if not digits:
        return ""
    return f"https://wa.me/{digits}?text={quote(message.strip())}"


def _whatsapp_qr(link: str) -> bytes:
    image = qrcode.make(link)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _inject_style() -> None:
    st.markdown(
        """
        <style>
        .stApp {background:#f2f7ff;color:#081426;}
        [data-testid="stHeader"] {background:#f2f7ff;}
        [data-testid="stAppViewContainer"] .main,
        [data-testid="stAppViewContainer"] .main h1,
        [data-testid="stAppViewContainer"] .main h2,
        [data-testid="stAppViewContainer"] .main h3,
        [data-testid="stAppViewContainer"] .main p,
        [data-testid="stAppViewContainer"] .main label,
        [data-testid="stAppViewContainer"] .main [data-testid="stMarkdown"],
        [data-testid="stAppViewContainer"] .main [data-testid="stCaptionContainer"],
        [data-testid="stAppViewContainer"] .main [data-testid="stCaptionContainer"] *,
        [data-testid="stAppViewContainer"] .main [data-testid="stFileUploader"] label,
        [data-testid="stAppViewContainer"] .main [data-testid="stFileUploader"] p,
        [data-testid="stAppViewContainer"] .main [data-testid="stFileUploader"] small,
        [data-testid="stAppViewContainer"] .main [data-testid="stTextInput"] label,
        [data-testid="stAppViewContainer"] .main [data-testid="stWidgetLabel"] p {
            color:#081426;
        }
        [data-testid="stAppViewContainer"] .main .hero,
        [data-testid="stAppViewContainer"] .main .hero h1 {color:#ffffff;}
        [data-testid="stAppViewContainer"] .main .hero p {color:#d8e6ff;}
        [data-testid="stAppViewContainer"] .main .eyebrow {color:#62e6d3;}
        [data-testid="stSidebar"] {background:#081426; color:#fff; border-right:1px solid #17263d;}
        [data-testid="stSidebar"] * {color:#e8efff;}
        [data-testid="stSidebar"] [role="radiogroup"] label {
            padding:.5rem .7rem;border-radius:11px;cursor:pointer;
            transition:background .14s ease,transform .14s ease,box-shadow .14s ease;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#132542;transform:translateX(2px);}
        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background:linear-gradient(100deg,#18375d,#0d6567);
            box-shadow:inset 3px 0 0 #62e6d3,0 6px 15px rgba(0,0,0,.16);
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {color:#ffffff;font-weight:750;}
        [data-testid="stSidebar"] .stButton>button {
            width:100%; background:#132542; color:#f4f8ff !important;
            border:1px solid #385477; border-radius:11px; box-shadow:none;
        }
        [data-testid="stSidebar"] .stButton>button:hover {
            background:#1a3960; color:#ffffff !important; border-color:#62e6d3;
        }
        [data-testid="stSidebar"] .stButton>button:focus {
            color:#ffffff !important; border-color:#62e6d3;
            box-shadow:0 0 0 2px rgba(98,230,211,.22);
        }
        [data-testid="stSidebar"] .stButton>button:disabled {
            background:#111f34; color:#8495ad !important; border-color:#263a55;
        }
        .hero {padding:1.55rem 1.7rem;border-radius:22px;background:linear-gradient(120deg,#081426,#12376b 65%,#0a8f88);color:white;box-shadow:0 16px 40px rgba(8,20,38,.18);margin-bottom:1.2rem;}
        .hero h1 {font-size:2.15rem;margin:0 0 .35rem;color:white;letter-spacing:-.03em;}
        .hero p {margin:0;color:#d8e6ff;font-size:1.02rem;}
        .eyebrow {font-size:.75rem;text-transform:uppercase;letter-spacing:.14em;color:#62e6d3;font-weight:700;margin-bottom:.55rem;}
        .status-pill {display:inline-block;padding:.28rem .62rem;border-radius:999px;background:#dff8ef;color:#08715c;font-weight:700;font-size:.78rem;margin:.2rem .2rem .2rem 0;}
        .soft-card {background:rgba(255,255,255,.88);border:1px solid #dfe8f6;border-radius:16px;padding:1rem 1.15rem;box-shadow:0 8px 24px rgba(28,55,90,.06);margin:.45rem 0 1rem;}
        .section-note {color:#5f6f85;font-size:.92rem;}
        .integration-card {
            padding:1.3rem 1.4rem;border-radius:20px;background:rgba(255,255,255,.96);
            border:1px solid #d8e3f2;box-shadow:0 10px 28px rgba(28,55,90,.075);
        }
        .integration-card h3 {margin:.1rem 0 .45rem;color:#202c3e;}
        .integration-card p {margin:.2rem 0;color:#617086;}
        .integration-badge {
            display:inline-block;margin-bottom:.7rem;padding:.3rem .65rem;border-radius:999px;
            background:#dcf8e8;color:#08715c;font-size:.76rem;font-weight:750;
        }
        .status-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1.25rem;margin:1.15rem 0 1.35rem;}
        .status-grid--2 {grid-template-columns:repeat(2,minmax(0,1fr));}
        .status-grid--4 {grid-template-columns:repeat(4,minmax(0,1fr));}
        .status-card {
            position:relative;min-height:146px;padding:1.25rem 1.35rem;border-radius:20px;
            background:rgba(255,255,255,.96);border:1px solid #d8e3f2;
            box-shadow:0 10px 28px rgba(28,55,90,.075);cursor:default;overflow:hidden;
            transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease;
        }
        .status-card::before {content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:#6e87a8;}
        .status-card::after {content:"";position:absolute;right:-34px;top:-38px;width:110px;height:110px;border-radius:50%;background:rgba(110,135,168,.08);}
        .status-card:hover {transform:translateY(-3px);box-shadow:0 16px 36px rgba(28,55,90,.13);border-color:#aec4df;}
        .status-card--success::before {background:#0a9b8f;}
        .status-card--success::after {background:rgba(10,155,143,.10);}
        .status-card--warning::before {background:#e39a26;}
        .status-card--warning::after {background:rgba(227,154,38,.12);}
        .status-card__label {position:relative;z-index:1;color:#617086;font-size:.9rem;font-weight:650;margin-bottom:1rem;}
        .status-card__value {
            position:relative;z-index:1;min-width:0;max-width:100%;color:#202c3e;
            min-height:4.25rem;font-size:clamp(1.55rem,2vw,1.85rem);font-weight:650;
            line-height:1.15;letter-spacing:-.015em;white-space:normal;
            word-break:normal;overflow-wrap:normal;
        }
        .status-card__meta {position:relative;z-index:1;color:#78869a;font-size:.78rem;margin-top:.65rem;}
        div[data-testid="stMetric"] {
            background:rgba(255,255,255,.96);border:1px solid #d8e3f2;
            padding:1.05rem 1.15rem;border-radius:18px;
            box-shadow:0 10px 28px rgba(28,55,90,.075);
            height:132px;min-height:132px;overflow:hidden;
            transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease;
        }
        div[data-testid="stMetric"]:hover {
            transform:translateY(-2px);border-color:#a9c4e6;
            box-shadow:0 14px 34px rgba(28,55,90,.12);
        }
        div[data-testid="stMetricLabel"] {font-weight:650;color:#53647b;min-height:1.55rem;}
        div[data-testid="stMetricValue"] {font-size:clamp(1.65rem,2.25vw,2.2rem);line-height:1.12;margin-top:.4rem;}
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] * {
            white-space:normal !important;overflow:visible !important;
            text-overflow:clip !important;overflow-wrap:normal !important;
        }
        div[data-testid="stDataFrame"] {border:1px solid #dfe8f6;border-radius:14px;overflow:hidden;}
        .stButton>button {
            min-height:3.15rem;height:3.15rem;padding:.65rem 1.2rem;border-radius:12px;
            font-weight:700;white-space:normal;line-height:1.2;font-size:.96rem;
            box-sizing:border-box;
            border:1px solid #c8d6e8;background:#ffffff;color:#163252;
            box-shadow:0 4px 12px rgba(23,50,82,.07);
            cursor:pointer;outline:none;
            transition:transform .14s ease,box-shadow .14s ease,border-color .14s ease,background .14s ease,color .14s ease;
        }
        .stButton>button p {margin:0;line-height:1.2;}
        [class*="st-key-suggestion_"] .stButton>button,
        [class*="st-key-suggestion_"] button {
            position:relative;width:100%;height:8.4rem !important;min-height:8.4rem !important;
            max-height:8.4rem !important;padding:1.2rem 1.15rem 1rem 1.3rem;box-sizing:border-box;
            justify-content:flex-start;text-align:left;font-size:1rem;overflow:hidden;
            background:rgba(255,255,255,.96);border-color:#d8e3f2;border-radius:20px;
            box-shadow:0 10px 28px rgba(28,55,90,.075);
        }
        [class*="st-key-suggestion_"] button::after {
            content:"";position:absolute;right:-34px;top:-38px;width:110px;height:110px;
            border-radius:50%;background:rgba(10,143,136,.08);pointer-events:none;
        }
        [class*="st-key-suggestion_"] button p {
            position:relative;z-index:1;display:block;width:100%;margin:0;
            white-space:normal !important;overflow:visible !important;
            text-overflow:clip !important;line-height:1.28;font-weight:700;
        }
        [class*="st-key-suggestion_"] button p::before {
            display:block;margin-bottom:.65rem;color:#6b7c92;font-size:.68rem;
            font-weight:800;letter-spacing:.11em;text-transform:uppercase;line-height:1;
        }
        .st-key-suggestion_0 button {border-left:5px solid #e39a26 !important;}
        .st-key-suggestion_0 button p::before {content:"Spending signals";}
        .st-key-suggestion_0 button::after {background:rgba(227,154,38,.11);}
        .st-key-suggestion_1 button {border-left:5px solid #0a9b8f !important;}
        .st-key-suggestion_1 button p::before {content:"Risk controls";}
        .st-key-suggestion_2 button {border-left:5px solid #5279aa !important;}
        .st-key-suggestion_2 button p::before {content:"Allocation drift";}
        .st-key-suggestion_2 button::after {background:rgba(82,121,170,.10);}
        .st-key-suggestion_3 button {border-left:5px solid #0a9b8f !important;}
        .st-key-suggestion_3 button p::before {content:"Tax safety";}
        .stButton>button:hover {
            color:#0a706c;border-color:#1bb6aa;background:#f5fffd;
            box-shadow:0 7px 18px rgba(10,112,108,.13);transform:translateY(-1px);
        }
        .stButton>button:active {
            transform:translateY(1px) scale(.99);box-shadow:0 2px 7px rgba(10,112,108,.14);
        }
        .stButton>button:focus {outline:none;}
        .stButton>button:focus-visible {
            outline:3px solid rgba(27,182,170,.28);outline-offset:2px;border-color:#0a8f88;
            box-shadow:0 0 0 1px #0a8f88;
        }
        .stButton>button:disabled {
            cursor:not-allowed;transform:none;box-shadow:none;opacity:.62;
            background:#edf1f6;color:#7b8798;border-color:#d6dee9;
        }
        .stButton>button[kind="primary"] {
            background:linear-gradient(120deg,#12376b,#0a8f88);color:#ffffff;
            border-color:transparent;box-shadow:0 8px 20px rgba(18,55,107,.2);
        }
        .stButton>button[kind="primary"]:hover {
            background:linear-gradient(120deg,#0d2d5c,#087b75);color:#ffffff;
            box-shadow:0 10px 24px rgba(10,93,112,.26);
        }
        [class*="st-key-suggestion_"] button[kind="primary"] {
            background:linear-gradient(135deg,#12376b,#0a8f88);color:#ffffff;
            border-color:transparent;box-shadow:0 10px 24px rgba(10,112,108,.22);
        }
        [class*="st-key-suggestion_"] button[kind="primary"] p,
        [class*="st-key-suggestion_"] button[kind="primary"] p::before {color:#ffffff;}
        [class*="st-key-suggestion_"] button[kind="primary"]::after {background:rgba(255,255,255,.09);}
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stCheckbox"] label,
        [data-testid="stExpander"] summary {cursor:pointer;}
        [data-testid="stFormSubmitButton"]>button {
            min-width:12rem;height:3.15rem;border-radius:12px;font-weight:700;
        }
        @media (max-width:900px) {
            .status-grid {grid-template-columns:1fr;gap:.8rem;}
            .status-card {min-height:118px;}
            div[data-testid="stMetric"] {height:116px;min-height:116px;}
            div[data-testid="stMetricValue"] {font-size:1.7rem;}
            [class*="st-key-suggestion_"] button {
                height:7.25rem !important;min-height:7.25rem !important;max-height:7.25rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _hero() -> None:
    st.markdown(
        """<div class="hero"><div class="eyebrow">Portfolio Intelligence · Paper Trading</div>
        <h1>Northstar Wealth Copilot</h1>
        <p>Spending intelligence, portfolio risk and tax-loss opportunities—with deterministic safety controls.</p></div>""",
        unsafe_allow_html=True,
    )


def _friendly_code(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("_", " ").title()


def _percent(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _currency(value, currency: str = "USD") -> str:
    try:
        return f"{currency} {float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _compact_currency(value, currency: str = "USD") -> str:
    """Keep headline currency metrics readable without losing the exact tooltip value."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    absolute = abs(amount)
    if absolute >= 1_000_000_000:
        display = f"{amount / 1_000_000_000:.1f}B"
    elif absolute >= 1_000_000:
        display = f"{amount / 1_000_000:.1f}M"
    elif absolute >= 1_000:
        display = f"{amount / 1_000:.1f}K"
    else:
        display = f"{amount:,.2f}"
    return f"{currency} {display}"


def _status_cards(cards: list[dict]) -> None:
    """Render one consistent, non-clickable summary-card system across the app."""
    count = min(max(len(cards), 1), 4)
    grid_class = f" status-grid--{count}" if count in {2, 4} else ""
    blocks = []
    for card in cards:
        variant = card.get("variant") if card.get("variant") in {"success", "warning"} else ""
        variant_class = f" status-card--{variant}" if variant else ""
        display_value = str(card.get("value") if card.get("value") is not None else "—")
        blocks.append(
            f'''<div class="status-card{variant_class}" title="{escape(str(card.get('title') or card.get('meta') or 'Summary'))}">
              <div class="status-card__label">{escape(str(card.get('label') or 'Summary'))}</div>
              <div class="status-card__value">{escape(display_value)}</div>
              <div class="status-card__meta">{escape(str(card.get('meta') or 'Current persisted value'))}</div>
            </div>'''
        )
    st.markdown(f'<div class="status-grid{grid_class}">{"".join(blocks)}</div>', unsafe_allow_html=True)


def _friendly_chat(reply: dict | None) -> None:
    if not reply:
        return
    text = str(reply.get("reply") or "")
    if reply.get("mode") == "llm":
        st.markdown(text)
        st.caption("AI-assisted explanation · Financial values retrieved from application tools")
        return
    value = re.search(r"value=Decimal\('([^']+)'\)", text)
    currency = re.search(r"currency='([^']+)'", text)
    count = re.search(r"transaction_count=(\d+)", text)
    if value and currency:
        st.success("Authoritative answer retrieved")
        _status_cards([
            {"label": "Calculated value", "value": f"{currency.group(1)} {float(value.group(1)):,.2f}", "meta": "Authoritative application result", "variant": "success"},
            {"label": "Transactions analysed", "value": count.group(1) if count else "—", "meta": "Included in this answer"},
        ])
    else:
        st.info(text or "No result returned.")
    with st.expander("Technical audit details"):
        st.json(reply)


def _ask_financial_question(question: str) -> None:
    if not question.strip():
        return
    log = st.session_state.setdefault("chat_log", [])
    log.append({"role": "user", "content": question.strip()})
    reply = _post("/api/orchestrator-sessions/chat", json={"message": question.strip()})
    if reply:
        log.append(
            {
                "role": "assistant",
                "content": str(reply.get("reply") or "No answer returned."),
                "mode": reply.get("mode", "deterministic_fallback"),
            }
        )
    else:
        log.append({"role": "assistant", "content": "I could not reach the financial service. Please try again.", "mode": "error"})


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


def _post(path: str, json: dict | None = None, files=None, *, timeout: float = 30.0):
    try:
        response = httpx.post(f"{BACKEND}{path}", headers=_headers(), json=json, files=files, timeout=timeout)
        if response.status_code >= 400:
            st.error(response.text)
            return None
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"Backend unavailable: {exc}")
        return None


def _ingest_statement_pdf(filename: str, data: bytes) -> dict:
    try:
        response = httpx.post(
            f"{BACKEND}/api/statements",
            headers=_headers(),
            files={"file": (filename, data, "application/pdf")},
            timeout=60.0,
        )
        if response.status_code >= 400:
            return {"filename": filename, "ok": False, "status": "failed", "error": response.text}
        payload = response.json()
        return {"filename": filename, "ok": True, **payload}
    except httpx.HTTPError as exc:
        return {"filename": filename, "ok": False, "status": "failed", "error": f"Backend unavailable: {exc}"}


def _resume_orchestrator() -> None:
    if st.session_state.get("orchestrator_session_id"):
        return
    try:
        response = httpx.get(f"{BACKEND}/api/orchestrator-sessions/active", headers=_headers(), timeout=15.0)
        if response.status_code == 404:
            data = None
        else:
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        st.error(f"Backend unavailable: {exc}")
        data = None
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


def _load_analysis_candidates(analysis_run_id: str) -> None:
    approved = _get(f"/api/analyses/{analysis_run_id}/candidates/approved")
    rejected = _get(f"/api/analyses/{analysis_run_id}/candidates/rejected")
    if approved is not None:
        st.session_state.approved_candidates = approved.get("candidates", [])
    if rejected is not None:
        st.session_state.rejected_candidates = rejected.get("candidates", [])


PREPARE_BUTTON_LABEL = "Prepare paper order"
PREPARE_DISABLED_HELP = "A persisted APPROVED candidate is required before preparation."
_RUNNING_ANALYSIS_STATUSES = frozenset({"PENDING", "RUNNING"})


def _persisted_analysis_snapshot(run_id: str | None) -> dict:
    """Load run status and candidate counts from the backend, not session cache."""
    empty = {"kind": "none", "run": None, "approved": [], "rejected": []}
    if not run_id:
        return empty
    run = _get(f"/api/analyses/{run_id}")
    if run is None:
        return {"kind": "unavailable", "run": None, "approved": [], "rejected": []}
    status = str(run.get("status") or "").upper()
    if status in _RUNNING_ANALYSIS_STATUSES:
        return {"kind": "running", "run": run, "approved": [], "rejected": []}
    if status == "FAILED":
        return {"kind": "failed", "run": run, "approved": [], "rejected": []}
    if status != "COMPLETED":
        return {"kind": "unavailable", "run": run, "approved": [], "rejected": []}
    approved_payload = _get(f"/api/analyses/{run_id}/candidates/approved")
    rejected_payload = _get(f"/api/analyses/{run_id}/candidates/rejected")
    if approved_payload is None or rejected_payload is None:
        return {"kind": "unavailable", "run": run, "approved": [], "rejected": []}
    approved = [row for row in approved_payload.get("candidates", []) if row.get("status") == "APPROVED"]
    rejected = rejected_payload.get("candidates", [])
    kind = "completed_approved" if approved else "completed_none"
    return {"kind": kind, "run": run, "approved": approved, "rejected": rejected}


def _render_protected_decisions(rejected: list[dict]) -> None:
    st.subheader("Protected decisions")
    if rejected:
        st.dataframe(
            [
                {
                    "Symbol": item.get("symbol"),
                    "Status": _friendly_code(item.get("status")),
                    "Control": _friendly_code(item.get("rejection_code")),
                    "Why it matters": item.get("explanation"),
                    "Candidate ID": item.get("candidate_id"),
                }
                for item in rejected
            ],
            use_container_width=True,
            hide_index=True,
        )


def _retry_analysis_from_paper_orders() -> None:
    result = _post(
        "/api/analyses",
        json={"idempotency_key": f"streamlit-{uuid4().hex}", "trigger": "API"},
        timeout=300.0,
    )
    if not result:
        return
    st.session_state.analysis_attempt = result
    run_id = result.get("analysis_run_id")
    if run_id:
        st.session_state.analysis_run_id = run_id
    if result.get("status") == "COMPLETED" and run_id:
        st.session_state.analysis_result = result
        _load_analysis_candidates(run_id)
    elif result.get("status") == "FAILED":
        st.session_state.analysis_result = None


def _analysis_failure_message(reason: str | None) -> str:
    if reason and "lock:" in reason:
        return "Another analysis was already running for this portfolio. Its safety lock prevented an overlapping run. Please run the analysis again."
    if reason:
        return f"The analysis stopped safely: {reason}"
    return "The analysis stopped safely before producing recommendations. Please try again or review the backend logs."


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
    if snapshot.get("quote_context") == "MARKET_CLOSED_USING_LAST_PRICE":
        st.info(
            "US equity market is closed. The reference price is the latest Alpaca trade from the most recently completed session. This is informational context, not a stale-quote rejection."
        )
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
            kind, message = paper_submit_feedback(result)
            if kind == "info":
                st.info(message)
            st.success(f"Provider order {result.get('provider_order_id')} status {result.get('status')}")
        else:
            st.error("Confirmation failed. Prepare again after correcting the error. No silent retry.")


def main() -> None:
    st.set_page_config(page_title="Tax-loss harvesting demo", layout="wide")
    _inject_style()
    _hero()
    _ensure_demo_session()
    _resume_orchestrator()
    page = st.sidebar.radio(
        "Pages",
        [
            "Portfolio overview",
            "Statement upload",
            "Statement questions",
            "Spending anomalies",
            "Portfolio analysis",
            "Portfolio drift",
            "Tax-loss candidates",
            "Evaluation details",
            "Paper orders",
            "WhatsApp integration",
        ],
    )
    st.sidebar.markdown("---")
    st.sidebar.markdown("**System status**")
    st.sidebar.markdown('<span class="status-pill">● API online</span><span class="status-pill">Paper only</span>', unsafe_allow_html=True)
    st.sidebar.caption("Demo user · Alex Morgan\n\nSafety rules · harvest_gates_v1")
    if st.sidebar.button("Reset conversation"):
        _reset_orchestrator()
    if st.sidebar.button("Close conversation"):
        _post("/api/orchestrator-sessions/close", json={})
        st.session_state.orchestrator_session_id = None

    if page == "Portfolio overview":
        st.header("Portfolio overview")
        st.caption("A clear view of the current synthetic portfolio used for the paper-trading demonstration.")
        data = _get("/api/holdings") or {"holdings": []}
        holdings = data.get("holdings", [])
        insights = (_get("/api/portfolio-insights") or {}).get("portfolios", [])
        total_value = sum(float(item.get("total_value") or 0) for item in insights)
        _status_cards([
            {"label": "Positions", "value": len(holdings), "meta": "Current holdings"},
            {"label": "Asset classes", "value": len({h.get('asset_type') for h in holdings}), "meta": "Portfolio diversification", "variant": "success"},
            {"label": "Estimated value", "value": _compact_currency(total_value), "meta": f"Full value: {_currency(total_value)}"},
            {"label": "Taxable accounts", "value": len({h.get('portfolio_id') for h in holdings}), "meta": "Eligible account scope"},
        ])
        st.subheader("Holdings")
        if holdings:
            st.dataframe(
                [{"Symbol":h.get("symbol"),"Name":h.get("name"),"Type":_friendly_code(h.get("asset_type")),"Quantity":h.get("quantity"),"Account":h.get("account"),"As of":h.get("as_of","")[:10]} for h in holdings],
                use_container_width=True,
                hide_index=True,
            )
        st.caption("Authoritative values are reloaded from PostgreSQL; conversation memory is never a financial source of truth.")
    elif page == "Statement upload":
        st.header("Bank and brokerage statement upload")
        st.caption(
            "Upload multiple PDF files. Bank and brokerage statements can be selected together; "
            "the parser is detected automatically from each document. In the file-selection window, "
            "use Ctrl/Shift or Ctrl+A to select multiple PDFs."
        )
        with st.form("statement_upload_form", clear_on_submit=True):
            uploaded = st.file_uploader(
                "Statement PDFs",
                type=["pdf"],
                accept_multiple_files=True,
                help=(
                    "Use Ctrl/Shift or Ctrl+A in the file-selection window to select multiple PDFs. "
                    "Bank and brokerage statements can be mixed."
                ),
            )
            submitted = st.form_submit_button("Ingest statements")

        if submitted:
            items, warnings = collect_statement_pdfs(uploads=uploaded)
            for warning in warnings:
                st.warning(warning)
            if not items:
                st.warning("Choose one or more PDF files.")
            else:
                results = [_ingest_statement_pdf(name, data) for name, data in items]
                ingested = sum(1 for row in results if row.get("status") == "ingested")
                duplicates = sum(1 for row in results if row.get("status") == "duplicate")
                failed = sum(1 for row in results if not row.get("ok"))
                banks = sum(1 for row in results if row.get("format") == "SYNTHETIC_BANK_V1")
                brokerages = sum(1 for row in results if row.get("format") == "SYNTHETIC_BROKERAGE_V1")
                _status_cards([
                    {"label": "Imported", "value": ingested, "meta": "New statements persisted", "variant": "success" if ingested else None},
                    {"label": "Already imported", "value": duplicates, "meta": "Duplicates were skipped"},
                    {"label": "Bank / brokerage", "value": f"{banks} / {brokerages}", "meta": "Detected parser formats"},
                    {"label": "Failed", "value": failed, "meta": "Rejected or unreachable", "variant": "warning" if failed else None},
                ])
                st.dataframe(
                    [
                        {
                            "File": row.get("filename"),
                            "Type": _friendly_code(row.get("format")),
                            "Result": _friendly_code(row.get("status")),
                            "Detail": row.get("error") or row.get("statement_id") or "",
                        }
                        for row in results
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
                if failed:
                    st.error("Some statements could not be imported. Check the detail column.")
                elif ingested:
                    st.success("Statements imported. Transactions and lots are available for questions and analysis.")
                elif duplicates:
                    st.info("These statements were already imported, so no rows were duplicated.")
    elif page == "Statement questions":
        st.header("Ask your financial data")
        st.caption("Ask naturally about statement-derived spending, anomaly signals, portfolio risk, allocation drift, and safely evaluated tax-loss opportunities.")
        st.markdown("**Try one of these questions**")
        suggestions = [
            "Show my unusual spending.",
            "Is my portfolio within its risk limits?",
            "How far is my portfolio from its target allocation?",
            "Do I have any safe tax-loss opportunities?",
        ]
        cols = st.columns(4)
        for index, suggestion in enumerate(suggestions):
            selected = st.session_state.get("selected_suggestion") == index
            if cols[index].button(
                suggestion,
                key=f"suggestion_{index}",
                use_container_width=True,
                type="primary" if selected else "secondary",
            ):
                st.session_state.selected_suggestion = index
                _ask_financial_question(suggestion)
                st.rerun()
        for item in st.session_state.get("chat_log", []):
            with st.chat_message(item.get("role", "assistant")):
                st.markdown(item.get("content", ""))
                if item.get("role") == "assistant":
                    if item.get("mode") == "llm":
                        st.caption("AI-assisted explanation · Values retrieved from authoritative tools")
                    elif item.get("mode") == "deterministic_fallback":
                        st.caption("Deterministic fallback · Values retrieved directly from application tools")
        question = st.chat_input("Ask about spending, income, holdings, anomalies, or analysis")
        if question:
            _ask_financial_question(question)
            st.rerun()
        st.info("Safety boundary: AI can explain and retrieve information, but cannot approve or submit an order.")
    elif page == "Spending anomalies":
        st.header("Spending anomalies")
        st.caption("Unusual transactions identified by the persisted Isolation Forest model.")
        anomalies = (_get("/api/anomalies") or {}).get("anomalies", [])
        model_status = anomalies[0].get("ml_status") if anomalies else "Run analysis"
        review_status = "Attention needed" if anomalies else "No flags"
        review_class = "warning" if anomalies else "success"
        _status_cards([
            {"label": "Flagged transactions", "value": f"{len(anomalies):,}", "meta": "Persisted review signals"},
            {"label": "Model", "value": _friendly_code(model_status), "meta": "Isolation Forest analysis", "variant": "success"},
            {"label": "Review status", "value": review_status, "meta": "Signals are not proof of fraud", "variant": review_class},
        ])
        if anomalies:
            st.dataframe(
                [{"Date":a.get("date"),"Merchant":a.get("merchant"),"Amount":f"{a.get('currency','')} {a.get('amount','')}","Anomaly score":round(float(a.get("normalized_score",0)),3),"Status":"Review"} for a in anomalies],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Run portfolio analysis to generate anomaly scores.")
    elif page == "Portfolio analysis":
        st.header("Portfolio analysis")
        st.caption("One controlled run evaluates spending, drift, tax lots, wash-sale conflicts and risk limits.")
        st.markdown('<div class="soft-card"><b>What happens in this run</b><br><span class="section-note">Live or mocked quotes → anomaly scoring → portfolio risk → candidate generation → deterministic safety evaluation.</span></div>', unsafe_allow_html=True)
        if st.button("Run analysis", type="primary"):
            with st.spinner("Running anomaly, drift, harvesting, and safety evaluation…"):
                result = _post(
                    "/api/analyses",
                    json={"idempotency_key": f"streamlit-{uuid4().hex}", "trigger": "API"},
                    timeout=300.0,
                )
            if result:
                run_id = result["analysis_run_id"]
                st.session_state.analysis_attempt = result
                if result.get("status") == "COMPLETED":
                    st.session_state.analysis_run_id = run_id
                    st.session_state.analysis_result = result
                    _load_analysis_candidates(run_id)
                    st.success(f"Analysis completed: {run_id}")
                else:
                    st.error(_analysis_failure_message(result.get("failure_reason")))
        attempt = st.session_state.get("analysis_attempt")
        result = attempt or st.session_state.get("analysis_result")
        if result:
            completed = result.get("status") == "COMPLETED"
            approved = st.session_state.get('approved_candidates', []) if completed else []
            rejected = st.session_state.get('rejected_candidates', []) if completed else []
            _status_cards([
                {"label": "Run status", "value": "Complete" if completed else _friendly_code(result.get("status")), "meta": "Persisted pipeline result", "variant": "success" if completed else "warning"},
                {"label": "ML status", "value": _friendly_code(result.get("ml_status")), "meta": "Anomaly model state", "variant": "success" if result.get("ml_status") == "FITTED" else ""},
                {"label": "Approved", "value": len(approved), "meta": "Passed every hard gate", "variant": "success" if approved else ""},
                {"label": "Protected / rejected", "value": len(rejected), "meta": "Blocked by safety controls", "variant": "warning" if rejected else "success"},
            ])
            if completed:
                st.success("Analysis completed and every candidate received a persisted final decision.")
            else:
                st.error(_analysis_failure_message(result.get("failure_reason")))
                if st.session_state.get("analysis_result"):
                    st.info("Your last successful analysis remains available on the candidate and evaluation pages.")
            with st.expander("Analysis audit record"):
                st.json(result)
    elif page == "Portfolio drift":
        st.header("Portfolio risk & drift")
        st.caption("Current allocation compared with the portfolio's approved target and risk limits.")
        result = st.session_state.get("analysis_result")
        approved = st.session_state.get("approved_candidates", [])
        rejected = st.session_state.get("rejected_candidates", [])
        portfolios = (_get("/api/portfolio-insights") or {}).get("portfolios", [])
        if not portfolios:
            st.warning("No brokerage portfolio is available for allocation analysis.")
        else:
            options = {f"{item.get('account')} · {_friendly_code(item.get('profile'))}": item for item in portfolios}
            portfolio = options[st.selectbox("Portfolio", list(options))]
            allocations = portfolio.get("allocations", [])
            off_target = [row for row in allocations if row.get("status") != "ON_TARGET"]
            portfolio_currency = portfolio.get("base_currency") or "USD"
            _status_cards([
                {"label": "Portfolio value", "value": _compact_currency(portfolio.get("total_value"), portfolio_currency), "meta": f"Full value: {_currency(portfolio.get('total_value'), portfolio_currency)}"},
                {"label": "Risk profile", "value": _friendly_code(portfolio.get("profile")), "meta": "Configured tolerance", "variant": "success"},
                {"label": "Classes off target", "value": len(off_target), "meta": "Outside 5% tolerance", "variant": "warning" if off_target else "success"},
                {"label": "Analysis readiness", "value": "Complete" if result else "Not run", "meta": "Latest controlled run", "variant": "success" if result else "warning"},
            ])
            chart_rows = [
                {
                    "Asset class": _friendly_code(row.get("asset_class")),
                    "Current %": round(float(row.get("current_weight") or 0) * 100, 2),
                    "Target %": round(float(row.get("target_weight") or 0) * 100, 2),
                }
                for row in allocations
            ]
            st.subheader("Allocation versus target")
            if chart_rows:
                st.bar_chart(chart_rows, x="Asset class", y=["Current %", "Target %"], color=["#0a8f88", "#9bb4d3"])
                st.dataframe(
                    [
                        {
                            "Asset class": _friendly_code(row.get("asset_class")),
                            "Current": _percent(row.get("current_weight")),
                            "Target": _percent(row.get("target_weight")),
                            "Difference": _percent(row.get("drift")),
                            "Status": _friendly_code(row.get("status")),
                        }
                        for row in allocations
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            if off_target:
                names = ", ".join(_friendly_code(row.get("asset_class")) for row in off_target)
                st.warning(f"Management attention: {names} {'is' if len(off_target) == 1 else 'are'} more than 5 percentage points from target.")
            else:
                st.success("Every asset class is within 5 percentage points of its target allocation.")
            limits = portfolio.get("risk_limits") or {}
            st.subheader("Risk guardrails")
            st.dataframe(
                [
                    {"Guardrail": "Maximum crypto", "Limit": _percent(limits.get("max_crypto_weight"))},
                    {"Guardrail": "Maximum equities and equity ETFs", "Limit": _percent(limits.get("max_equity_weight"))},
                    {"Guardrail": "Maximum single asset", "Limit": _percent(limits.get("max_single_asset_weight"))},
                    {"Guardrail": "Minimum bonds", "Limit": _percent(limits.get("min_bond_weight"))},
                    {"Guardrail": "Maximum turnover", "Limit": _percent(limits.get("max_turnover"))},
                    {"Guardrail": "Maximum trade size", "Limit": _currency(limits.get("max_trade_notional"), portfolio.get("base_currency") or "USD")},
                ],
                use_container_width=True,
                hide_index=True,
            )
            if portfolio.get("stale_symbols"):
                st.info("Allocation uses the latest available prices. Markets are closed or quotes are older for: " + ", ".join(portfolio["stale_symbols"]) + ".")
            if portfolio.get("missing_symbols"):
                st.warning("No price was available for: " + ", ".join(portfolio["missing_symbols"]) + ". These assets are excluded from the displayed value and weights.")
        st.markdown('<div class="soft-card"><b>How to read this</b><br><span class="section-note">Positive difference means overweight; negative difference means underweight. Recommendations remain blocked whenever a proposed sale would violate a configured guardrail.</span></div>', unsafe_allow_html=True)
        if rejected:
            reasons = {}
            for item in rejected:
                label = _friendly_code(item.get("rejection_code"))
                reasons[label] = reasons.get(label, 0) + 1
            st.subheader("Why recommendations were blocked")
            st.dataframe([{"Control":k,"Candidates":v} for k,v in sorted(reasons.items(), key=lambda x:-x[1])], use_container_width=True, hide_index=True)
        else:
            st.info("Run Portfolio analysis to populate the risk view.")
    elif page == "Tax-loss candidates":
        st.header("Tax-loss opportunities")
        st.caption("Only candidates that pass every hard gate appear in the approved list.")
        run_id = st.session_state.get("analysis_run_id")
        if run_id and st.button("Refresh latest candidates"):
            _load_analysis_candidates(run_id)
        if not run_id:
            attempt = st.session_state.get("analysis_attempt")
            if attempt and attempt.get("status") == "FAILED":
                st.warning("The latest attempt did not complete, so no candidate decision is being shown. Run Portfolio analysis again.")
            else:
                st.info("Run Portfolio analysis first.")
        approved = st.session_state.get("approved_candidates") or []
        rejected = st.session_state.get("rejected_candidates") or []
        _status_cards([
            {"label": "Approved", "value": len(approved), "meta": "Passed all safety gates", "variant": "success" if approved else ""},
            {"label": "Blocked", "value": len(rejected), "meta": "Protected by deterministic rules", "variant": "warning" if rejected else "success"},
            {"label": "Decision policy", "value": "Fail closed", "meta": "Uncertain candidates never proceed", "variant": "success"},
        ])
        st.subheader("Approved opportunities")
        if approved:
            st.dataframe([{"Rank":x.get("rank"),"Symbol":x.get("symbol"),"Type":x.get("asset_type"),"Quantity":x.get("selected_quantity"),"Estimated loss":x.get("estimated_loss"),"Reference price":x.get("reference_price"),"Provider":x.get("quote_provider"),"Feed":x.get("quote_feed"),"Decision":"Approved","Candidate ID":x.get("candidate_id")} for x in approved], use_container_width=True, hide_index=True)
        elif run_id:
            st.info("No candidate currently passes every safety rule. This is a valid fail-closed outcome.")
        _render_protected_decisions(rejected)
    elif page == "Evaluation details":
        st.header("Evaluation details")
        st.caption("Inspect the evidence and final policy decision for any candidate.")
        candidates = (st.session_state.get("approved_candidates") or []) + (st.session_state.get("rejected_candidates") or [])
        if not candidates:
            attempt = st.session_state.get("analysis_attempt")
            if attempt and attempt.get("status") == "FAILED":
                st.warning("The latest analysis attempt failed safely and produced no final candidate decisions. Run Portfolio analysis again.")
            else:
                st.info("Run Portfolio analysis first.")
        else:
            options = {f"{x.get('symbol','Unknown')} · {_friendly_code(x.get('status'))} · {str(x.get('candidate_id'))[:8]}": x for x in candidates}
            selected = options[st.selectbox("Select candidate", list(options))]
            approved_decision = selected.get("status") == "APPROVED"
            _status_cards([
                {"label": "Decision", "value": _friendly_code(selected.get("status")), "meta": "Persisted Eval Agent result", "variant": "success" if approved_decision else "warning"},
                {"label": "Symbol", "value": selected.get("symbol") or "—", "meta": _friendly_code(selected.get("asset_type"))},
                {"label": "Estimated loss", "value": _currency(selected.get("estimated_loss")), "meta": "Before execution", "variant": "success" if approved_decision else ""},
            ])
            if selected.get("quote_context") == "MARKET_CLOSED_USING_LAST_PRICE":
                st.info(
                    "US equity market is closed. This price is the latest Alpaca trade from the most recently completed session. It is not a stale-quote rejection."
                )
            if selected.get("status") == "APPROVED":
                st.success("Passed every configured hard gate and is eligible for paper-order preparation.")
            else:
                st.warning(selected.get("explanation") or "Candidate blocked by policy.")
            st.dataframe([{"Field":"Account","Value":selected.get("account")},{"Field":"Asset type","Value":selected.get("asset_type")},{"Field":"Quantity","Value":selected.get("selected_quantity")},{"Field":"Reference price","Value":selected.get("reference_price")},{"Field":"Quote provider","Value":selected.get("quote_provider")},{"Field":"Quote feed","Value":selected.get("quote_feed")},{"Field":"Replacement","Value":selected.get("replacement")},{"Field":"Rule version","Value":selected.get("rule_version")},{"Field":"Candidate ID","Value":selected.get("candidate_id")}], use_container_width=True, hide_index=True)
    elif page == "Paper orders":
        st.header("Paper order review")
        st.caption("Select an approved opportunity, review a read-only snapshot, then use the separate confirmation control.")
        snapshot = _persisted_analysis_snapshot(st.session_state.get("analysis_run_id"))
        kind = snapshot["kind"]
        approved_candidates = snapshot["approved"]
        rejected = snapshot["rejected"]
        if kind in {"completed_approved", "completed_none"}:
            st.session_state.approved_candidates = approved_candidates
            st.session_state.rejected_candidates = rejected
        candidate_id = None
        if kind == "none":
            st.info("Run Portfolio analysis first. Only opportunities that pass every safety rule can be prepared.")
        elif kind == "running":
            st.info("Portfolio analysis is in progress.")
        elif kind == "failed":
            st.error(_analysis_failure_message((snapshot["run"] or {}).get("failure_reason")))
            if st.button("Retry analysis", key="paper_orders_retry_analysis"):
                _retry_analysis_from_paper_orders()
                st.rerun()
        elif kind == "completed_none":
            st.info("Analysis completed, but no opportunities passed every safety rule. Review Protected decisions for the reasons.")
        elif kind == "completed_approved":
            choices = {
                f"#{item.get('rank') or '—'} · {item.get('symbol')} · {item.get('account')} · {_currency(item.get('estimated_loss'))} estimated loss": item.get("candidate_id")
                for item in approved_candidates
            }
            candidate_id = choices[st.selectbox("Approved opportunity", list(choices))]
        if st.button(
            PREPARE_BUTTON_LABEL,
            type="primary",
            disabled=not candidate_id,
            help=None if candidate_id else PREPARE_DISABLED_HELP,
        ) and candidate_id:
            prepared = _post(f"/api/candidates/{candidate_id}/prepare")
            if prepared:
                st.session_state.prepared_snapshot = prepared
                st.session_state.paper_enabled = bool(prepared.get("paper_orders_enabled"))
                st.session_state.candidate_approved = prepared.get("approval_status") == "APPROVED"
                st.session_state.order_submitted = False
        if kind in {"completed_none", "completed_approved"}:
            _render_protected_decisions(rejected)
        render_confirm_panel(
            snapshot=st.session_state.get("prepared_snapshot"),
            paper_enabled=bool(st.session_state.get("paper_enabled", False)),
            approved=bool(st.session_state.get("candidate_approved", False)),
        )
        last = st.session_state.get("last_order")
        if last:
            if last.get("queued") or last.get("status") == "QUEUED":
                kind, message = paper_submit_feedback(last)
                st.info(message)
            if st.button("Refresh paper-order status"):
                refreshed = _post(f"/api/paper-orders/{last['order_id']}/refresh")
                if refreshed:
                    st.session_state.last_order = {**last, **refreshed}
                    st.write(refreshed)
    elif page == "WhatsApp integration":
        st.header("WhatsApp integration")
        st.caption("Connect a read-only WhatsApp channel for authoritative portfolio, risk, drift and safely evaluated tax-loss information.")
        configured_phone = os.environ.get("WHATSAPP_PHONE_NUMBER", "").strip()
        default_message = os.environ.get(
            "WHATSAPP_DEFAULT_MESSAGE",
            "Hello Northstar, show my portfolio summary.",
        )
        webhook_configured = all(
            os.environ.get(name, "").strip()
            for name in (
                "WHATSAPP_PHONE_NUMBER_ID",
                "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_VERIFY_TOKEN",
                "WHATSAPP_APP_SECRET",
                "WHATSAPP_ALLOWED_SENDERS",
            )
        )
        connect_tab, portfolio_tab, recommendations_tab = st.tabs(
            ["Connect WhatsApp", "Portfolio", "Recommendations"]
        )
        with connect_tab:
            details, qr_column = st.columns([1.45, 1], gap="large")
            with details:
                st.markdown(
                    '''<div class="integration-card">
                    <span class="integration-badge">READ-ONLY CHANNEL</span>
                    <h3>Northstar on WhatsApp</h3>
                    <p>Ask for portfolio holdings, allocation drift, risk limits, spending signals, or tax-loss opportunities approved by deterministic safety controls.</p>
                    </div>''',
                    unsafe_allow_html=True,
                )
                _status_cards([
                    {"label": "QR link", "value": "Ready" if configured_phone else "Setup", "meta": "Click-to-chat access", "variant": "success" if configured_phone else "warning"},
                    {"label": "Bot webhook", "value": "Connected" if webhook_configured else "Pending", "meta": "Automatic question responses", "variant": "success" if webhook_configured else "warning"},
                ])
                st.markdown("#### Connection checklist")
                st.markdown(
                    "1. Enter the Meta business or test number below.\n"
                    "2. Scan the generated QR with your phone camera.\n"
                    "3. Configure Meta's callback to `/api/whatsapp/webhook`.\n"
                    "4. Add your personal number to `WHATSAPP_ALLOWED_SENDERS`."
                )
            with qr_column:
                phone_number = st.text_input(
                    "Meta business/test number",
                    value=configured_phone,
                    placeholder="447700900123",
                    help="Digits only, including country code. Use the number provided by Meta—not your password.",
                )
                starter = st.selectbox(
                    "Starter question",
                    [
                        default_message,
                        "Hello Northstar, is my portfolio within its risk limits?",
                        "Hello Northstar, show my allocation drift.",
                        "Hello Northstar, show my safe tax-loss opportunities.",
                    ],
                )
                link = _whatsapp_link(phone_number, starter)
                if link:
                    st.image(_whatsapp_qr(link), caption="Scan with your phone camera", width=260)
                    st.link_button("Open WhatsApp", link, use_container_width=True)
                    digits = re.sub(r"\D", "", phone_number)
                    st.caption(f"Business/test number: +{'•' * max(len(digits) - 4, 0)}{digits[-4:]}")
                    if not webhook_configured:
                        st.warning("The QR opens WhatsApp now, but automatic bot replies require the Meta webhook credentials listed below.")
                else:
                    st.info("Enter the Meta business/test number to generate the scannable QR code.")
            st.info("Safety boundary: WhatsApp cannot prepare, approve, confirm, buy, sell, or submit an order.")
            with st.expander("Meta configuration fields"):
                st.code(
                    "WHATSAPP_PHONE_NUMBER=\n"
                    "WHATSAPP_PHONE_NUMBER_ID=\n"
                    "WHATSAPP_ACCESS_TOKEN=\n"
                    "WHATSAPP_VERIFY_TOKEN=\n"
                    "WHATSAPP_APP_SECRET=\n"
                    "WHATSAPP_ALLOWED_SENDERS=",
                    language="text",
                )
                st.caption("Never paste access tokens into the QR field or commit them to Git.")
        with portfolio_tab:
            st.subheader("Portfolio available to WhatsApp")
            st.caption("Preview the authoritative data that the WhatsApp assistant is allowed to explain.")
            holdings = (_get("/api/holdings") or {}).get("holdings", [])
            accounts = sorted({str(item.get("account") or "Unknown") for item in holdings})
            asset_types = sorted({_friendly_code(item.get("asset_type")) for item in holdings})
            filter_columns = st.columns([1, 1, 1.2])
            account_filter = filter_columns[0].selectbox("Account", ["All accounts", *accounts])
            type_filter = filter_columns[1].multiselect("Asset type", asset_types, default=asset_types)
            symbol_filter = filter_columns[2].text_input("Find symbol", placeholder="e.g. VTI")
            visible_holdings = [
                item for item in holdings
                if (account_filter == "All accounts" or item.get("account") == account_filter)
                and _friendly_code(item.get("asset_type")) in type_filter
                and (not symbol_filter or symbol_filter.lower() in str(item.get("symbol") or "").lower())
            ]
            _status_cards([
                {"label": "Visible positions", "value": len(visible_holdings), "meta": "After selected filters"},
                {"label": "Accounts", "value": len({item.get('account') for item in visible_holdings}), "meta": "Authoritative portfolio scope"},
                {"label": "Channel access", "value": "Explain only", "meta": "No portfolio mutation", "variant": "success"},
            ])
            st.dataframe(
                [{"Symbol": item.get("symbol"), "Name": item.get("name"), "Type": _friendly_code(item.get("asset_type")), "Quantity": item.get("quantity"), "Account": item.get("account")} for item in visible_holdings],
                use_container_width=True,
                hide_index=True,
            )
        with recommendations_tab:
            st.subheader("Recommendations available to WhatsApp")
            st.caption("These are tax-loss decisions—not unrestricted predictions of which investment will perform best.")
            approved = st.session_state.get("approved_candidates") or []
            protected = st.session_state.get("rejected_candidates") or []
            rows = [*approved, *protected]
            decision_filter = st.segmented_control(
                "Decision",
                ["All", "Approved", "Protected"],
                default="All",
            )
            controls = sorted({_friendly_code(item.get("rejection_code")) for item in protected if item.get("rejection_code")})
            control_filter = st.multiselect("Safety control", controls, default=controls)
            filtered = []
            for item in rows:
                is_approved = item.get("status") == "APPROVED"
                if decision_filter == "Approved" and not is_approved:
                    continue
                if decision_filter == "Protected" and is_approved:
                    continue
                if not is_approved and controls and _friendly_code(item.get("rejection_code")) not in control_filter:
                    continue
                filtered.append(item)
            _status_cards([
                {"label": "Approved", "value": len(approved), "meta": "Passed every hard gate", "variant": "success" if approved else ""},
                {"label": "Protected", "value": len(protected), "meta": "Blocked by safety controls", "variant": "warning" if protected else "success"},
                {"label": "Policy", "value": "Fail closed", "meta": "No speculative best-investment ranking", "variant": "success"},
            ])
            if filtered:
                st.dataframe(
                    [{"Symbol": item.get("symbol"), "Decision": _friendly_code(item.get("status")), "Safety control": _friendly_code(item.get("rejection_code")) if item.get("rejection_code") else "Passed all gates", "Explanation": item.get("explanation"), "Rank": item.get("rank")} for item in filtered],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("Run Portfolio analysis to populate persisted recommendation decisions.")


if __name__ == "__main__":
    main()
