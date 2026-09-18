"""J.A.R.V.I.S centralized design system — Refined Stark HUD.

A precise, restrained holographic language: deep vacuum-black surfaces, a single
arc-reactor ice-cyan as the working color, and a sparing Stark repulsor-gold as
the one signature accent reserved for the live/active state. Strokes are thin and
consistent; glow is used as punctuation, never as wallpaper. Shared by every panel.

Design rules encoded here so the whole UI stays coherent:
  * One primary (CYAN), one signature accent (GOLD), a cold neutral ramp, three
    status colors. Nothing else earns a slot.
  * Three radii only (LG / MD / SM). Borders come in three weights (LINE / SOFT / FAINT).
  * Type: Orbitron for display/headings, Rajdhani for UI/body, Share Tech Mono for logs.
"""
from __future__ import annotations

# ── Token helpers ───────────────────────────────────────────────────────────
# Qt's QColor()/QPen() cannot parse rgba() strings (PyQt6 rejects them), so any
# token that reaches painter code has to be flattened to an opaque hex first.
# These helpers are what lets the whole HUD share one token set: the translucent
# QSS values and the opaque painter values are derived, never declared twice.


def _rgba_parts(color: str) -> tuple[int, int, int, int]:
    """Parse '#rgb', '#rrggbb' or '#rrggbbaa' -> (r, g, b, a). Never raises."""
    s = str(color or "").strip()
    if s.lower().startswith("rgba(") or s.lower().startswith("rgb("):
        body = s[s.index("(") + 1:s.rindex(")")]
        parts = [p.strip() for p in body.split(",")]
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            return (0, 0, 0, 255)
        while len(vals) < 4:
            vals.append(255.0)
        return (int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]))
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) not in (6, 8):
        return (0, 0, 0, 255)
    try:
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
        a = int(s[6:8], 16) if len(s) == 8 else 255
    except ValueError:
        return (0, 0, 0, 255)
    return (r, g, b, a)


def rgba(color: str, alpha: int) -> str:
    """The same hue at a given alpha -> a QSS 'rgba(...)' colour."""
    r, g, b, _a = _rgba_parts(color)
    return f"rgba({r}, {g}, {b}, {int(alpha)})"


def hex_color(color: str) -> str:
    """Opaque '#rrggbb' of any token — the form QColor() accepts."""
    r, g, b, _a = _rgba_parts(color)
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(color: str, other: str, amount: float) -> str:
    """Blend `amount` of `color` into `other` -> opaque '#rrggbb'."""
    r, g, b, _a = _rgba_parts(color)
    orr, og, ob, _b = _rgba_parts(other)
    k = max(0.0, min(1.0, float(amount)))
    return "#{:02x}{:02x}{:02x}".format(
        int(r * k + orr * (1 - k) + 0.5),
        int(g * k + og * (1 - k) + 0.5),
        int(b * k + ob * (1 - k) + 0.5))


def shade(color: str, factor: float) -> str:
    """Darken (factor < 1) or lighten (factor > 1) a token, clamped."""
    r, g, b, _a = _rgba_parts(color)
    f = max(0.0, float(factor))
    return "#{:02x}{:02x}{:02x}".format(
        *[max(0, min(255, int(c * f + 0.5))) for c in (r, g, b)])


def flatten(color: str, alpha: int | None = None, bg: str | None = None) -> str:
    """Composite a translucent token onto a background -> opaque '#rrggbb'.

    This is how one translucent token serves both QSS (needs the alpha) and
    painter code (needs a solid colour).
    """
    r, g, b, a = _rgba_parts(color)
    if alpha is not None:
        a = int(alpha)
    br, bg_g, bb, _ = _rgba_parts(BG if bg is None else bg)
    k = max(0, min(255, a)) / 255.0
    return "#{:02x}{:02x}{:02x}".format(
        int(r * k + br * (1 - k) + 0.5),
        int(g * k + bg_g * (1 - k) + 0.5),
        int(b * k + bb * (1 - k) + 0.5))


def qcolor(color: str, alpha: int | None = None):
    """QColor for any token, tolerating rgba() and '#rrggbbaa'.

    Imported lazily so this module stays Qt-free for string-only callers.
    """
    from PyQt6.QtGui import QColor
    c = QColor(hex_color(color))
    _r, _g, _b, a = _rgba_parts(color)
    c.setAlpha(int(a if alpha is None else alpha))
    return c


# ── Palette seed ────────────────────────────────────────────────────────────
# The single primary accent. Surfaces are an alpha/tint ramp of it, the stroke
# weights are three alphas of it, and the user's ui_color rotates its hue — so
# this one value re-tints the entire interface.
ACCENT        = "#00d4ff"
ACCENT_BRIGHT = "#8ceaff"
ACCENT_DEEP   = "#007a99"
ACCENT_INK    = "#001f2e"

# The one signature accent, reserved for live / armed / active state only.
GOLD        = "#f4b24a"
GOLD_DEEP   = "#b87d1e"
GOLD_SOFT   = "#ffd48a"

# ── Surfaces ────────────────────────────────────────────────────────────────
# A vacuum-black base with a faint accent bias, then translucent panes stacked on
# top. Alphas are tuned so panels read as layered glass, not flat fills.
BG          = "#02060c"
BG_DEEP     = shade(BG, 0.7)
PANEL       = rgba(mix(ACCENT, BG, 0.06), 240)
PANEL_SOFT  = rgba(mix(ACCENT, BG, 0.05), 92)
CARD        = rgba(mix(ACCENT, BG, 0.05), 168)
CARD_HI     = rgba(mix(ACCENT, BG, 0.09), 190)
HEADER      = rgba(mix(ACCENT, BG, 0.08), 150)

# Opaque variants of the panes, for painter code and for surfaces that need a
# solid fill (cards over a patterned backdrop).
SURFACE       = mix(ACCENT, BG, 0.075)
SURFACE_ALT   = mix(ACCENT, BG, 0.040)
SURFACE_INSET = mix(ACCENT, BG, 0.050)

# ── Strokes ─────────────────────────────────────────────────────────────────
# Three weights only, and all three are *the same accent at three alphas*:
# LINE = interactive edge, SOFT = grouping, FAINT = hairline.
BORDER       = rgba(ACCENT, 120)
BORDER_SOFT  = rgba(ACCENT, 70)
BORDER_FAINT = rgba(ACCENT, 46)

# Flattened (painter-safe) forms of the same three weights.
STROKE_STRONG = flatten(BORDER)
STROKE_MID    = flatten(BORDER_SOFT)
STROKE_HAIR   = flatten(BORDER_FAINT)

# ── Text ramp ───────────────────────────────────────────────────────────────
TEXT        = "#daf5ff"
TEXT_BRIGHT = "#f0fdff"
TEXT_MID    = "#a6e9ff"
TEXT_DIM    = "#6ba7bb"
TEXT_FAINT  = "#547f92"

# ── Status ──────────────────────────────────────────────────────────────────
# The status ramp is deliberately independent of the accent: a warning must not
# change colour because the user re-tinted the HUD.
GREEN       = "#5df0b6"
GREEN_DEEP  = shade(GREEN, 0.72)
AMBER       = "#ffc773"
RED         = "#ff6f8b"

# ── Geometry ────────────────────────────────────────────────────────────────
# Three radii only, and one spacing scale: every margin, gap and control height
# in the HUD is a value from these two sets.
RADIUS_LG   = 12
RADIUS_MD   = 9
RADIUS_SM   = 6

SPACE_XS    = 4
SPACE_SM    = 6
SPACE_MD    = 10
SPACE_LG    = 14
SPACE_XL    = 20
SPACING     = (SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL)
SPACING_SCALE = (SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL)

# Control heights. Legacy widgets used 26/28/30/34/36 at random; these three
# are the only allowed heights, and ui.py's class C aliases them.
CONTROL_SM  = 28
CONTROL_MD  = 34
CONTROL_LG  = 38
CONTROL_HEIGHTS = (CONTROL_SM, CONTROL_MD, CONTROL_LG)

# Panel header band. A minimum, not a fixed height: the shared header grows when
# a title wraps instead of clipping it.
HEADER_MIN_H = 54
PANEL_MIN_W  = 380
PANEL_MIN_H  = 250

# Text that must never be clipped: how much of the header a title may take.
TITLE_MAX_LINES  = 2

# ── Type ────────────────────────────────────────────────────────────────────
FONT_UI    = "'Rajdhani'"
FONT_HEAD  = "'Orbitron'"
FONT_LOG   = "'Share Tech Mono'"
FONT_NUM   = "'Orbitron'"
FONT_SMALL = "'Exo 2'"
FONT_MONO  = "'Share Tech Mono'"
FONT_DISP  = "'Orbitron'"


def button_css(min_height: int = 34) -> str:
    """Quiet default control: hairline edge, cyan only on interaction."""
    return (
        "QPushButton {"
        f" color:{TEXT_MID}; background:rgba(5,22,34,120);"
        f" border:1px solid {BORDER_FAINT}; border-radius:{RADIUS_SM}px;"
        f" font:700 8pt {FONT_SMALL}; letter-spacing:1px;"
        f" min-height:{min_height}px; padding:6px 13px; }}"
        "QPushButton:hover {"
        f" color:{TEXT_BRIGHT}; border-color:{ACCENT}; background:rgba(10,134,184,64); }}"
        "QPushButton:pressed { background:rgba(9,110,152,150); }"
        "QPushButton:disabled {"
        " color:#37606f; border-color:rgba(52,128,158,20); background:rgba(5,22,34,44); }"
        "QPushButton:checked {"
        f" color:{TEXT_BRIGHT}; background:rgba(10,134,184,70); border-color:{ACCENT}; }}"
    )


def primary_button_css(min_height: int = 36) -> str:
    """The committed action. Gold edge marks it as the one thing to press."""
    return (
        "QPushButton {"
        f" color:#04121a; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f"  stop:0 {ACCENT_BRIGHT}, stop:1 {ACCENT});"
        f" border:1px solid {ACCENT_BRIGHT}; border-radius:{RADIUS_SM}px;"
        f" font:800 8pt {FONT_SMALL}; letter-spacing:1px;"
        f" min-height:{min_height}px; padding:6px 15px; }}"
        "QPushButton:hover {"
        f" background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f"  stop:0 #b6f2ff, stop:1 {ACCENT_BRIGHT}); }}"
        "QPushButton:pressed {"
        f" background:{ACCENT_DEEP}; color:{TEXT_BRIGHT}; }}"
    )


def accent_button_css(min_height: int = 34) -> str:
    """Gold signature button — use sparingly for a single 'armed/live' action."""
    return (
        "QPushButton {"
        f" color:#1c1204; background:rgba(244,178,74,30);"
        f" border:1px solid {GOLD}; border-radius:{RADIUS_SM}px;"
        f" font:800 8pt {FONT_SMALL}; letter-spacing:1px;"
        f" min-height:{min_height}px; padding:6px 13px; }}"
        f"QPushButton {{ color:{GOLD}; }}"
        "QPushButton:hover {"
        f" color:#1c1204; background:{GOLD}; border-color:#ffd48a; }}"
        f"QPushButton:pressed {{ background:{GOLD_DEEP}; color:#1c1204; }}"
    )


def field_css() -> str:
    return (
        "QLineEdit,QComboBox,QTextEdit,QListWidget,QSpinBox,QDoubleSpinBox "
        "{background:rgba(3,17,29,185);"
        f"color:{TEXT_BRIGHT};"
        f"border:1px solid {BORDER_FAINT};border-radius:{RADIUS_MD}px;"
        f"padding:6px 11px;font:9pt {FONT_UI};"
        f"selection-background-color:rgba(10,134,184,150);selection-color:{TEXT_BRIGHT};}}"
        "QLineEdit:hover,QComboBox:hover,QTextEdit:hover,QSpinBox:hover,QDoubleSpinBox:hover "
        f"{{border-color:{BORDER_SOFT};}}"
        "QLineEdit:focus,QComboBox:focus,QTextEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus "
        f"{{border-color:{ACCENT};background:rgba(4,26,40,225);}}"
        "QComboBox::drop-down{width:26px;border:none;background:transparent;}"
        f"QComboBox QAbstractItemView{{background:{BG};color:{TEXT};"
        f"border:1px solid {BORDER_SOFT};border-radius:{RADIUS_SM}px;padding:4px;"
        "selection-background-color:rgba(10,134,184,140);selection-color:#f0fdff;}"
        "QListWidget::item{padding:7px 9px;border:1px solid transparent;border-radius:5px;margin:1px 0;}"
        "QListWidget::item:selected{background:rgba(10,134,184,90);color:#f0fdff;"
        f"border-color:{BORDER_SOFT};}}"
        "QListWidget::item:hover{background:rgba(10,134,184,36);}"
    )


def card_css() -> str:
    return (
        f"QFrame {{ background:{CARD}; border:1px solid {BORDER_FAINT};"
        f" border-radius:{RADIUS_LG}px; }}"
    )


def header_css() -> str:
    return (
        f"QFrame {{ background:{HEADER}; border:1px solid {BORDER_SOFT};"
        f" border-radius:{RADIUS_MD}px; }}"
    )


def chip_css() -> str:
    return (
        f"color:{ACCENT_BRIGHT}; background:rgba(10,134,184,52);"
        f"border:1px solid {BORDER_FAINT}; border-radius:{RADIUS_SM}px;"
        "font:700 7pt 'Exo 2'; letter-spacing:1px; padding:5px 10px;"
    )


def section_css() -> str:
    return (
        f"color:{ACCENT}; font:800 8pt {FONT_HEAD}; letter-spacing:2px;"
        " background:transparent; padding:2px 2px 6px 2px;"
    )


def title_css(size: str = "10pt") -> str:
    return (
        f"color:{TEXT}; font:700 {size} {FONT_HEAD}; letter-spacing:3px;"
        " background:transparent; border:none;"
    )


def subtitle_css() -> str:
    return (
        f"color:{TEXT_FAINT}; font:600 6.5pt {FONT_SMALL}; letter-spacing:1px;"
        " background:transparent; border:none;"
    )


def scrollbar_css(width: int = 6) -> str:
    return (
        f"QScrollBar:vertical {{ background:transparent; width:{width}px; border:none; margin:2px; }}"
        "QScrollBar::handle:vertical { background:rgba(30,116,142,150); border-radius:3px; min-height:26px; }"
        f"QScrollBar::handle:vertical:hover {{ background:{ACCENT}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0px; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }"
        f"QScrollBar:horizontal {{ background:transparent; height:{width}px; border:none; margin:2px; }}"
        "QScrollBar::handle:horizontal { background:rgba(30,116,142,150); border-radius:3px; min-width:26px; }"
        f"QScrollBar::handle:horizontal:hover {{ background:{ACCENT}; }}"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0px; }"
        "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }"
    )


def hidden_scrollbar_css() -> str:
    return (
        "QScrollBar:vertical { background:transparent; width:0px; border:none; }"
        "QScrollBar::handle:vertical { background:transparent; border:none; }"
        "QScrollBar:horizontal { background:transparent; height:0px; border:none; }"
        "QScrollBar::handle:horizontal { background:transparent; border:none; }"
    )


def tab_css() -> str:
    return (
        f"QPushButton {{ color:{TEXT_DIM}; background:rgba(5,22,34,70); border:1px solid transparent;"
        f" border-radius:{RADIUS_SM}px; font:700 7pt 'Exo 2'; letter-spacing:1px; padding:6px 10px; }}"
        f"QPushButton:hover {{ color:{TEXT_BRIGHT}; background:rgba(10,134,184,44); }}"
        f"QPushButton:checked {{ color:{TEXT_BRIGHT}; background:rgba(10,134,184,80);"
        f" border-color:{BORDER_SOFT}; }}"
    )


def icon_font_css() -> str:
    return "font-family:'Segoe UI Symbol','Segoe UI Emoji','Rajdhani','Segoe UI',sans-serif;"


def slider_css() -> str:
    return (
        "QSlider::groove:horizontal { height:4px; background:rgba(3,20,30,180);"
        f" border:1px solid {BORDER_FAINT}; border-radius:2px; }}"
        "QSlider::sub-page:horizontal { height:4px;"
        f" background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT_DEEP}, stop:1 {ACCENT});"
        " border:none; border-radius:2px; }"
        "QSlider::add-page:horizontal { background:rgba(3,18,26,150); border-radius:2px; }"
        f"QSlider::handle:horizontal {{ width:14px; height:14px; margin:-6px 0; background:{TEXT_BRIGHT};"
        f" border:2px solid {ACCENT}; border-radius:7px; }}"
        f"QSlider::handle:horizontal:hover {{ background:#ffffff; border-color:{ACCENT_BRIGHT}; }}"
    )


def checkbox_css() -> str:
    return (
        f"QCheckBox {{ spacing:8px; padding:6px 2px; color:{TEXT_MID}; background:transparent;"
        f" font:600 9pt {FONT_UI}; }}"
        "QCheckBox::indicator { width:15px; height:15px; border:1px solid #1f5f76;"
        " background:rgba(3,17,26,150); border-radius:4px; }"
        f"QCheckBox::indicator:hover {{ border-color:{ACCENT}; }}"
        f"QCheckBox::indicator:checked {{ background:{ACCENT}; border-color:{ACCENT_BRIGHT}; }}"
    )


def dialog_css() -> str:
    return (
        f"QDialog {{ background:{PANEL}; border:1px solid {BORDER_SOFT};"
        f" border-radius:{RADIUS_LG}px; color:{TEXT}; }}"
    )


def tooltip_css() -> str:
    return (
        f"QToolTip {{ background:{BG_DEEP}; color:{TEXT_BRIGHT};"
        f" border:1px solid {ACCENT_DEEP}; border-radius:5px; padding:5px 8px;"
        " font:8pt 'Exo 2'; }"
    )


STATUS_COLORS = {
    "ok": GREEN, "run": ACCENT, "think": ACCENT_BRIGHT, "warn": AMBER,
    "error": RED, "dim": TEXT_FAINT, "you": TEXT_BRIGHT, "ai": ACCENT_BRIGHT,
    "sys": TEXT_DIM, "live": GOLD,
}


def status_color(kind: str) -> str:
    return STATUS_COLORS.get(str(kind or "").lower(), STATUS_COLORS["dim"])


def meter_color(pct: float) -> str:
    try:
        pct = float(pct)
    except Exception:
        return ACCENT
    if pct >= 90:
        return RED
    if pct >= 70:
        return AMBER
    return ACCENT


# ── The one palette ─────────────────────────────────────────────────────────
# ui.py's `class C` is an alias table onto this mapping, not a palette of its
# own: every value here is either a canonical token above or a derived form of
# one, so there is exactly one place in the project where a colour is decided.
#
# All values are opaque '#rrggbb' because the legacy call sites pass them to
# QColor()/QPen() (which reject rgba strings) as well as into QSS.
PALETTE: dict[str, str] = {
    # Surfaces
    "BG":        hex_color(BG),
    "PANEL":     SURFACE,
    "PANEL2":    SURFACE_ALT,
    "DARK":      hex_color(BG_DEEP),
    "BAR_BG":    SURFACE_INSET,
    # Strokes — the same three weights, flattened for painter code
    "BORDER":    STROKE_HAIR,
    "BORDER_A":  STROKE_MID,
    "BORDER_B":  STROKE_STRONG,
    # Accent
    "PRI":       hex_color(ACCENT),
    "PRI_DIM":   hex_color(ACCENT_DEEP),
    "PRI_GHO":   hex_color(ACCENT_INK),
    # Text ramp
    "TEXT":      hex_color(TEXT),
    "TEXT_DIM":  hex_color(TEXT_DIM),
    "TEXT_MED":  hex_color(TEXT_MID),
    "WHITE":     hex_color(TEXT_BRIGHT),
    # Signature + status
    "ACC":       hex_color(GOLD),
    "ACC2":      hex_color(AMBER),
    "GREEN":     hex_color(GREEN),
    "GREEN_D":   hex_color(GREEN_DEEP),
    "RED":       hex_color(RED),
    "MUTED_C":   hex_color(RED),
    "GOLD":      hex_color(GOLD),
}

# Keys whose hue follows the user's ui_color. Everything else is fixed, so a
# user accent can never turn a warning green.
HUE_LINKED: tuple[str, ...] = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)

PALETTE_DEFAULTS: dict[str, str] = {k: PALETTE[k] for k in HUE_LINKED}

DEFAULT_ACCENT = PALETTE["PRI"]

# The accent values this project shipped before the token consolidation. They
# are mapped to the current accent so an existing install does not keep the
# retired palette alive; anything else the user picked is left alone.
LEGACY_ACCENTS: dict[str, str] = {
    "#00d4ff": DEFAULT_ACCENT,
    "#007a99": PALETTE["PRI_DIM"],
    "#001f2e": PALETTE["PRI_GHO"],
}


def migrate_accent(value: str) -> str:
    """Map a legacy accent value onto the current token; pass others through."""
    key = str(value or "").strip().lower()
    return LEGACY_ACCENTS.get(key, key)


# ── Shared components ───────────────────────────────────────────────────────
# One title treatment, one status chip, one section heading, one control row.
# Every panel and Settings tab renders through these, which is what stops each
# surface from inventing its own title style.

def panel_title_css() -> str:
    """Panel title type role — the same on every surface."""
    return (
        f"color:{TEXT_BRIGHT}; font:800 9.5pt {FONT_HEAD}; letter-spacing:1.8px;"
        " background:transparent; border:none;"
    )


def panel_subtitle_css() -> str:
    """Micro-label under a panel title (SYSTEM MEMORY / LIVE TELEMETRY …)."""
    return (
        f"color:{TEXT_FAINT}; font:700 6.5pt {FONT_SMALL}; letter-spacing:1.4px;"
        " background:transparent; border:none;"
    )


def panel_icon_css() -> str:
    return (
        f'color:{ACCENT}; font:800 15pt {FONT_UI}; letter-spacing:0;'
        " background:transparent; border:none; padding:0;"
    )


def status_chip_css(kind: str = "live") -> str:
    """The small state marker in a header — gold only for live/armed."""
    color = status_color(kind)
    return (
        f"color:{color}; font:800 6.5pt {FONT_SMALL}; letter-spacing:1px;"
        " background:transparent; border:none;"
    )


def separator_css() -> str:
    """The hairline that runs between a title and its status marker."""
    return f"background:{rgba(ACCENT, 46)}; border:none;"


def micro_label_css() -> str:
    """Technical identifier text (TELEMETRY / SYS / NODE 04)."""
    return (
        f"color:{TEXT_FAINT}; font:700 6.5pt {FONT_LOG}; letter-spacing:1.2px;"
        " background:transparent; border:none;"
    )


def shell_dialog_css() -> str:
    """Translucent dialog shell — the painted backdrop supplies the field."""
    return (
        "QDialog { background: transparent;"
        f" color:{TEXT}; }}"
        "QFrame#SettingsRoot { background: transparent; border: none; }"
        f"QLabel {{ color: {TEXT_MID}; background: transparent; }}"
    )


def nav_header_css() -> str:
    """The gradient header band shared by the settings window and panels."""
    return (
        "QFrame#HudHeader {"
        " background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
        f"  stop:0 {rgba(mix(ACCENT, BG, 0.08), 238)},"
        f"  stop:0.55 {rgba(BG, 222)}, stop:1 {rgba(mix(ACCENT, BG, 0.09), 205)});"
        f" border: 1px solid {BORDER_SOFT}; border-radius: {RADIUS_MD}px; }}"
    )


def qtabbar_css() -> str:
    """Left-rail tab bar (QTabWidget). The HUD's only tab treatment."""
    return (
        "QTabWidget { background: transparent; }"
        "QTabWidget::pane { border: none; background: transparent; }"
        "QTabBar { background: transparent; }"
        "QTabBar::tab {"
        " min-width: 154px; min-height: 46px;"
        f" color: {TEXT_DIM}; background: {rgba(mix(ACCENT, BG, 0.05), 92)};"
        f" border: 1px solid {BORDER_FAINT}; border-left: 2px solid transparent;"
        f" border-radius: {RADIUS_MD}px; padding: 8px 13px; margin: 2px 8px 5px 0;"
        f" text-align: left; font: 700 8pt {FONT_SMALL}; }}"
        "QTabBar::tab:hover {"
        f" color: {TEXT_BRIGHT}; background: {rgba(ACCENT, 58)};"
        f" border-color: {BORDER}; }}"
        "QTabBar::tab:selected {"
        f" color: {TEXT_BRIGHT}; background: {rgba(ACCENT, 96)};"
        f" border-color: {BORDER}; border-left: 2px solid {ACCENT}; }}"
    )


def scroll_area_css() -> str:
    return (
        "QScrollArea { border: none; background: transparent; }"
        "QScrollArea > QWidget > QWidget { background: transparent; }"
    )


def group_box_css() -> str:
    """Section container. The final block is the unified finish: no stacked
    frames, just an Orbitron caption over the panel's own background."""
    return (
        "QGroupBox {"
        f" background: {rgba(mix(ACCENT, BG, 0.05), 90)};"
        f" border: 1px solid {BORDER_SOFT}; border-radius: {RADIUS_LG}px;"
        " margin-top: 16px; padding: 16px 14px 13px 14px; }"
        "QGroupBox::title {"
        " subcontrol-origin: margin; left: 14px; padding: 0 8px;"
        f" color: {ACCENT}; background: {BG}; font: 800 8pt {FONT_HEAD}; }}"
        "QGroupBox { background: transparent; border: none;"
        " margin-top: 10px; padding-top: 8px; }"
        f"QGroupBox::title {{ background: transparent; color: {ACCENT}; padding: 0 4px; }}"
    )


def settings_surface_css() -> str:
    """Cards and section frames inside the control center."""
    return (
        "QFrame#TabIntro, QFrame#SettingsCard, QFrame#SettingsSection {"
        f" background: {rgba(mix(ACCENT, BG, 0.04), 115)}; border: none; }}"
    )


def composed_app_css() -> str:
    """The whole window chrome, assembled from the shared component rules.

    Surfaces used to hand-write their own copy of this; composing it here is
    what keeps Settings, the floating panels and the main shell identical.
    """
    return "\n".join((
        shell_dialog_css(), nav_header_css(), qtabbar_css(), scroll_area_css(),
        button_css(), primary_button_css(), accent_button_css(), field_css(),
        checkbox_css(), slider_css(), scrollbar_css(), group_box_css(),
        tooltip_css(), settings_surface_css(),
    ))
