from __future__ import annotations


def confirm_button_enabled(
    *,
    checked: bool,
    paper_enabled: bool,
    prepared: bool,
    approved: bool,
    submitted: bool,
) -> bool:
    if submitted:
        return False
    if not checked:
        return False
    if not paper_enabled or not prepared or not approved:
        return False
    return True
