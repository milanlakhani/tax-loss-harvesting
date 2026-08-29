from __future__ import annotations

import io

from app.api.statements import validate_pdf_upload


def test_validate_pdf_upload_accepts_real_pdf_header():
    payload = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    result = validate_pdf_upload(io.BytesIO(payload), max_bytes=1_000_000)
    assert result["ok"] is True
    assert result["mime_type"] == "application/pdf"
    assert result["size_bytes"] == len(payload)


def test_validate_pdf_upload_rejects_non_pdf_data():
    result = validate_pdf_upload(io.BytesIO(b"not a pdf"), max_bytes=1_000_000)
    assert result["ok"] is False
    assert result["reason"] == "File does not appear to be a valid PDF"
