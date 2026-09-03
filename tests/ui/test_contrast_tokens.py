from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STREAMLIT_APP = (REPO / "app" / "ui" / "streamlit_app.py").read_text(encoding="utf-8")
THEME_CONFIG = (REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8")


def _hex_luminance(value: str) -> float:
    raw = value.lstrip("#")
    channels = [int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)]

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted((_hex_luminance(foreground), _hex_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _css_tokens() -> dict[str, str]:
    return {
        name: colour
        for name, colour in re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});", STREAMLIT_APP)
    }


@pytest.mark.unit
def test_theme_config_uses_accessible_baseline_tokens():
    assert 'primaryColor = "#08736D"' in THEME_CONFIG
    assert 'backgroundColor = "#F2F7FF"' in THEME_CONFIG
    assert 'secondaryBackgroundColor = "#FFFFFF"' in THEME_CONFIG
    assert 'textColor = "#16263D"' in THEME_CONFIG
    assert 'base = "light"' in THEME_CONFIG


@pytest.mark.unit
def test_main_foreground_background_tokens_meet_wcag_aa():
    tokens = _css_tokens()
    expected = {
        "text-primary": "#16263D",
        "text-secondary": "#455A73",
        "text-muted": "#52657D",
        "text-on-dark": "#F4F8FF",
        "text-on-dark-secondary": "#D8E6FF",
        "link-on-light": "#08736D",
        "surface-page": "#F2F7FF",
        "surface-card": "#FFFFFF",
        "surface-navy": "#081426",
        "surface-teal": "#08736D",
        "surface-disabled": "#E4EAF2",
        "text-disabled": "#455A73",
        "surface-pill": "#DFF8EF",
        "text-pill": "#08715C",
    }
    for name, colour in expected.items():
        assert tokens[name].upper() == colour.upper()

    pairs = [
        (tokens["text-primary"], tokens["surface-page"], 4.5),
        (tokens["text-primary"], tokens["surface-card"], 4.5),
        (tokens["text-secondary"], tokens["surface-page"], 4.5),
        (tokens["text-secondary"], tokens["surface-card"], 4.5),
        (tokens["text-muted"], tokens["surface-page"], 4.5),
        (tokens["text-muted"], tokens["surface-card"], 4.5),
        (tokens["text-on-dark"], tokens["surface-navy"], 4.5),
        (tokens["text-on-dark-secondary"], tokens["surface-navy"], 4.5),
        (tokens["text-on-dark"], tokens["surface-teal"], 4.5),
        (tokens["link-on-light"], tokens["surface-page"], 4.5),
        (tokens["link-on-light"], tokens["surface-card"], 4.5),
        (tokens["text-disabled"], tokens["surface-disabled"], 4.5),
        (tokens["text-pill"], tokens["surface-pill"], 4.5),
    ]
    for foreground, background, minimum in pairs:
        assert contrast_ratio(foreground, background) >= minimum, (foreground, background)


@pytest.mark.unit
def test_captions_do_not_rely_on_opacity_for_muted_text():
    assert re.search(
        r"\[data-testid=\"stCaptionContainer\"\][^{]*\{[^}]*opacity:\s*1\s*!important",
        STREAMLIT_APP,
        re.S,
    )
    assert '[data-testid="stMain"]' in STREAMLIT_APP
    assert '[data-testid="stAppViewContainer"] .main' not in STREAMLIT_APP
