from __future__ import annotations

from pathlib import Path

MAX_STATEMENT_BYTES = 10 * 1024 * 1024
PDF_SUFFIXES = {".pdf"}


def collect_statement_pdfs(*, uploads=None, folder: str = "") -> tuple[list[tuple[str, bytes]], list[str]]:
    """Gather bank and brokerage PDFs from selected uploads.

    ``folder`` remains for internal CLI/tests. The web UI must not expose a host path.
    """
    items: list[tuple[str, bytes]] = []
    warnings: list[str] = []
    seen: set[str] = set()

    def add(name: str, data: bytes) -> None:
        filename = Path(name).name or "upload.pdf"
        key = filename.casefold()
        if key in seen:
            return
        if Path(filename).suffix.casefold() not in PDF_SUFFIXES:
            warnings.append(f"{filename}: skipped (not a PDF)")
            return
        if not data.startswith(b"%PDF-"):
            warnings.append(f"{filename}: skipped (not a valid PDF)")
            return
        if len(data) > MAX_STATEMENT_BYTES:
            warnings.append(f"{filename}: skipped (exceeds 10 MB)")
            return
        seen.add(key)
        items.append((filename, data))

    for upload in uploads or []:
        name = getattr(upload, "name", None) or "upload.pdf"
        data = upload.getvalue() if hasattr(upload, "getvalue") else bytes(upload)
        add(name, data)

    folder = folder.strip()
    if folder:
        path = Path(folder)
        if not path.is_dir():
            warnings.append(f"Folder not found: {folder}")
        else:
            pdfs = sorted({resolved for candidate in path.glob("*") if (resolved := candidate.resolve()).is_file() and resolved.suffix.casefold() in PDF_SUFFIXES})
            if not pdfs:
                warnings.append(f"No PDF files found in {folder}")
            for pdf in pdfs:
                add(pdf.name, pdf.read_bytes())
    return items, warnings
