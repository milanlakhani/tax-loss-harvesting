from __future__ import annotations

from app.ui.confirm_state import confirm_button_enabled


def test_confirm_button_rules():
    assert confirm_button_enabled(checked=True, paper_enabled=True, prepared=True, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=False, paper_enabled=True, prepared=True, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=False, prepared=True, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=True, prepared=False, approved=True, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=True, prepared=True, approved=False, submitted=False)
    assert not confirm_button_enabled(checked=True, paper_enabled=True, prepared=True, approved=True, submitted=True)
