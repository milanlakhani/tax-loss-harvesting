from __future__ import annotations

from pathlib import Path

import fitz

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT = 36
TOP = 36
LINE_HEIGHT = 11
FONT_SIZE = 7
MAX_Y = 760
BANK_MARKER = "SYNTHETIC_BANK_V1"
BROKERAGE_MARKER = "SYNTHETIC_BROKERAGE_V1"
DEMO_BANNER = "DEMO DATA - NOT A REAL STATEMENT"
FIXED_METADATA = {
    "producer": "tlh-demo-generator",
    "creator": "tlh-demo-generator",
    "creationDate": "D:20240101000000Z",
    "modDate": "D:20240101000000Z",
}


class PagedTextDocument:
    def __init__(self, format_marker: str) -> None:
        self.format_marker = format_marker
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.y = TOP
        self._write_page_header()

    def _write_page_header(self) -> None:
        self.page.insert_text((LEFT, self.y), f"FORMAT: {self.format_marker}", fontsize=FONT_SIZE, fontname="courier")
        self.y += LINE_HEIGHT
        self.page.insert_text((LEFT, self.y), DEMO_BANNER, fontsize=FONT_SIZE, fontname="courier")
        self.y += LINE_HEIGHT * 1.5

    def writeln(self, line: str = "") -> None:
        if self.y > MAX_Y - 24:
            self._new_page()
        self.page.insert_text((LEFT, self.y), line[:240], fontsize=FONT_SIZE, fontname="courier")
        self.y += LINE_HEIGHT

    def _new_page(self) -> None:
        self._write_page_footer()
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.y = TOP
        self._write_page_header()

    def _write_page_footer(self) -> None:
        page_no = self.page.number + 1
        footer = f"PAGE {page_no} CONTINUES  {self.format_marker}  {DEMO_BANNER}"
        self.page.insert_text((LEFT, 778), footer, fontsize=7, fontname="courier")

    def finalize(self) -> bytes:
        self._write_page_footer()
        total = self.doc.page_count
        for i, page in enumerate(self.doc):
            stamp = f"PAGE {i + 1} OF {total}  {self.format_marker}  {DEMO_BANNER}"
            # Overlay a second footer line so parsers can check continuity.
            page.insert_text((LEFT, 788), stamp, fontsize=7, fontname="courier")
        self.doc.set_metadata(FIXED_METADATA)
        data = self.doc.tobytes()
        self.doc.close()
        return data

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.finalize())
        return path
