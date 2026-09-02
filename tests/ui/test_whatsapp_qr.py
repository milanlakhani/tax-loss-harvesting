from app.ui.streamlit_app import _whatsapp_link, _whatsapp_qr


def test_whatsapp_qr_uses_sanitized_click_to_chat_link() -> None:
    link = _whatsapp_link("+44 7700 900123", "Show my portfolio & recommendations")

    assert link == "https://wa.me/447700900123?text=Show%20my%20portfolio%20%26%20recommendations"
    assert _whatsapp_qr(link).startswith(b"\x89PNG\r\n\x1a\n")
    assert _whatsapp_link("not configured", "Hello") == ""
