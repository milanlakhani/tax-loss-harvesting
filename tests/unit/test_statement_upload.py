from __future__ import annotations

from pathlib import Path

from app.ui.statement_upload import collect_statement_pdfs


class _Upload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def test_collect_statement_pdfs_from_folder_keeps_bank_and_brokerage(tmp_path: Path):
    (tmp_path / "BANK-0.pdf").write_bytes(b"%PDF-1.4 bank")
    (tmp_path / "BROKERAGE-A.pdf").write_bytes(b"%PDF-1.4 brokerage")
    (tmp_path / "notes.txt").write_text("ignore")
    items, warnings = collect_statement_pdfs(folder=str(tmp_path))
    names = [name for name, _ in items]
    assert names == ["BANK-0.pdf", "BROKERAGE-A.pdf"]
    assert warnings == []


def test_collect_statement_pdfs_from_multiple_uploads():
    items, warnings = collect_statement_pdfs(
        uploads=[
            _Upload("checking.pdf", b"%PDF-1.4 bank"),
            _Upload("taxable.pdf", b"%PDF-1.4 brokerage"),
            _Upload("notes.txt", b"not a pdf"),
        ]
    )
    assert [name for name, _ in items] == ["checking.pdf", "taxable.pdf"]
    assert any("not a PDF" in warning for warning in warnings)


def test_mixed_bank_and_brokerage_uploads_are_collected_for_submit():
    uploads = [
        _Upload("BANK-0.pdf", b"%PDF-1.4 bank-statement"),
        _Upload("BROKERAGE-A.pdf", b"%PDF-1.4 brokerage-statement"),
    ]
    items, warnings = collect_statement_pdfs(uploads=uploads)
    assert warnings == []
    assert items == [
        ("BANK-0.pdf", b"%PDF-1.4 bank-statement"),
        ("BROKERAGE-A.pdf", b"%PDF-1.4 brokerage-statement"),
    ]
    submitted = [(name, data) for name, data in items]
    assert [name for name, _ in submitted] == ["BANK-0.pdf", "BROKERAGE-A.pdf"]


def test_collect_statement_pdfs_skips_invalid_and_missing_folder(tmp_path: Path):
    (tmp_path / "broken.pdf").write_bytes(b"not-pdf")
    items, warnings = collect_statement_pdfs(folder=str(tmp_path / "missing"))
    assert items == []
    assert any("Folder not found" in warning for warning in warnings)
    items, warnings = collect_statement_pdfs(uploads=[_Upload("broken.pdf", b"not-pdf")])
    assert items == []
    assert any("not a valid PDF" in warning for warning in warnings)
