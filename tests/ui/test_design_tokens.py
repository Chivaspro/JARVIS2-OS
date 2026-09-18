"""The design-token contract.

These tests are the reason ``ui.py`` no longer owns a palette: they pin the
token vocabulary to three radii, three stroke weights, one accent, and a small
spacing scale, and they prove the derived helpers (which are what let painter
code and QSS share one colour) actually work.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import ui_theme as T

HEX6 = re.compile(r"^#[0-9a-f]{6}$")


def test_palette_values_are_opaque_hex():
    """Legacy call sites hand these to QColor()/QPen(), which reject rgba()."""
    for name, value in T.PALETTE.items():
        assert HEX6.match(value), f"{name} is not opaque #rrggbb: {value!r}"


def test_hue_linked_keys_all_exist_in_the_palette():
    for key in T.HUE_LINKED:
        assert key in T.PALETTE, f"hue-linked key {key} has no token"


def test_palette_defaults_are_hex_for_the_accent_rotation():
    """apply_ui_accent() parses each default as '#rrggbb'; anything else is a
    silently ignored accent change."""
    for key, value in T.PALETTE_DEFAULTS.items():
        assert HEX6.match(value), f"{key} cannot be hue-rotated: {value!r}"


def test_geometry_vocabulary_is_closed():
    assert {T.RADIUS_LG, T.RADIUS_MD, T.RADIUS_SM} == {12, 9, 6}
    assert len({T.RADIUS_LG, T.RADIUS_MD, T.RADIUS_SM}) == 3


def test_there_are_exactly_three_stroke_weights():
    """One accent at three alphas — not four independent border colours."""
    parsed = [T._rgba_parts(v) for v in (T.BORDER, T.BORDER_SOFT, T.BORDER_FAINT)]
    hues = {p[:3] for p in parsed}
    assert len(hues) == 1, "the three stroke weights must share one hue"
    alphas = [p[3] for p in parsed]
    assert alphas == sorted(alphas, reverse=True)
    assert len({T.STROKE_STRONG, T.STROKE_MID, T.STROKE_HAIR}) == 3


def test_spacing_scale_is_small_and_ordered():
    assert T.SPACING_SCALE == tuple(sorted(T.SPACING_SCALE))
    assert len(T.SPACING_SCALE) <= 5


def test_control_heights_are_a_small_closed_set():
    assert len(T.CONTROL_HEIGHTS) == 3
    assert T.CONTROL_HEIGHTS == (28, 34, 38)


@pytest.mark.parametrize("value,expected", [
    ("#00d4ff", "#00d4ff"),
    ("#00D4FF", "#00d4ff"),
    ("rgba(0, 212, 255, 120)", "#00d4ff"),
    ("#0af", "#00aaff"),
    ("#00d4ff80", "#00d4ff"),
    ("", "#000000"),
    ("not-a-colour", "#000000"),
])
def test_hex_color_normalises_anything_qcolor_would_reject(value, expected):
    assert T.hex_color(value) == expected


def test_rgba_and_flatten_round_trip():
    assert T.rgba("#00d4ff", 120) == "rgba(0, 212, 255, 120)"
    flattened = T.flatten(T.rgba("#00d4ff", 120))
    assert HEX6.match(flattened)
    # A fully opaque token flattens to itself.
    assert T.flatten("#00d4ff") == "#00d4ff"


def test_mix_and_shade_stay_in_gamut():
    assert T.mix("#ffffff", "#000000", 0.5) == "#808080"
    assert T.mix("#ffffff", "#000000", 9.9) == "#ffffff"
    assert T.mix("#ffffff", "#000000", -3) == "#000000"
    assert T.shade("#808080", 2.0) == "#ffffff"
    assert T.shade("#808080", 0) == "#000000"


def test_qcolor_accepts_rgba_tokens():
    """The bug this helper exists for: QColor('rgba(...)') is invalid in PyQt6."""
    from PyQt6.QtGui import QColor
    assert not QColor(T.PANEL).isValid() or QColor(T.PANEL).alpha() == 255
    colour = T.qcolor(T.PANEL)
    assert colour.isValid()
    assert colour.alpha() == 240


@pytest.mark.parametrize("legacy,current", [
    ("#00d4ff", T.PALETTE["PRI"]),
    ("#007a99", T.PALETTE["PRI_DIM"]),
])
def test_legacy_accents_migrate(legacy, current):
    assert T.migrate_accent(legacy) == current


def test_custom_accents_pass_through_untouched():
    assert T.migrate_accent("#ff00aa") == "#ff00aa"
    assert T.migrate_accent("#AB12CD") == "#ab12cd"


def test_stylesheet_factories_produce_valid_css():
    """Doubled braces leaking into a stylesheet is invalid QSS that Qt silently
    ignores, which is exactly how a panel ends up unstyled."""
    factories = [
        T.button_css, T.primary_button_css, T.accent_button_css, T.field_css,
        T.card_css, T.header_css, T.chip_css, T.section_css, T.title_css,
        T.subtitle_css, T.scrollbar_css, T.hidden_scrollbar_css, T.tab_css,
        T.slider_css, T.checkbox_css, T.dialog_css, T.tooltip_css,
        T.shell_dialog_css, T.nav_header_css, T.qtabbar_css, T.scroll_area_css,
        T.group_box_css, T.settings_surface_css, T.separator_css,
        T.micro_label_css, T.panel_title_css, T.panel_subtitle_css,
        T.panel_icon_css, T.status_chip_css, T.composed_app_css,
    ]
    for factory in factories:
        sheet = factory()
        assert "{{" not in sheet and "}}" not in sheet, f"{factory.__name__} has doubled braces"
        assert sheet.count("{") == sheet.count("}"), f"{factory.__name__} has unbalanced braces"


def test_no_surface_declares_its_own_palette():
    """The audit that keeps this consolidated: none of the HUD modules may carry
    a private colour table."""
    root = Path(__file__).resolve().parent.parent.parent
    allowed = {"ui_theme.py"}
    offenders = {}
    for name in ("ui.py", "ui_settings.py", "ui_layout.py"):
        text = (root / name).read_text(encoding="utf-8")
        # A palette declaration looks like `NAME = "#rrggbb"` at module or class
        # level; local colour choices inside one widget are handled below.
        hits = re.findall(r"^\s{0,8}[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\"']#[0-9a-fA-F]{6}[\"']", text, re.M)
        if hits:
            offenders[name] = hits
    for name in ("dashboard/brain3d.py",):
        text = (root / name).read_text(encoding="utf-8")
        hits = re.findall(r"^\s{0,8}[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\"']#[0-9a-fA-F]{6}[\"']", text, re.M)
        if hits:
            offenders[name] = hits
    assert not offenders, f"modules still declaring their own palette: {offenders}"
    assert allowed == {"ui_theme.py"}
