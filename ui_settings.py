"""J.A.R.V.I.S standalone settings/control center.

The settings window is deliberately independent from the compact Arc Reactor.
Opening it never expands, hides, or replaces the main reactor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QFont, QBrush, QPainter, QPen, QPainterPath, QLinearGradient, QRadialGradient, QKeySequence, QShortcut, QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QTabBar, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget, QFileDialog, QDoubleSpinBox
)

from ui_theme import (
    ACCENT, ACCENT_BRIGHT, ACCENT_DEEP, ACCENT_INK, GOLD, GOLD_DEEP,
    GREEN, GREEN_DEEP, AMBER, RED, BG, BG_DEEP, PANEL, CARD, CARD_HI, HEADER,
    BORDER, BORDER_SOFT, BORDER_FAINT, STROKE_STRONG, STROKE_MID, STROKE_HAIR,
    SURFACE, SURFACE_ALT, SURFACE_INSET,
    TEXT, TEXT_BRIGHT, TEXT_MID, TEXT_DIM, TEXT_FAINT,
    RADIUS_LG, RADIUS_MD, RADIUS_SM,
    FONT_UI, FONT_HEAD, FONT_LOG, FONT_SMALL,
    SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL,
    CONTROL_SM, CONTROL_MD, CONTROL_LG,
    DEFAULT_ACCENT, PALETTE, migrate_accent as _migrate_accent,
    rgba as _rgba, mix as _mix, shade as _shade, qcolor as _qcolor,
    flatten as _flatten, hex_color as _hex_color,
    composed_app_css as _composed_app_css,
    button_css as _button_css, primary_button_css as _primary_button_css,
    field_css as _field_css, checkbox_css as _checkbox_css, slider_css as _slider_css,
    scrollbar_css as _scrollbar_css, tooltip_css as _tooltip_css,
    panel_title_css as _panel_title_css, panel_subtitle_css as _panel_subtitle_css,
    panel_icon_css as _panel_icon_css, micro_label_css as _micro_label_css,
    separator_css as _separator_css, status_color as _status_color,
)

DEFAULTS = {
    "compact_opacity": 0,
    "compact_size": 176,
    "ui_opacity": 82,
    "reactor_opacity": 100,
    "reactor_stroke_opacity": 100,
    "reactor_animation_speed": 1.0,
    "equalizer_sensitivity": 1.0,
    "voice_speed": 1.0,
    "voice_pitch": 0.0,
    "voice_volume": 1.0,
    "voice_mode": "standard",
    "tts_engine": "edgetts",
    "tts_voice": "en-GB-RyanNeural",
    "voice_name": "Charon",
    # Primary voice pipeline: the realtime Gemini Live native-audio voice —
    # Gemini speaks the reply itself, with no local synthesis hop. The bundled
    # Piper ONNX voice (jarvis-high) is the Gemini → Piper pipeline's renderer;
    # onnx_execution_provider is cpu | gpu | auto; cpu is the safe default and
    # always works.
    "voice_output_mode": "gemini",
    "onnx_voice_model": "config/voices/jarvis-high.onnx",
    "onnx_voice_config": "config/voices/jarvis-high.json",
    "onnx_voice_speaker": "default",
    "onnx_execution_provider": "cpu",
    "fish_audio_api_key": "",
    "fish_audio_model_id": "s2-pro",
    "fish_audio_voice_id": "",
    "fish_audio_endpoint": "https://api.fish.audio/v1/tts",
    "fish_audio_format": "mp3",
    "fish_audio_latency": "normal",
    "gemini_api_key": "",
    "kokoro_voice": "af_heart",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
    "elevenlabs_model_id": "eleven_multilingual_v2",
    "assistant_name": "JARVIS",
    "user_name": "",
    "wake_words": ["hey jarvis", "jarvis", "okay jarvis", "wake up jarvis"],
    "ai_provider": "gemini",
    "ai_model": "",
    "openai_api_key": "",
    "anthropic_api_key": "",
    "groq_api_key": "",
    "custom_provider_name": "",
    "custom_ai_base_url": "",
    "custom_ai_api_key": "",
    "custom_ai_model": "",
    "ui_font": "Rajdhani",
    "ui_color": DEFAULT_ACCENT,
    "web_homepage": "https://www.google.com",
    "world_monitor_refresh": 60,
    "memory_autosave": True,
    "panel_always_on_top": True,
    "hud_grid": True,
    "ui_x": None,
    "ui_y": None,
    "quick_actions": [],
    "chat_filter_categories": {"sys": True, "vis": True, "jarvis": True, "you": True, "file": True, "err": True, "warn": True},
    "features": {
        "audio_reactive": True,
        "smooth_animations": True,
        "chat_history": True,
        "compact_mode": True,
        "drag_reactor": True,
        "remember_position": True,
        "hide_taskbar_button": False,
        "autostart": False,
        "wake_word": True,
        "hands_free": True,
        "push_to_talk": False,
        "voice_activity_detection": True,
        "voice_interruption": True,
        "barge_in": True,
        "background_listening": True,
        "always_on": False,
        "voice_command_recognition": True,
        "computer_control": True,
        "desktop_awareness": True,
        "screen_understanding": True,
        "mouse_keyboard_control": True,
        "window_control": True,
        "terminal_control": True,
        "file_control": True,
        "desktop_automation": True,
        "webview": True,
        "webview_engine": True,
        "webview_music": True,
        "web_task": True,
        "world_monitor": True,
        "floating_windows": True,
        "draggable_panels": True,
        "enable_sfx": True,
        "proactive_speaking": False,
        "mic_access": True,
        "camera_access": True,
    },
}


def load(path: str | Path) -> dict:
    path = Path(path)
    data = dict(DEFAULTS)
    data["features"] = dict(DEFAULTS["features"])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            # A pipeline recorded by SAVE AS DEFAULT is the default every
            # fresh session starts on — overriding the DEFAULTS constant.
            try:
                _dp = str(((raw.get("voice") or {}).get("defaultPipeline")) or "")
                if _dp in ("gemini", "gemini_piper", "local"):
                    data["voice_output_mode"] = _dp
            except Exception:
                pass
            data.update(raw)
            if isinstance(raw.get("features"), dict):
                data["features"].update(raw["features"])
    except Exception:
        pass
    # A stored accent from the retired palette is mapped onto the current token
    # here, at the single read path, so nothing downstream keeps the old palette
    # alive and the user's own custom colour is passed through untouched.
    data["ui_color"] = _migrate_accent(data.get("ui_color") or DEFAULT_ACCENT)
    return data


def save(path: str | Path, **updates) -> dict:
    path = Path(path)
    data = load(path)
    for k, v in updates.items():
        if k == "features" and isinstance(v, dict):
            merged = dict(data.get("features", {}))
            merged.update(v)
            data[k] = merged
        else:
            data[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return data


def _default_config_path() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "config" / "api_keys.json"


class HorizontalNavBar(QTabBar):
    """Reference-style left rail with horizontal labels and angular cards."""
    def tabSizeHint(self, index):
        base = super().tabSizeHint(index)
        return QSize(150, max(46, base.height()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i in range(self.count()):
            r = self.tabRect(i).adjusted(4, 4, -5, -4)
            cut = 8
            path = QPainterPath()
            path.moveTo(r.left()+cut, r.top())
            path.lineTo(r.right()-cut, r.top())
            path.lineTo(r.right(), r.top()+cut)
            path.lineTo(r.right(), r.bottom()-cut)
            path.lineTo(r.right()-cut, r.bottom())
            path.lineTo(r.left()+cut, r.bottom())
            path.lineTo(r.left(), r.bottom()-cut)
            path.lineTo(r.left(), r.top()+cut)
            path.closeSubpath()
            selected = i == self.currentIndex()
            p.setPen(QPen(QColor(ACCENT if selected else STROKE_HAIR), 1))
            p.fillPath(path, QBrush(_qcolor(ACCENT, 170) if selected else _qcolor(SURFACE_ALT, 92)))
            p.drawPath(path)
            if selected:
                p.setPen(QPen(QColor(ACCENT_BRIGHT), 2))
                p.drawLine(r.left()+2, r.top()+10, r.left()+2, r.bottom()-10)
            p.setPen(QColor(TEXT_BRIGHT if selected else TEXT_FAINT))
            p.setFont(QFont(FONT_SMALL.strip("'"), 8, QFont.Weight.Bold))
            p.drawText(r.adjusted(16, 0, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.tabText(i))
        p.end()


class HudGroupBox(QGroupBox):
    """Angular transparent HUD panel inspired by the supplied reference sheet."""
    def __init__(self, title='', parent=None):
        super().__init__(title, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setProperty('_jarvis_floating_panel', True)
        self.setProperty('_jarvis_panel_kind', 'control_center')
        self.setProperty('_jarvis_panel_key', 'control_center')
        self.setObjectName('HudGroupBox')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(1, 1, -1, -1)
        cut = 12
        path = QPainterPath()
        pts = [
            QPointF(r.left()+cut, r.top()), QPointF(r.right()-cut, r.top()),
            QPointF(r.right(), r.top()+cut), QPointF(r.right(), r.bottom()-cut),
            QPointF(r.right()-cut, r.bottom()), QPointF(r.left()+cut, r.bottom()),
            QPointF(r.left(), r.bottom()-cut), QPointF(r.left(), r.top()+cut),
        ]
        path.moveTo(pts[0])
        for pt in pts[1:]: path.lineTo(pt)
        path.closeSubpath()
        p.fillPath(path, QBrush(_qcolor(SURFACE, 158)))
        p.setPen(QPen(QColor(STROKE_MID), 1))
        p.drawPath(path)
        # Title cutout and caption.
        title = self.title()
        if title:
            f = QFont(FONT_HEAD.strip("'"), 7, QFont.Weight.Bold)
            fm = p.fontMetrics(); tw = fm.horizontalAdvance(title) + 18
            tr = QRectF(r.left()+10, r.top()-7, tw, 16)
            p.setPen(QColor(ACCENT)); p.setFont(f)
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, title)
        p.end()


class HudHeader(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('HudHeader')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(1,1,-1,-1)
        cut = 9
        path = QPainterPath()
        path.moveTo(r.left()+cut, r.top()); path.lineTo(r.right()-22, r.top())
        path.lineTo(r.right(), r.top()+22); path.lineTo(r.right(), r.bottom()-cut)
        path.lineTo(r.right()-cut, r.bottom()); path.lineTo(r.left()+cut, r.bottom())
        path.lineTo(r.left(), r.bottom()-cut); path.lineTo(r.left(), r.top()+cut); path.closeSubpath()
        p.fillPath(path, QBrush(_qcolor(SURFACE, 160)))
        p.setPen(QPen(QColor(STROKE_STRONG), 1))
        p.drawPath(path)
        p.end()


def _hud_cut_path(rect, cut=14):
    path = QPainterPath()
    cut = max(8, min(cut, min(rect.width(), rect.height()) / 6))
    path.moveTo(QPointF(rect.left() + cut, rect.top()))
    path.lineTo(QPointF(rect.right() - cut, rect.top()))
    path.lineTo(QPointF(rect.right(), rect.top() + cut))
    path.lineTo(QPointF(rect.right(), rect.bottom() - cut))
    path.lineTo(QPointF(rect.right() - cut, rect.bottom()))
    path.lineTo(QPointF(rect.left() + cut, rect.bottom()))
    path.lineTo(QPointF(rect.left(), rect.bottom() - cut))
    path.lineTo(QPointF(rect.left(), rect.top() + cut))
    path.closeSubpath()
    return path


class _ControlCenterBackdrop(QFrame):
    """Angular grid-and-glow background used by the standalone control center."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = max(1, self.width()), max(1, self.height())
            bounds = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
            cut = max(12, min(18, min(w, h) * 0.03))
            clip = _hud_cut_path(bounds, cut)
            p.setClipPath(clip)
            gradient = QLinearGradient(0, 0, w, h)
            gradient.setColorAt(0.0, _qcolor(SURFACE, 238))
            gradient.setColorAt(0.5, _qcolor(BG_DEEP, 246))
            gradient.setColorAt(1.0, _qcolor(_mix(ACCENT, BG, 0.07), 234))
            p.fillPath(clip, QBrush(gradient))

            glow = QRadialGradient(QPointF(w * 0.80, h * 0.16), max(w, h) * 0.72)
            glow.setColorAt(0.0, _qcolor(ACCENT, 40))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillPath(clip, QBrush(glow))

            # A single warm counterpoint low-left so the cold field has depth.
            gold = QRadialGradient(QPointF(w * 0.10, h * 0.92), max(w, h) * 0.55)
            gold.setColorAt(0.0, _qcolor(GOLD, 16))
            gold.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillPath(clip, QBrush(gold))

            p.setPen(QPen(_qcolor(BORDER_FAINT, 22), 1))
            for x in range(0, w + 28, 28):
                p.drawLine(x, 0, x, h)
            for y in range(0, h + 28, 28):
                p.drawLine(0, y, w, y)
            # Soft accent atmosphere (bounded to three layers), then one crisp frame.
            for width, alpha in ((7.0, 10), (4.0, 18), (2.0, 34)):
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_qcolor(BORDER, alpha), width))
                p.drawPath(clip)
            p.setPen(QPen(_qcolor(BORDER_SOFT, 72), 1))
            p.drawPath(clip)
            p.setClipping(False)
        except Exception:
            pass
        p.end()


class _SettingsRoot(QFrame):
    """Root shell that keeps the painted backdrop fitted to the dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._futuristic_backdrop = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_backdrop()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_backdrop()

    def _fit_backdrop(self):
        backdrop = self._futuristic_backdrop
        if backdrop is not None:
            backdrop.setGeometry(self.rect())
            backdrop.lower()


class SettingsWindow(QDialog):
    """Full-featured tabbed control center for J.A.R.V.I.S.f"""

    settings_saved = pyqtSignal(dict)
    voice_test_requested = pyqtSignal(str)

    def __init__(self, config_path: str | Path | None = None, parent=None):
        super().__init__(parent)
        self._config_path = Path(config_path) if config_path else _default_config_path()
        self._data = load(self._config_path)
        self._color = str(self._data.get("ui_color", DEFAULTS["ui_color"]))
        self._drag_offset = None
        self._layout_rows: dict[str, dict] = {}
        self._layout_selected_name = None

        self.setWindowTitle("J.A.R.V.I.S Settings")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setProperty('_jarvis_floating_panel', True)
        self.setProperty('_jarvis_panel_kind', 'control_center')
        self.setProperty('_jarvis_panel_key', 'control_center')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(940, 680)
        self.resize(1120, 760)

        self._build_style()
        self._build_ui()
        self._load_layout_values()
        self.tabs.currentChanged.connect(self._sync_nav_status)
        self._sync_nav_status(self.tabs.currentIndex())

    def _build_style(self):
        # The control center no longer carries its own copy of the visual
        # language: the chrome is composed from the shared component rules, so
        # this window, the floating panels and the main shell cannot drift apart.
        self.setStyleSheet(_composed_app_css())

    def _build_ui(self):
        root = _SettingsRoot(self)
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.addWidget(root)
        backdrop = _ControlCenterBackdrop(root)
        root._futuristic_backdrop = backdrop
        root._fit_backdrop()

        outer = QVBoxLayout(root)
        outer.setContentsMargins(11, 11, 11, 11)
        outer.setSpacing(12)

        header = HudHeader(root)
        header.setFixedHeight(68)
        hb = QHBoxLayout(header)
        hb.setContentsMargins(18, 9, 10, 9)
        hb.setSpacing(14)

        icon = QLabel("◈")
        icon.setStyleSheet('color:#56d6f5;font:700 20pt "Segoe UI Symbol";background:transparent;')
        hb.addWidget(icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("J.A.R.V.I.S  //  CONTROL CENTER")
        title.setMinimumWidth(300)
        title.setWordWrap(False)
        title.setStyleSheet(f'color:{TEXT_BRIGHT};font:800 12pt "Orbitron";background:transparent; letter-spacing:2px;')
        subtitle = QLabel("GENERAL • AI • VOICE • SYSTEM • MEMORY • PLUGINS")
        subtitle.setStyleSheet(f'color:{TEXT_FAINT};font:700 7pt "Exo 2";background:transparent; letter-spacing:1px;')
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        hb.addLayout(title_box)
        hb.addStretch(1)

        self._status = QLabel("●  READY  //  CONTROL CENTER")
        self._status.setStyleSheet(f'color:{GREEN};font:800 8pt "Exo 2";background:rgba(9,64,50,90);padding:7px 12px;border:1px solid rgba(93,240,182,85);border-radius:7px; letter-spacing:1px;')
        hb.addWidget(self._status)
        close = QPushButton("×")
        close.setFixedSize(34, 34)
        close.setToolTip("Close control center")
        close.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_DIM}; border: none; border-radius: 7px; font: 700 16pt 'Rajdhani'; padding: 0px; }} QPushButton:hover {{ color: {RED}; background: rgba(60, 10, 20, 130); }}")
        close.clicked.connect(self.close)
        hb.addWidget(close)
        outer.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabBar(HorizontalNavBar())
        self.tabs.setTabPosition(QTabWidget.TabPosition.West)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        body.addWidget(self.tabs, 1)
        outer.addLayout(body, 1)

        self._build_general_tab()
        self._build_ai_tab()
        self._build_voice_tab()
        self._build_personality_tab()
        self._build_system_tab()
        self._build_automation_tab()
        self._build_memory_settings_tab()
        self._build_permissions_tab()
        self._build_layout_tab()
        self._build_plugins_tab()
        self._build_diagnostics_tab()
        self._build_web_tab()
        self._build_3d_tab()
        self._build_video_tab()
        self._build_image_tab()

        footer = QHBoxLayout()
        footer.setSpacing(8)
        hint = QLabel("CHANGES ARE STORED IN CONFIG / API_KEYS.JSON")
        hint.setStyleSheet(f'color:{TEXT_FAINT};font:600 7pt "Exo 2";padding-left:4px;')
        footer.addWidget(hint)
        footer.addStretch(1)
        reset = QPushButton("RESET")
        reset.setToolTip("Restore default J.A.R.V.I.S settings")
        reset.clicked.connect(self.reset_defaults)
        footer.addWidget(reset)
        save_btn = QPushButton("SAVE CHANGES")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_settings)
        footer.addWidget(save_btn)
        outer.addLayout(footer)

        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move
        header.mouseReleaseEvent = self._header_release
        title.mousePressEvent = self._header_press
        title.mouseMoveEvent = self._header_move
        title.mouseReleaseEvent = self._header_release

        self._close_shortcut = QShortcut(QKeySequence("Shift+Home"), self)
        self._close_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._close_shortcut.activated.connect(self.close)

    def _sync_nav_status(self, index: int):
        labels = ["GENERAL", "AI", "VOICE", "PERSONALITY", "SYSTEM", "AUTOMATION",
                  "MEMORY", "PERMISSIONS", "LAYOUT", "PLUGINS", "DIAG", "WEB",
                  "3D", "VIDEO", "IMAGE"]
        if 0 <= index < len(labels):
            self._status.setText(f"●  {labels[index]}")

    def _scroll_tab(self):
        page = QWidget()
        page.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        page.setStyleSheet('background: transparent;')
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet('QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }')
        area.viewport().setStyleSheet('background: transparent;')
        area.setWidget(page)
        return page, QVBoxLayout(page), area

    def _tab_intro(self, lay, text):
        row = QFrame()
        row.setObjectName("TabIntro")
        row.setStyleSheet("""
            QFrame#TabIntro {
                background: rgba(1,18,29,115);
                border: 1px solid rgba(55,150,181,75);
                border-radius: 10px;
                padding: 2px;
            }
        f""")
        rv = QHBoxLayout(row)
        rv.setContentsMargins(11, 7, 11, 7)
        rv.setSpacing(9)
        marker = QLabel("◈")
        marker.setStyleSheet(f'color:{ACCENT};font:700 12pt "Segoe UI Symbol";background:transparent;')
        label = QLabel(text)
        label.setStyleSheet(f'color:{ACCENT};font:800 8.5pt "Orbitron";background:transparent;letter-spacing:2px;')
        rv.addWidget(marker)
        rv.addWidget(label, 1)
        telemetry = QLabel("JARVIS CONTROL MATRIX")
        telemetry.setStyleSheet(f'color:{TEXT_FAINT};font:700 6.5pt "Exo 2";background:transparent;letter-spacing:1px;')
        rv.addWidget(telemetry)
        lay.addWidget(row)

    def _build_general_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "GENERAL  //  THEME · REACTOR · WINDOWS")

        appearance = HudGroupBox("APPEARANCE / THEME")
        form = QFormLayout(appearance)
        self.font = QComboBox()
        fonts = ["Rajdhani", "Orbitron", "Share Tech Mono", "Exo 2", "Segoe UI", "Segoe UI Variable", "Consolas", "Cascadia Code", "JetBrains Mono", "Arial", "Tahoma"]
        self.font.addItems(fonts)
        self.font.setCurrentText(str(self._data.get("ui_font", "Rajdhani")))
        form.addRow("Font", self.font)
        self.color_btn = QPushButton(self._color)
        self.color_btn.clicked.connect(self.pick_color)
        form.addRow("Accent color", self.color_btn)
        quick = QHBoxLayout()
        for label, color in [("CYAN", ACCENT), ("RED", RED), ("GREEN", GREEN), ("GOLD", GOLD), ("WHITE", TEXT_BRIGHT)]:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=color: self._set_color(c))
            quick.addWidget(b)
        form.addRow("Presets", quick)
        lay.addWidget(appearance)

        reactor = HudGroupBox("REACTOR / UI")
        rv = QVBoxLayout(reactor)
        self.ui_opacity = self._slider_row(rv, "Glass opacity", int(self._data.get("ui_opacity", 82)), 0, 100, "%")
        self.reactor_opacity = self._slider_row(rv, "Reactor opacity", int(self._data.get("reactor_opacity", 60)), 0, 100, "%")
        self.reactor_stroke_opacity = self._slider_row(rv, "Reactor stroke opacity", int(self._data.get("reactor_stroke_opacity", 100)), 0, 100, "%")
        self.anim_speed = self._slider_row(rv, "Animation speed", int(float(self._data.get("reactor_animation_speed", 1.0))*100), 25, 250, "%")
        self.eq_sens = self._slider_row(rv, "Equalizer sensitivity", int(float(self._data.get("equalizer_sensitivity", 1.0))*100), 10, 300, "%")
        self.reactor_size = self._slider_row(rv, "Arc Reactor size", int(self._data.get("compact_size", 176)), 120, 500, " px")
        # Resizing (or tweaking) auto-saves so the reactor applies + persists live.
        for _sl in (self.ui_opacity, self.reactor_opacity, self.reactor_stroke_opacity,
                    self.anim_speed, self.eq_sens, self.reactor_size):
            try:
                _sl.sliderReleased.connect(self._autosave_quiet)
            except Exception:
                pass
        lay.addWidget(reactor)

        behavior = HudGroupBox("WINDOW BEHAVIOR")
        bv = QVBoxLayout(behavior)
        self._compact = QCheckBox("Always keep the main HUD compact")
        # Loaded from the saved value. It used to be forced to True, so the
        # checkbox in SYSTEM → Features and this one silently overwrote each
        # other and the HUD always started compact whatever you picked.
        self._compact.setChecked(
            bool((self._data.get("features", {}) or {}).get("compact_mode", True)))
        # The compact Arc Reactor is the only HUD this build renders (the old
        # expanded layout was removed), so the switch cannot change anything.
        # It says so instead of pretending to.
        self._compact.setEnabled(False)
        self._compact.setToolTip(
            "The compact Arc Reactor is the only HUD in this build — the "
            "expanded layout was removed, so this cannot be turned off.")
        self._drag = QCheckBox("Allow the small Arc Reactor button to be dragged")
        self._drag.setChecked(bool(self._data.get("features", {}).get("drag_reactor", True)))
        self._remember = QCheckBox("Remember Arc Reactor position")
        self._remember.setChecked(bool(self._data.get("features", {}).get("remember_position", True)))
        self._remember.setToolTip("Save and restore the compact reactor window position.")
        for cb in (self._compact, self._drag, self._remember):
            bv.addWidget(cb)
        lay.addWidget(behavior)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "GENERAL")

    def _build_voice_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "VOICE  //  PIPELINE · PROVIDERS · TESTS")
        voice = HudGroupBox("VOICE ASSISTANT")
        vv = QVBoxLayout(voice)

        # ── 0 · Pipeline (who renders the reply, provider-independent) ──
        pipeline_box = HudGroupBox("0 · PIPELINE")
        ef = QFormLayout(pipeline_box)
        self.voice_output_mode = QComboBox()
        self.voice_output_mode.addItem("Gemini Native Audio", userData="gemini")
        self.voice_output_mode.addItem("Gemini → Piper ONNX", userData="gemini_piper")
        self.voice_output_mode.addItem("Local Piper Only", userData="local")
        # Preselect what the RUNTIME resolves, not the raw stored string: the
        # pipeline recorded by SAVE AS DEFAULT (voice.defaultPipeline) wins,
        # then the stored mode with its one-time lifts applied (see
        # normalize_voice_output_mode).
        _cur_vom = str(self._data.get("voice_output_mode", "") or "")
        try:
            from core.hybrid_voice import resolve_voice_mode
            try:
                from voice.providers_config import providers_block as _pb
                _dp = str(_pb(self._data).get("defaultPipeline", "") or "")
            except Exception:
                _dp = ""
            if _dp:
                _cur_vom, _ = resolve_voice_mode({**self._data, "voice_output_mode": _dp})
            else:
                _cur_vom, _ = resolve_voice_mode(self._data)
        except Exception:
            _cur_vom = _cur_vom or "gemini"
        _vi = self.voice_output_mode.findData(_cur_vom)
        self.voice_output_mode.setCurrentIndex(_vi if _vi >= 0 else 0)
        self.voice_output_mode.setToolTip(
            "Gemini Native Audio (default): the realtime Gemini Live voice speaks the "
            "reply itself — lowest latency. Gemini → Piper ONNX: Gemini handles the "
            "conversation and JARVIS speaks the reply through the local Piper voice — "
            "Gemini's own audio is not played. Local Piper Only: every reply is spoken "
            "locally, by the selected provider."
        )
        ef.addRow("Voice output", self.voice_output_mode)
        # SAVE persists the selected pipeline for this install (and syncs the
        # Gemini renderer so the runtime actually follows this combo);
        # SAVE AS DEFAULT additionally records it as the startup default for
        # fresh sessions (config ▸ voice.defaultPipeline, read by load()).
        pipe_row = QHBoxLayout()
        pipe_row.setSpacing(6)
        pipe_save = QPushButton("SAVE")
        pipe_save.setToolTip("Persist the selected pipeline as this install's voice renderer.")
        pipe_save.clicked.connect(self._save_pipeline)
        pipe_row.addWidget(pipe_save)
        pipe_default = QPushButton("SAVE AS DEFAULT")
        pipe_default.setToolTip("Save the pipeline and record it as the default every new session starts on.")
        pipe_default.clicked.connect(self._save_pipeline_as_default)
        pipe_row.addWidget(pipe_default)
        pipe_row.addStretch(1)
        ef.addRow("", pipe_row)
        self._pipeline_status = QLabel("")
        self._pipeline_status.setWordWrap(True)
        self._pipeline_status.setStyleSheet(f"color:{TEXT_DIM};font:7pt 'Exo 2';background:transparent;")
        ef.addRow("", self._pipeline_status)
        test_voice = QPushButton("TEST VOICE LINK")
        test_voice.setToolTip("Ask the active J.A.R.V.I.S session to speak a short professional greeting.")
        test_voice.clicked.connect(self._test_voice_link)
        ef.addRow("", test_voice)
        vv.addWidget(pipeline_box)

        # ── 1 · Provider selector + one isolated panel per provider ──
        provider_box = HudGroupBox("1 · SPEECH PROVIDER (only the selected provider's settings apply)")
        pf = QFormLayout(provider_box)
        self.provider_selector = QComboBox()
        try:
            from voice.providers_config import PROVIDER_IDS as _PIDS
        except Exception:
            _PIDS = ["fishAudio", "elevenLabs", "gemini", "piperOnnx", "edgeTts", "kokoro", "sapi"]
        self._PROVIDER_LABELS = {
            "fishAudio": "Fish Audio",
            "elevenLabs": "ElevenLabs",
            "gemini": "Gemini",
            "piperOnnx": "Piper / ONNX",
            "edgeTts": "Edge TTS",
            "kokoro": "Kokoro",
            "sapi": "Windows SAPI",
        }
        for _pid in _PIDS:
            self.provider_selector.addItem(self._PROVIDER_LABELS.get(_pid, _pid), userData=_pid)
        try:
            from voice.providers_config import active_provider as _active_provider
            _cur_provider = _active_provider(self._data)
        except Exception:
            _cur_provider = str(self._data.get("tts_engine", "edgetts"))
        _pi = self.provider_selector.findData(_cur_provider)
        self.provider_selector.setCurrentIndex(_pi if _pi >= 0 else 4)  # edgeTts
        self.provider_selector.setToolTip(
            "Who renders local speech. Each provider keeps its OWN settings — "
            "switching or saving one never touches another."
        )
        pf.addRow("Provider", self.provider_selector)

        self.provider_stack = QStackedWidget()
        self._provider_panels = {}
        self._build_fish_panel(); self.provider_stack.addWidget(self._provider_panels["fishAudio"])
        self._build_eleven_panel(); self.provider_stack.addWidget(self._provider_panels["elevenLabs"])
        self._build_gemini_panel(); self.provider_stack.addWidget(self._provider_panels["gemini"])
        self._build_piper_panel(); self.provider_stack.addWidget(self._provider_panels["piperOnnx"])
        self._build_edge_panel(); self.provider_stack.addWidget(self._provider_panels["edgeTts"])
        self._build_kokoro_panel(); self.provider_stack.addWidget(self._provider_panels["kokoro"])
        self._build_sapi_panel(); self.provider_stack.addWidget(self._provider_panels["sapi"])
        self.provider_selector.currentIndexChanged.connect(self._on_provider_selected)
        self._on_provider_selected(self.provider_selector.currentIndex())
        pf.addRow("", self.provider_stack)
        vv.addWidget(provider_box)
        lay.addWidget(voice)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "VOICE")

    # -- per-provider panels -------------------------------------------------
    def _panel_buttons(self, pid: str, with_refresh: bool = False) -> QFormLayout:
        """The shared Refresh/Test/Save/Save-as-Default row for one panel."""
        form = self._provider_forms[pid]
        row = QHBoxLayout()
        if with_refresh:
            refresh = QPushButton("REFRESH")
            refresh.setToolTip(f"Fetch the {self._PROVIDER_LABELS[pid]} voice list into this panel only.")
            refresh.clicked.connect(lambda _=False, p=pid: self._refresh_provider_voices(p))
            row.addWidget(refresh)
        test = QPushButton("TEST VOICE")
        test.setToolTip(f"Speak a short test line through {self._PROVIDER_LABELS[pid]} with the values currently in this panel.")
        test.clicked.connect(lambda _=False, p=pid: self._test_provider(p))
        row.addWidget(test)
        save = QPushButton("SAVE")
        save.setToolTip(f"Validate and save ONLY the {self._PROVIDER_LABELS[pid]} settings, and make it the active provider.")
        save.clicked.connect(lambda _=False, p=pid: self._save_provider_panel(p, as_default=False))
        row.addWidget(save)
        default = QPushButton("SAVE AS DEFAULT")
        default.setToolTip(f"Save {self._PROVIDER_LABELS[pid]} and store provider + voice as the validated default pair.")
        default.clicked.connect(lambda _=False, p=pid: self._save_provider_panel(p, as_default=True))
        row.addWidget(default)
        form.addRow("", row)
        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet(f"color:{TEXT_DIM};font:7pt 'Exo 2';background:transparent;")
        form.addRow("", status)
        self._provider_status[pid] = status
        return form

    def _build_fish_panel(self):
        box = HudGroupBox("FISH AUDIO (cloud)")
        ff = QFormLayout(box)
        self._provider_forms = getattr(self, "_provider_forms", {})
        self._provider_status = getattr(self, "_provider_status", {})
        self._provider_forms["fishAudio"] = ff
        self.fish_api_key = QLineEdit(str(self._data.get("fish_audio_api_key", "")))
        self.fish_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.fish_api_key.setPlaceholderText("Fish Audio API key")
        ff.addRow("API key", self.fish_api_key)
        self.fish_model_id = QLineEdit(str(self._data.get("fish_audio_model_id", "s2-pro")))
        self.fish_model_id.setPlaceholderText("Backend: s1 or s2-pro")
        self.fish_model_id.setToolTip("Fish Audio backend model (e.g. s1, s2.1-pro). A voice ID typed here is auto-detected as the voice.")
        ff.addRow("Model", self.fish_model_id)
        self.fish_voice_id = QLineEdit(str(self._data.get("fish_audio_voice_id", "")))
        self.fish_voice_id.setPlaceholderText("Reference voice ID")
        ff.addRow("Voice ID", self.fish_voice_id)
        self.fish_endpoint = QLineEdit(str(self._data.get("fish_audio_endpoint", DEFAULTS["fish_audio_endpoint"])))
        ff.addRow("Endpoint", self.fish_endpoint)
        fish_row = QHBoxLayout()
        self.fish_format = QComboBox()
        self.fish_format.addItems(["mp3", "wav", "opus"])
        self.fish_format.setCurrentText(str(self._data.get("fish_audio_format", "mp3")))
        fish_row.addWidget(self.fish_format)
        self.fish_latency = QComboBox()
        self.fish_latency.addItems(["normal", "balanced"])
        _lat = str(self._data.get("fish_audio_latency", "normal"))
        self.fish_latency.setCurrentText("balanced" if _lat == "low" else _lat)
        fish_row.addWidget(self.fish_latency)
        ff.addRow("Format / latency", fish_row)
        self._panel_buttons("fishAudio", with_refresh=True)
        self._provider_panels["fishAudio"] = box

    def _build_eleven_panel(self):
        box = HudGroupBox("ELEVENLABS (cloud)")
        elf = QFormLayout(box)
        self._provider_forms["elevenLabs"] = elf
        self.elevenlabs_api_key = QLineEdit(str(self._data.get("elevenlabs_api_key", "")))
        self.elevenlabs_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.elevenlabs_api_key.setPlaceholderText("sk_… ElevenLabs API key")
        elf.addRow("API key", self.elevenlabs_api_key)
        self.elevenlabs_voice_id = QLineEdit(str(self._data.get("elevenlabs_voice_id", DEFAULTS["elevenlabs_voice_id"])))
        self.elevenlabs_voice_id.setPlaceholderText("20-char voice ID (default Adam)")
        self.elevenlabs_voice_id.setToolTip("ElevenLabs voice ID — 20 characters, e.g. pNInz6obpgDQGcFmaJgB (Adam). NOT an Edge name like en-US-GuyNeural.")
        elf.addRow("Voice ID", self.elevenlabs_voice_id)
        self.elevenlabs_model_id = QComboBox()
        self.elevenlabs_model_id.addItems(["eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5"])
        self.elevenlabs_model_id.setCurrentText(str(self._data.get("elevenlabs_model_id", DEFAULTS["elevenlabs_model_id"])))
        elf.addRow("Model", self.elevenlabs_model_id)
        self._panel_buttons("elevenLabs", with_refresh=True)
        self._provider_panels["elevenLabs"] = box

    def _build_gemini_panel(self):
        box = HudGroupBox("GEMINI (cloud · conversation voice)")
        gf = QFormLayout(box)
        self._provider_forms["gemini"] = gf
        self.live_voice = QComboBox()
        self.live_voice.addItems(["Charon", "Puck", "Kore", "Fenrir", "Aoede"])
        self.live_voice.setCurrentText(str(self._data.get("voice_name", "Charon")))
        self.live_voice.setToolTip("Gemini Live voice used by the real-time voice assistant. Belongs to the Gemini provider only.")
        gf.addRow("Live assistant voice", self.live_voice)
        self._panel_buttons("gemini")
        self._provider_panels["gemini"] = box

    def _build_piper_panel(self):
        box = HudGroupBox("PIPER / ONNX (local · offline)")
        onf = QFormLayout(box)
        self._provider_forms["piperOnnx"] = onf
        self.onnx_voice = QComboBox()
        try:
            from core.tts import list_onnx_voices
            self._onnx_voices = list_onnx_voices()
        except Exception:
            self._onnx_voices = []
        for v in self._onnx_voices:
            self.onnx_voice.addItem(v["label"], userData=v)
        self.onnx_voice.setToolTip(
            "Voices discovered in config/voices — a Piper-format .onnx model plus "
            "its sidecar .json. Drop a new pair in that folder and restart Settings."
        )
        onf.addRow("Piper voice", self.onnx_voice)
        self.onnx_execution_provider = QComboBox()
        self.onnx_execution_provider.addItem("CPU", userData="cpu")
        self.onnx_execution_provider.addItem("GPU", userData="gpu")
        self.onnx_execution_provider.addItem("Auto", userData="auto")
        _cur_ep = str(self._data.get("onnx_execution_provider", "cpu") or "cpu")
        _epi = self.onnx_execution_provider.findData(_cur_ep)
        self.onnx_execution_provider.setCurrentIndex(_epi if _epi >= 0 else 0)
        self.onnx_execution_provider.setToolTip(
            "ONNX Runtime execution provider for the Piper voice. CPU always works. "
            "GPU uses the installed accelerator; if it is unavailable the reason is "
            "logged and CPU is used instead."
        )
        onf.addRow("Execution", self.onnx_execution_provider)
        self.onnx_model_path = QLineEdit(str(self._data.get("onnx_voice_model", "") or ""))
        self.onnx_model_path.setPlaceholderText("config/voices/jarvis-high.onnx (or any absolute path)")
        self.onnx_model_path.setToolTip("Optional override — any local .onnx voice model path.")
        onf.addRow("Model path", self.onnx_model_path)
        self.onnx_speaker = QLineEdit(str(self._data.get("onnx_voice_speaker", "default")))
        self.onnx_speaker.setPlaceholderText("default")
        self.onnx_speaker.setToolTip("Speaker name/index for multi-speaker ONNX models. jarvis-high is single-voice: leave 'default'.")
        onf.addRow("Speaker", self.onnx_speaker)
        onnx_prev = QPushButton("TEST PIPER VOICE")
        onnx_prev.setToolTip(
            "Load the selected Piper voice with the selected execution provider and "
            "speak a short line — without saving or switching the active mode."
        )
        onnx_prev.clicked.connect(self._test_piper_voice)
        onf.addRow("", onnx_prev)
        self._panel_buttons("piperOnnx")
        self._provider_panels["piperOnnx"] = box
        # Preselect the currently configured ONNX voice, if any.
        try:
            _cur = (str(self._data.get("onnx_voice_model", "")).replace("\\", "/").split("/")[-1] or "")
            for _i in range(self.onnx_voice.count()):
                if self.onnx_voice.itemData(_i) and self.onnx_voice.itemData(_i)["model"].replace("\\", "/").endswith(_cur):
                    self.onnx_voice.setCurrentIndex(_i)
                    break
        except Exception:
            pass
        self.onnx_voice.currentIndexChanged.connect(self._on_onnx_voice_picked)
        self._on_onnx_voice_picked(self.onnx_voice.currentIndex())  # fill path field now

    def _build_edge_panel(self):
        box = HudGroupBox("EDGE TTS (free · Microsoft · online)")
        df = QFormLayout(box)
        self._provider_forms["edgeTts"] = df
        self.tts_voice = QLineEdit(str(self._data.get("tts_voice", "en-GB-RyanNeural")))
        self.tts_voice.setPlaceholderText("e.g. en-GB-RyanNeural")
        df.addRow("Edge voice", self.tts_voice)
        self._panel_buttons("edgeTts")
        self._provider_panels["edgeTts"] = box

    def _build_kokoro_panel(self):
        box = HudGroupBox("KOKORO (offline · neural)")
        kf = QFormLayout(box)
        self._provider_forms["kokoro"] = kf
        self.kokoro_voice = QComboBox()
        self.kokoro_voice.addItems([
            "af_heart", "af_bella", "af_nicole", "am_adam", "am_michael",
            "bf_emma", "bm_george", "jf_alpha", "zf_xiaobei", "ef_eva",
        ])
        try:
            from voice.providers_config import providers_block as _pb
            _kv = str(_pb(self._data).get("providers", {}).get("kokoro", {}).get("voice", "af_heart"))
        except Exception:
            _kv = "af_heart"
        _ki = self.kokoro_voice.findText(_kv)
        self.kokoro_voice.setCurrentIndex(_ki if _ki >= 0 else 0)
        self.kokoro_voice.setToolTip("Offline neural voice. The ~330 MB model downloads on first use.")
        kf.addRow("Kokoro voice", self.kokoro_voice)
        self._panel_buttons("kokoro")
        self._provider_panels["kokoro"] = box

    def _build_sapi_panel(self):
        box = HudGroupBox("WINDOWS SAPI (offline · built-in)")
        sf = QFormLayout(box)
        self._provider_forms["sapi"] = sf
        self.sapi_voice = QComboBox()
        try:
            import pyttsx3 as _pt3
            _eng = _pt3.init()
            for _v in _eng.getProperty("voices"):
                self.sapi_voice.addItem(str(getattr(_v, "name", _v.id)), userData=str(getattr(_v, "id", "")))
        except Exception:
            self.sapi_voice.addItem("(no SAPI voices found)", userData="")
        _cur_sapi = str(self._data.get("sapi_voice", "") or "")
        if _cur_sapi:
            _si = self.sapi_voice.findData(_cur_sapi)
            if _si >= 0:
                self.sapi_voice.setCurrentIndex(_si)
        self.sapi_voice.setToolTip("Voices installed in Windows. Instant start, zero network — the fastest engine.")
        sf.addRow("Installed voices", self.sapi_voice)
        sapi_prev = QPushButton("PREVIEW SAPI VOICE")
        sapi_prev.setToolTip("Hear the selected Windows voice immediately.")
        sapi_prev.clicked.connect(self._preview_sapi_voice)
        sf.addRow("", sapi_prev)
        self._panel_buttons("sapi")
        self._provider_panels["sapi"] = box

    def _on_provider_selected(self, index: int) -> None:
        """Show exactly the selected provider's panel (hide — not dim — the rest)."""
        try:
            self.provider_stack.setCurrentIndex(max(0, int(index)))
        except Exception:
            pass

    def _build_personality_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "PERSONALITY  //  IDENTITY · DELIVERY · VOICE TUNE")
        ident = HudGroupBox("IDENTITY")
        form = QFormLayout(ident)
        self.assistant_name = QLineEdit(str(self._data.get("assistant_name", "JARVIS") or "JARVIS"))
        self.assistant_name.setPlaceholderText("JARVIS")
        form.addRow("Assistant name", self.assistant_name)
        self.user_name = QLineEdit(str(self._data.get("user_name", "") or ""))
        self.user_name.setPlaceholderText("How should JARVIS address you? (empty = sir)")
        form.addRow("Your name", self.user_name)
        lay.addWidget(ident)

        delivery = HudGroupBox("DELIVERY")
        dv = QVBoxLayout(delivery)
        form2 = QFormLayout()
        self.voice_mode = QComboBox()
        self.voice_mode.addItems(["standard", "executive", "friendly", "focus", "emergency", "cinematic"])
        self.voice_mode.setCurrentText(str(self._data.get("voice_mode", "standard")))
        form2.addRow("Delivery mode", self.voice_mode)
        dv.addLayout(form2)
        self.voice_speed = self._slider_row(dv, "Speaking speed", int(float(self._data.get("voice_speed", 1.0)) * 100), 50, 200, "%")
        self.voice_pitch = self._slider_row(dv, "Voice pitch", int(float(self._data.get("voice_pitch", 0.0)) * 10), -20, 20, " st")
        self.voice_volume = self._slider_row(dv, "Voice volume", int(float(self._data.get("voice_volume", 1.0)) * 100), 0, 100, "%")
        lay.addWidget(delivery)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "PERSONALITY")

    def _flag_checkbox(self, store: dict, key: str, label: str, default: bool = True,
                       tip: str = "") -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(bool(store.get(key, default)))
        if tip:
            cb.setToolTip(tip)
        store[key] = cb
        return cb

    def _build_automation_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "AUTOMATION  //  VOICE HANDS-FREE · COMPUTER · TASKS")
        self._voice_checks = {}
        voice_features = self._data.get("features", {}) or {}
        hands = HudGroupBox("VOICE AUTOMATION")
        hv = QVBoxLayout(hands)
        for key, label in [
            ("wake_word", 'Wake word: "Hey Jarvis"'),
            ("hands_free", "Hands-free voice mode"),
            ("push_to_talk", "Push-to-talk mode (F6)"),
            ("voice_activity_detection", "Voice activity detection"),
            ("voice_interruption", "Voice interruption"),
            ("barge_in", "Barge-in while JARVIS is speaking"),
            ("background_listening", "Background listening"),
            ("always_on", "Always-on mode"),
            ("voice_command_recognition", "Voice command recognition"),
            ("proactive_speaking", "Speak without being spoken to (proactive voice)"),
        ]:
            hv.addWidget(self._flag_checkbox(self._voice_checks, key, label,
                                             key not in {"push_to_talk", "always_on", "proactive_speaking"}))
        # Extra wake words — matched IN ADDITION to the built-ins. Comma- or
        # line-separated, e.g.: jarvis, j.a.r.v.i.s, friday, alfred
        wake_row = QHBoxLayout()
        wake_lab = QLabel("Extra wake words")
        wake_lab.setMinimumWidth(185)
        self.wake_words = QLineEdit(
            ", ".join(str(w) for w in (self._data.get("wake_words") or []) )
        )
        self.wake_words.setPlaceholderText("jarvis, j.a.r.v.i.s, computer, friday …")
        self.wake_words.setToolTip(
            "Comma-separated extra wake words JARVIS answers to, matched in "
            "addition to the built-ins (hey jarvis / okay jarvis / computer)."
        )
        wake_row.addWidget(wake_lab)
        wake_row.addWidget(self.wake_words, 1)
        hv.addLayout(wake_row)
        lay.addWidget(hands)

        computer = HudGroupBox("COMPUTER CONTROL")
        cv = QVBoxLayout(computer)
        for key, label in [
            ("computer_control", "Enable computer control"),
            ("desktop_awareness", "Desktop awareness and system status"),
            ("screen_understanding", "Screen capture and visual understanding"),
            ("mouse_keyboard_control", "Mouse, keyboard, clicking, and typing"),
            ("window_control", "Open, switch, move, resize, and control windows"),
            ("terminal_control", "Execute desktop and terminal commands"),
            ("file_control", "Control files and folders"),
            ("desktop_automation", "Automate repetitive desktop tasks"),
        ]:
            cv.addWidget(self._flag_checkbox(self._voice_checks, key, label, True))
        lay.addWidget(computer)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "AUTOMATION")

    def _build_permissions_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "PERMISSIONS  //  STARTUP · DEVICES · ACCESS")
        self._perm_checks = {}
        perms = HudGroupBox("SYSTEM ACCESS")
        pv = QVBoxLayout(perms)
        self._autostart = QCheckBox("Auto-start J.A.R.V.I.S with Windows")
        self._autostart.setChecked(bool(self._data.get("features", {}).get("autostart", False)))
        self._autostart.setToolTip("Registers J.A.R.V.I.S to start when you sign in to Windows.")
        pv.addWidget(self._autostart)
        self._taskbar = QCheckBox("Hide J.A.R.V.I.S from the Windows taskbar")
        feats = self._data.get("features", {}) or {}
        self._taskbar.setChecked(bool(feats.get("hide_taskbar_button", feats.get("hide_taskbar", False))))
        self._taskbar.setEnabled(sys.platform.startswith("win"))
        if not sys.platform.startswith("win"):
            self._taskbar.setToolTip("Windows only. The Windows taskbar itself stays visible; only the J.A.R.V.I.S taskbar button is hidden.")
        pv.addWidget(self._taskbar)
        lay.addWidget(perms)

        devices = HudGroupBox("DEVICE ACCESS")
        dv = QVBoxLayout(devices)
        dv.addWidget(self._flag_checkbox(self._perm_checks, "mic_access", "Microphone access (voice input)",
                                         True, "Off = JARVIS stays muted and cannot unmute."))
        dv.addWidget(self._flag_checkbox(self._perm_checks, "camera_access", "Camera access (webcam + vision)",
                                         True, "Off = camera captures are refused."))
        note = QLabel("Revoking a device takes effect on SAVE. The microphone mute (F4) still works as a quick override.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        dv.addWidget(note)
        lay.addWidget(devices)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "PERMISSIONS")

    def _test_voice_link(self):
        self.voice_test_requested.emit(
            "Please say a brief, professional greeting in refined natural British English."
        )
        self._status.setText("VOICE TEST SENT")

    def _provider_values(self, pid: str) -> dict:
        """Collect ONLY the selected provider's field values from its panel."""
        d = self._data
        if pid == "fishAudio":
            return {
                "apiKey": self.fish_api_key.text().strip(),
                "endpoint": self.fish_endpoint.text().strip() or DEFAULTS["fish_audio_endpoint"],
                "model": self.fish_model_id.text().strip() or DEFAULTS["fish_audio_model_id"],
                "voice": self.fish_voice_id.text().strip(),
                "format": self.fish_format.currentText(),
                "latency": self.fish_latency.currentText(),
            }
        if pid == "elevenLabs":
            return {
                "apiKey": self.elevenlabs_api_key.text().strip(),
                "model": self.elevenlabs_model_id.currentText(),
                "voice": self.elevenlabs_voice_id.text().strip() or DEFAULTS["elevenlabs_voice_id"],
            }
        if pid == "gemini":
            return {"apiKey": str(d.get("gemini_api_key", "") or ""),
                    "model": str(d.get("ai_model", "") or ""),
                    "voice": self.live_voice.currentText()}
        if pid == "piperOnnx":
            data = self.onnx_voice.currentData() or {}
            return {
                "modelPath": self.onnx_model_path.text().strip()
                             or str((data or {}).get("model") or d.get("onnx_voice_model", "")),
                "configPath": str((data or {}).get("config") or d.get("onnx_voice_config", "")),
                "speaker": self.onnx_speaker.text().strip() or "default",
                "executionProvider": str(self.onnx_execution_provider.currentData() or "cpu"),
            }
        if pid == "kokoro":
            return {"voice": self.kokoro_voice.currentText(),
                    "speed": float(d.get("voice_speed", 1.0) or 1.0)}
        if pid == "edgeTts":
            return {"voice": self.tts_voice.text().strip() or DEFAULTS["tts_voice"],
                    "rate": "+0%", "pitch": "+0Hz", "volume": float(d.get("voice_volume", 1.0) or 1.0)}
        if pid == "sapi":
            return {"voice": str(self.sapi_voice.currentData() or "")}
        return {}

    def _selected_provider(self) -> str:
        try:
            return str(self.provider_selector.currentData() or "edgeTts")
        except Exception:
            return "edgeTts"

    def _provider_message(self, pid: str, text: str) -> None:
        try:
            self._provider_status[pid].setText(text)
        except Exception:
            pass
        self._status.setText(text[:160])

    def _save_pipeline(self, as_default: bool = False) -> None:
        """Persist ONLY the pipeline choice (pipeline-scoped save).

        The selected pipeline combo value becomes ``voice_output_mode``, the
        Gemini renderer subtree is synced so the runtime actually follows the
        combo, and the one-time pipeline lift markers are recorded so the
        deliberate choice is never re-lifted. ``as_default`` additionally
        stores ``voice.defaultPipeline`` (read by ``ui_settings.load``), so
        fresh sessions start on this pipeline.
        """
        try:
            from voice.providers_config import (
                ProviderConfigError, providers_block, set_default_pipeline,
            )
        except Exception as exc:
            self._pipeline_status.setText(f"PROVIDER LAYER UNAVAILABLE: {exc}")
            return
        pipeline = str(self.voice_output_mode.currentData() or "")
        if pipeline not in ("gemini", "gemini_piper", "local"):
            self._pipeline_status.setText("NOT SAVED — select a pipeline first.")
            return
        try:
            cfg = dict(self._data or {})
            cfg["voice_output_mode"] = pipeline
            cfg["voice_pipeline_migrated"] = True
            cfg["voice_piper_to_native_lifted"] = True
            # Sync the Gemini renderer subtree so the runtime (which reads the
            # provider block) actually follows this combo.
            block = providers_block(cfg)
            block["geminiRenderer"] = "native" if pipeline == "gemini" else "piper"
            cfg["voice"] = block
            if as_default:
                cfg = set_default_pipeline(cfg, pipeline)
            # One guarded write of the merged config (flat keys + voice block).
            updates = {"voice_output_mode": pipeline,
                       "voice_pipeline_migrated": True,
                       "voice_piper_to_native_lifted": True,
                       "voice": cfg.get("voice", {})}
            try:
                from core import config_guard as _guard
                merged = dict(_guard.read(self._config_path))
            except Exception:
                from ui_settings import load as _load_cfg  # self-import: same module
                merged = dict(_load_cfg(self._config_path))
            merged.update(updates)
            try:
                from core import config_guard as _guard
                _ok, _msg = _guard.guard_write(merged, tag="voice-pipeline", path=self._config_path)
                if not _ok:
                    raise RuntimeError(_msg)
                self._data = _guard.read(self._config_path)
            except Exception:
                from ui_settings import save as _save_cfg
                self._data = _save_cfg(self._config_path,
                                       **{k: v for k, v in merged.items() if k != "features"})
            label = {"gemini": "Gemini Native Audio", "gemini_piper": "Gemini → Piper ONNX",
                     "local": "Local Piper Only"}.get(pipeline, pipeline)
            msg = f"DEFAULT SAVED · {label}" if as_default else f"SAVED · {label}"
            self._pipeline_status.setText(msg)
            self._status.setText(msg)
            self.settings_saved.emit(dict(self._data))
            parent = self.parent()
            method = getattr(parent, "_apply_full_settings", None)
            if callable(method):
                method(dict(self._data))
        except ProviderConfigError as exc:
            self._pipeline_status.setText(f"NOT SAVED — {exc}")
        except Exception as exc:
            self._pipeline_status.setText(f"SAVE FAILED — {str(exc)[:140]}")
            self.write_log_safe(f"Pipeline save failed: {exc}")

    def _save_pipeline_as_default(self) -> None:
        """SAVE AS DEFAULT for the pipeline: persist it and record it as the
        default every new session starts on."""
        self._save_pipeline(as_default=True)

    def _save_provider_panel(self, pid: str, as_default: bool = False) -> None:
        """Validate + persist ONLY this provider's subtree (spec: provider-scoped save).

        Save: providers.<pid> + activeProvider change; every other provider's
        subtree is carried through untouched.  Save as Default: additionally
        stores defaultProvider + defaultVoice as one validated atomic pair.
        """
        try:
            from voice.providers_config import (
                ProviderConfigError, save_provider, set_active_provider,
                set_default_pair, validate_provider,
            )
        except Exception as exc:
            self._provider_message(pid, f"PROVIDER LAYER UNAVAILABLE: {exc}")
            return
        values = self._provider_values(pid)
        if pid == "gemini":
            # The Gemini key lives on the AI page; presence in the loaded
            # config counts (the panel itself has no key field).
            values["_configHasGeminiKey"] = bool(str((self._data or {}).get("gemini_api_key", "") or "").strip())
        err = validate_provider(pid, values)
        if err:
            self._provider_message(pid, f"NOT SAVED — {err}")
            return
        try:
            cfg = dict(self._data or {})
            cfg = save_provider(cfg, pid, values)
            cfg = set_active_provider(cfg, pid)
            if as_default:
                # The pair's voice is the provider's identity: the selected
                # voice for every provider except Piper, whose arrangement is
                # identified by its model file (jarvis-high.onnx IS the voice).
                try:
                    from voice.providers_config import provider_voices as _pv_ids
                    _voices = _pv_ids(cfg, pid)
                except Exception:
                    _voices = ()
                _pair_voice = str(values.get("voice", "") or (_voices[0] if _voices else ""))
                cfg = set_default_pair(cfg, pid, _pair_voice)
            # Merge the nested voice block over the flat keys that the rest of
            # the settings flow persists, then write once through the guard.
            updates = {"voice": cfg.get("voice", {})}
            self._persist_provider_write(pid, values, updates)
            try:
                from core.tts import clear_tts_cache
                clear_tts_cache()
            except Exception:
                pass
            self._data = cfg
            label = self._PROVIDER_LABELS.get(pid, pid)
            if as_default:
                voice = str(values.get("voice", ""))
                self._provider_message(pid, f"DEFAULT SAVED · {label} · {voice}")
            else:
                self._provider_message(pid, f"SAVED · {label}")
            self.settings_saved.emit(dict(self._data))
            parent = self.parent()
            if parent is not None and hasattr(parent, "_apply_full_settings"):
                parent._apply_full_settings(dict(self._data))
        except ProviderConfigError as exc:
            self._provider_message(pid, f"NOT SAVED — {exc}")
        except Exception as exc:
            self._provider_message(pid, f"SAVE FAILED — {str(exc)[:140]}")
            self.write_log_safe(f"Provider save failed ({pid}): {exc}")

    def _persist_provider_write(self, pid: str, values: dict, updates: dict) -> None:
        """One guarded write of the nested voice block + the legacy flat keys
        this provider maps onto (dual-read mirror), leaving other providers'
        flat keys exactly as they are in the file."""
        try:
            from core import config_guard as _guard
            merged = dict(_guard.read(self._config_path))
        except Exception:
            from ui_settings import load as _load_cfg  # self-import: same module
            merged = dict(_load_cfg(self._config_path))
        merged.update(updates)
        # Mirror this provider's values onto the legacy flat keys only.
        if pid == "fishAudio":
            merged.update({"fish_audio_api_key": values.get("apiKey", ""),
                           "fish_audio_endpoint": values.get("endpoint", ""),
                           "fish_audio_model_id": values.get("model", ""),
                           "fish_audio_voice_id": values.get("voice", ""),
                           "fish_audio_format": values.get("format", "mp3"),
                           "fish_audio_latency": values.get("latency", "normal")})
        elif pid == "elevenLabs":
            merged.update({"elevenlabs_api_key": values.get("apiKey", ""),
                           "elevenlabs_model_id": values.get("model", ""),
                           "elevenlabs_voice_id": values.get("voice", "")})
        elif pid == "gemini":
            merged.update({"voice_name": values.get("voice", "")})
        elif pid == "piperOnnx":
            merged.update({"onnx_voice_model": values.get("modelPath", ""),
                           "onnx_voice_config": values.get("configPath", ""),
                           "onnx_voice_speaker": values.get("speaker", "default"),
                           "onnx_execution_provider": values.get("executionProvider", "cpu")})
        elif pid == "edgeTts":
            merged.update({"tts_voice": values.get("voice", "")})
        elif pid == "kokoro":
            merged.update({"tts_engine": "kokoro", "tts_voice": values.get("voice", ""),
                           "kokoro_voice": values.get("voice", "")})
        elif pid == "sapi":
            merged.update({"sapi_voice": values.get("voice", "")})
        # The stored voice_output_mode follows the PIPELINE combo (the user's
        # renderer choice), never the raw on-disk value — the combo already
        # shows the runtime-resolved mode (e.g. the one-time gemini lift).
        # gemini_piper stays gemini_piper even when the Piper provider panel is
        # open — that panel IS its renderer.
        try:
            _vod = str(self.voice_output_mode.currentData() or "")
            if _vod:
                merged["voice_output_mode"] = _vod
            else:
                merged["voice_output_mode"] = str(merged.get("voice_output_mode") or "gemini")
        except Exception:
            pass
        # A deliberate save records that the one-time gemini -> gemini_piper
        # lift has happened, so an explicit Gemini Native Audio choice made
        # from here on is never silently migrated again. It also records the
        # piper-pipeline → native-audio lift, so a deliberate piper choice is
        # never re-flipped to the new default.
        merged["voice_pipeline_migrated"] = True
        merged["voice_piper_to_native_lifted"] = True
        if str(merged.get("tts_engine", "")) not in ("kokoro",):
            merged["tts_engine"] = {"fishAudio": "fish_audio", "elevenLabs": "elevenlabs",
                                    "gemini": "edgetts", "piperOnnx": "onnx",
                                    "edgeTts": "edgetts", "sapi": "sapi"}.get(
                pid, str(merged.get("tts_engine", "edgetts")))
        try:
            from core import config_guard as _guard
            # guard_write (not save_with_history) so the test/self config path
            # is honoured — save_with_history always writes the global path.
            _ok, _msg = _guard.guard_write(merged, tag="voice-provider", path=self._config_path)
            if not _ok:
                raise RuntimeError(_msg)
            self._data = _guard.read(self._config_path)
        except Exception:
            from ui_settings import save as _save_cfg
            self._data = _save_cfg(self._config_path, **{k: v for k, v in merged.items() if k != "features"})

    def _test_provider(self, pid: str) -> None:
        """Speak a test line through the panel's provider with the panel's
        current (unsaved) values; provider-named errors land in the status bar."""
        import threading
        values = self._provider_values(pid)
        try:
            from voice.providers_config import ProviderConfigError, validate_provider
            err = validate_provider(pid, values)
            if err:
                self._provider_message(pid, f"TEST — {err}")
                return
        except Exception:
            pass
        label = self._PROVIDER_LABELS.get(pid, pid)
        self._provider_message(pid, f"TESTING {label.upper()}…")

        # Build a config dict shaped like the persisted one so the factory's
        # provider branch picks up the panel values (not the saved ones).
        cfg = dict(self._data or {})
        try:
            from voice.providers_config import save_provider, set_active_provider
            cfg = set_active_provider(save_provider(cfg, pid, values), pid)
        except Exception:
            pass

        def _say(text):
            QTimer.singleShot(0, self, lambda: self._provider_message(pid, text))

        def _run():
            try:
                import sys as _sys
                _sys.path.insert(0, str(self._config_path.parent.parent))
                from core.tts import clear_tts_cache, create_tts_player
                clear_tts_cache()   # panel values differ from any cached engine
                create_tts_player(cfg).speak("Voice test.")
                _say(f"{label.upper()} TEST OK")
            except Exception as exc:
                _say(f"{label.upper()} TEST FAILED: {str(exc)[:120]}")
                self.write_log_safe(f"Provider test failed ({pid}): {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_provider_voices(self, pid: str) -> None:
        """Fetch the provider's voice list into ITS panel only (10 s timeout,
        worker thread; failure never discards the saved selection)."""
        import threading
        label = self._PROVIDER_LABELS.get(pid, pid)
        values = self._provider_values(pid)
        key = str(values.get("apiKey", "") or "")
        if not key:
            self._provider_message(pid, f"REFRESH — {label} API key is missing.")
            return
        self._provider_message(pid, f"REFRESHING {label.upper()}…")

        def _run():
            try:
                import requests
                if pid == "fishAudio":
                    resp = requests.get(
                        "https://api.fish.audio/model",
                        headers={"Authorization": f"Bearer {key}"},
                        params={"page_size": 30, "self": True}, timeout=10)
                    resp.raise_for_status()
                    items = resp.json().get("items", []) or []
                    voices = [(str(i.get("title") or i.get("_id", "")), str(i.get("_id", "")))
                              for i in items if i.get("_id")]
                else:  # elevenLabs
                    resp = requests.get(
                        "https://api.elevenlabs.io/v1/voices",
                        headers={"xi-api-key": key}, timeout=10)
                    resp.raise_for_status()
                    voices = [(str(v.get("name", "")), str(v.get("voice_id", "")))
                              for v in resp.json().get("voices", []) if v.get("voice_id")]
            except Exception as exc:
                QTimer.singleShot(0, self, lambda: self._provider_message(
                    pid, f"REFRESH FAILED — {str(exc)[:120]} (saved voice kept)"))
                return

            def _apply():
                try:
                    combo = self.fish_voice_id if pid == "fishAudio" else self.elevenlabs_voice_id
                    if pid != "fishAudio" and hasattr(self, "_eleven_voice_combo"):
                        combo = self._eleven_voice_combo
                    current = combo.text().strip() if hasattr(combo, "text") else ""
                    # Keep it a plain QLineEdit: refresh offers the fetched ids
                    # as an editable completion list, the current value survives.
                    if hasattr(combo, "completer") and combo.completer() is None and voices:
                        from PyQt6.QtCore import QStringListModel
                        from PyQt6.QtWidgets import QCompleter
                        c = QCompleter([f"{n} ({i})" for n, i in voices], combo)
                        c.setFilterMode(Qt.MatchFlag.Contains)
                        combo.setCompleter(c)
                    if current:
                        combo.setText(current)   # the saved selection is preserved
                    self._provider_message(pid, f"REFRESHED · {len(voices)} voices available")
                except Exception as exc:
                    self._provider_message(pid, f"REFRESH FAILED — {str(exc)[:120]}")

            QTimer.singleShot(0, self, _apply)

        threading.Thread(target=_run, daemon=True).start()

    def _save_default_voice(self) -> None:
        """Legacy entry point: Save as Default for the currently selected provider."""
        self._save_provider_panel(self._selected_provider(), as_default=True)

    def _test_tts_engine(self):
        """Legacy entry point: test the currently selected provider."""
        self._test_provider(self._selected_provider())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        root = self.findChild(QFrame, "SettingsRoot")
        if root is not None and hasattr(root, "_fit_backdrop"):
            root._fit_backdrop()

    def _build_ai_tab(self):
        """AI agent assistant: provider + model dropdowns, custom provider, API keys.f"""
        from core.ai_providers import PROVIDERS, models_for
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        intro = QLabel("AI AGENT  //  PROVIDER · MODEL · CUSTOM ENDPOINT")
        intro.setStyleSheet(f'color:{ACCENT};font:800 9pt "Orbitron";padding:2px 2px 5px 2px;')
        lay.addWidget(intro)
        hint = QLabel("Gemini drives the voice session. Other providers answer typed text. Custom = any OpenAI-compatible endpoint.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"background:{SURFACE_INSET};border:none;border-radius:9px;padding:10px;color:{TEXT_MID};")
        lay.addWidget(hint)

        prov_box = HudGroupBox("PROVIDER & MODEL")
        pf = QFormLayout(prov_box)
        self.ai_provider = QComboBox()
        for key, meta in PROVIDERS.items():
            self.ai_provider.addItem(str(meta.get("label", key)), key)
        cur_prov = str(self._data.get("ai_provider", "gemini") or "gemini").strip().lower()
        idx = self.ai_provider.findData(cur_prov)
        self.ai_provider.setCurrentIndex(max(0, idx))
        self.ai_provider.currentIndexChanged.connect(self._refresh_ai_fields)
        pf.addRow("Provider", self.ai_provider)
        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        saved_model = str(self._data.get("ai_model", "") or "").strip()
        self.ai_model.addItems(models_for(cur_prov))
        if saved_model:
            self.ai_model.setCurrentText(saved_model)
        self.ai_model.setToolTip("Pick a model or type your own (e.g. a newly released ID).")
        pf.addRow("Model", self.ai_model)
        lay.addWidget(prov_box)

        # ── MODEL ROLES (core/model_router.py) ────────────────────────────
        # Each role answers a different kind of work; blank = built-in default.
        try:
            from core.model_router import ROLES as _ROLES, ROLE_HINTS as _HINTS
        except Exception:
            _ROLES, _HINTS = (), {}
        self._role_edits: dict[str, QLineEdit] = {}
        if _ROLES:
            roles_box = HudGroupBox("MODEL ROLES")
            rf = QFormLayout(roles_box)
            saved_roles = dict(self._data.get("model_roles") or {})
            for _role in _ROLES:
                _edit = QLineEdit(str(saved_roles.get(_role, "") or ""))
                _edit.setPlaceholderText("(auto)")
                _edit.setToolTip(str(_HINTS.get(_role, "")))
                self._role_edits[_role] = _edit
                rf.addRow(_role.capitalize(), _edit)
            _roles_hint = QLabel(
                "Blank = built-in default. A role model that fails or "
                "rate-limits is benched and the fallback role takes over."
            )
            _roles_hint.setWordWrap(True)
            _roles_hint.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
            rf.addRow("", _roles_hint)
            lay.addWidget(roles_box)

        self._ai_key_box = HudGroupBox("PROVIDER API KEY")
        kf = QFormLayout(self._ai_key_box)
        self.openai_api_key = QLineEdit(str(self._data.get("openai_api_key", "")))
        self.openai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_api_key.setPlaceholderText("sk-… OpenAI key")
        kf.addRow("OpenAI key", self.openai_api_key)
        self.anthropic_api_key = QLineEdit(str(self._data.get("anthropic_api_key", "")))
        self.anthropic_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_api_key.setPlaceholderText("sk-ant-… Anthropic key")
        kf.addRow("Anthropic key", self.anthropic_api_key)
        self.groq_api_key = QLineEdit(str(self._data.get("groq_api_key", "")))
        self.groq_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_api_key.setPlaceholderText("gsk_… Groq key")
        kf.addRow("Groq key", self.groq_api_key)
        info = QLabel("Ollama needs no key — just run Ollama locally. Gemini uses the key from first-time setup.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        kf.addRow("", info)
        lay.addWidget(self._ai_key_box)

        self._custom_box = HudGroupBox("CUSTOM PROVIDER (OpenAI-compatible)")
        cf = QFormLayout(self._custom_box)
        self.custom_provider_name = QLineEdit(str(self._data.get("custom_provider_name", "")))
        self.custom_provider_name.setPlaceholderText("e.g. My Cloud GPU")
        cf.addRow("Name", self.custom_provider_name)
        self.custom_ai_base_url = QLineEdit(str(self._data.get("custom_ai_base_url", "")))
        self.custom_ai_base_url.setPlaceholderText("https://my-server:8000/v1")
        cf.addRow("Base URL", self.custom_ai_base_url)
        self.custom_ai_api_key = QLineEdit(str(self._data.get("custom_ai_api_key", "")))
        self.custom_ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.custom_ai_api_key.setPlaceholderText("Custom API key (if required)")
        cf.addRow("API key", self.custom_ai_api_key)
        self.custom_ai_model = QLineEdit(str(self._data.get("custom_ai_model", "")))
        self.custom_ai_model.setPlaceholderText("e.g. my-model-7b")
        cf.addRow("Model", self.custom_ai_model)
        lay.addWidget(self._custom_box)

        test = QPushButton("TEST CONNECTION")
        test.setToolTip("Send a tiny probe to the selected provider.")
        test.clicked.connect(self._test_ai_connection)
        lay.addWidget(test)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "AI")
        self._refresh_ai_fields(self.ai_provider.currentIndex())

    def _refresh_ai_fields(self, _index=None):
        """Refresh model list + dim the custom group unless provider = custom.f"""
        try:
            from core.ai_providers import models_for
            from PyQt6.QtWidgets import QGraphicsOpacityEffect as _OE
            prov = str(self.ai_provider.currentData() or "gemini")
            keep = self.ai_model.currentText().strip()
            self.ai_model.blockSignals(True)
            self.ai_model.clear()
            self.ai_model.addItems(models_for(prov))
            if keep:
                self.ai_model.setCurrentText(keep)
            self.ai_model.blockSignals(False)
            box = getattr(self, "_custom_box", None)
            if box is not None:
                eff = box.graphicsEffect()
                if not isinstance(eff, _OE):
                    eff = _OE(box)
                    box.setGraphicsEffect(eff)
                eff.setOpacity(1.0 if prov == "custom" else 0.45)
        except Exception:
            pass

    def _test_ai_connection(self):
        try:
            from core import ai_providers as _aip
            cfg = self._collect()
            reply = _aip.test_connection(cfg)
            self._status.setText("AI LINK OK")
            self.write_log_safe(f"AI test OK: {str(reply)[:120]}")
        except Exception as exc:
            self._status.setText("AI LINK FAILED")
            self.write_log_safe(f"AI test failed: {exc}")

    def write_log_safe(self, text: str):
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "write_log"):
                parent.write_log(text)
        except Exception:
            pass
        try:
            self._status.setToolTip(str(text)[:300])
        except Exception:
            pass

    def _build_plugins_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "PLUGINS  //  EXTEND JARVIS")
        self._plugin_list = QListWidget()
        self._plugin_list.setStyleSheet(
            f"QListWidget{{background:rgba(0,12,20,130);color:{TEXT_BRIGHT};border:1px solid {STROKE_MID};border-radius:10px;padding:5px;}}"
            "QListWidget::item{padding:7px 8px;border-bottom:1px solid rgba(41,135,170,45);}"
            "QListWidget::item:selected{background:rgba(0,103,143,105);}"
        )
        lay.addWidget(self._plugin_list, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self._reload_plugins)
        row.addWidget(refresh)
        toggle = QPushButton("ENABLE / DISABLE")
        toggle.setToolTip("Toggle the selected plugin. Takes effect on next voice request.")
        toggle.clicked.connect(self._toggle_plugin)
        row.addWidget(toggle)
        folder = QPushButton("OPEN FOLDER")
        folder.clicked.connect(self._open_plugins_folder)
        row.addWidget(folder)
        lay.addLayout(row)
        self._plugin_status = QLabel("")
        self._plugin_status.setWordWrap(True)
        self._plugin_status.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        lay.addWidget(self._plugin_status)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "PLUGINS")
        self._reload_plugins()

    def _iter_plugins(self):
        try:
            fn = getattr(self, "get_plugins", None)
            if callable(fn):
                return list(fn() or [])
        except Exception:
            pass
        return []

    def _reload_plugins(self):
        try:
            self._plugin_list.clear()
            plugins = self._iter_plugins()
            if not plugins:
                self._plugin_list.addItem("No plugins found. Drop a plugin folder into plugins/ and press REFRESH.")
            for p in plugins:
                try:
                    state = "ON" if p.get("enabled", True) else "OFF"
                    problem = "" if p.get("valid", True) else f"  ·  BROKEN: {p.get('error', '?')}"
                    item = QListWidgetItem(f"[{state}]  {p.get('name', '?')}  —  {p.get('description', '')[:90]}{problem}")
                    item.setData(Qt.ItemDataRole.UserRole, p.get("name", ""))
                    self._plugin_list.addItem(item)
                except Exception:
                    pass
            self._plugin_status.setText(f"{len(plugins)} plugin(s) discovered.")
        except Exception as exc:
            self._plugin_status.setText(f"Plugin scan failed: {exc}")

    def _toggle_plugin(self):
        item = self._plugin_list.currentItem()
        if item is None:
            self._plugin_status.setText("Select a plugin first.")
            return
        name = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not name:
            return
        try:
            cur = {p.get("name"): bool(p.get("enabled", True)) for p in self._iter_plugins()}
            new_state = not cur.get(name, True)
            fn = getattr(self, "set_plugin_enabled", None)
            if callable(fn):
                fn(name, new_state)
                self._plugin_status.setText(f"Plugin '{name}' {'enabled' if new_state else 'disabled'}. Applies to the next request.")
                self._reload_plugins()
            else:
                self._plugin_status.setText("Plugin control is unavailable while JARVIS is offline.")
        except Exception as exc:
            self._plugin_status.setText(f"Toggle failed: {exc}")

    def _open_plugins_folder(self):
        try:
            fn = getattr(self, "open_plugins_folder", None)
            if callable(fn):
                fn()
                return
            import subprocess
            path = str(Path(__file__).resolve().parent / "plugins")
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 - local UI action
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self._plugin_status.setText(f"Could not open folder: {exc}")

    def _build_diagnostics_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "DIAGNOSTICS  //  HEALTH · CHECKS · LOG")
        self._diag_box = HudGroupBox("SYSTEM CHECKS")
        dv = QVBoxLayout(self._diag_box)
        self._diag_list = QListWidget()
        self._diag_list.setStyleSheet(
            f"QListWidget{{background:rgba(0,12,20,130);color:{TEXT_BRIGHT};border:1px solid {STROKE_MID};border-radius:10px;padding:5px;}}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid rgba(41,135,170,45);}"
        )
        dv.addWidget(self._diag_list)
        run = QPushButton("RUN CHECKS")
        run.setToolTip("Verify engine, keys, files and devices without speaking.")
        run.clicked.connect(self._run_diagnostics)
        dv.addWidget(run)
        lay.addWidget(self._diag_box)
        log_box = HudGroupBox("RECENT ACTIVITY")
        lv = QVBoxLayout(log_box)
        self._diag_log = QTextEdit()
        self._diag_log.setReadOnly(True)
        self._diag_log.setMinimumHeight(150)
        self._diag_log.setStyleSheet(
            f"QTextEdit{{background:rgba(0,12,20,130);color:{ACCENT_BRIGHT};border:1px solid {STROKE_MID};border-radius:10px;padding:8px;font:8pt 'Share Tech Mono';}}"
        )
        lv.addWidget(self._diag_log)
        copy_log = QPushButton("REFRESH LOG")
        copy_log.clicked.connect(self._refresh_diag_log)
        lv.addWidget(copy_log)
        lay.addWidget(log_box)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "DIAG")
        self._refresh_diag_log()

    def _run_diagnostics(self):
        import platform as _plat
        results = []
        def _ok(name, good, detail=""):
            results.append((name, bool(good), str(detail or "")))
        _ok("Python", True, _plat.python_version())
        try:
            import PyQt6.QtCore as _qc
            _ok("PyQt6", True, _qc.QT_VERSION_STR)
        except Exception as exc:
            _ok("PyQt6", False, exc)
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa
            _ok("WebEngine", True, "embedded browser ready")
        except Exception:
            _ok("WebEngine", False, "pip install PyQt6-WebEngine")
        try:
            from core.tts import create_tts_player, voice_status
            _player = create_tts_player(self._collect())
            _player._ensure_engine()   # lazy player: force the real engine build here
            _st = voice_status()
            _ok("TTS engine", _st.state != "ERROR",
                f"{self._selected_provider()}"
                + (f" · {_st.error[:60]}" if _st.error else ""))
        except Exception as exc:
            _ok("TTS engine", False, str(exc)[:100])
        cfg = self._collect()
        try:
            _file_cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            _file_cfg = {}
        _ok("Gemini key", bool(str(_file_cfg.get("gemini_api_key", "") or "").strip()), "voice session")
        _ok("ElevenLabs key", str(cfg.get("elevenlabs_api_key", "") or "").startswith("sk_"), "cloud voice")
        _ok("Fish key", bool(str(cfg.get("fish_audio_api_key", "") or "").strip()), "cloud voice")
        try:
            mem = Path(__file__).resolve().parent / "memory" / "long_term.json"
            _ok("Memory file", mem.is_file(), f"{mem.stat().st_size} bytes" if mem.is_file() else "missing")
        except Exception as exc:
            _ok("Memory file", False, exc)
        try:
            import shutil as _sh
            free_gb = _sh.disk_usage(str(Path.home())).free / (1024 ** 3)
            _ok("Disk free", free_gb > 1.0, f"{free_gb:.1f} GB")
        except Exception as exc:
            _ok("Disk free", False, exc)
        try:
            self._diag_list.clear()
            for name, good, detail in results:
                mark = "OK " if good else "FAIL"
                self._diag_list.addItem(f"[{mark}]  {name}" + (f"  —  {detail}" if detail else ""))
            self._status.setText("CHECKS DONE")
        except Exception as exc:
            self._status.setText(f"CHECKS ERROR: {exc}")

    def _refresh_diag_log(self):
        lines = []
        try:
            fn = getattr(self, "get_activity_log", None)
            if callable(fn):
                lines = list(fn(60) or [])
        except Exception:
            pass
        try:
            self._diag_log.setPlainText("\n".join(lines) if lines else "No activity yet — open the app and talk to JARVIS first.")
        except Exception:
            pass

    def _build_layout_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(9)

        intro = QLabel("ADVANCED LAYOUT EDITOR — move, resize, recolor, hide, and create custom UI elements without expanding the main Arc Reactor.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"background:{SURFACE_INSET};border:none;border-radius:9px;padding:10px;color:{TEXT_MID};")
        lay.addWidget(intro)

        top = QHBoxLayout()
        top.addWidget(QLabel("Element"))
        self.layout_element = QComboBox()
        top.addWidget(self.layout_element, 1)
        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self._load_layout_values)
        top.addWidget(refresh)
        open_editor = QPushButton("OPEN FULL EDITOR")
        open_editor.clicked.connect(self._open_external_layout_editor)
        top.addWidget(open_editor)
        lay.addLayout(top)

        props = HudGroupBox("SELECTED ELEMENT")
        pf = QFormLayout(props)
        self.layout_x = QSpinBox(); self.layout_x.setRange(-5000, 5000); pf.addRow("X", self.layout_x)
        self.layout_y = QSpinBox(); self.layout_y.setRange(-5000, 5000); pf.addRow("Y", self.layout_y)
        self.layout_w = QSpinBox(); self.layout_w.setRange(20, 5000); pf.addRow("Width", self.layout_w)
        self.layout_h = QSpinBox(); self.layout_h.setRange(20, 5000); pf.addRow("Height", self.layout_h)
        self.layout_opacity = QSlider(Qt.Orientation.Horizontal); self.layout_opacity.setRange(0, 100); pf.addRow("Opacity", self.layout_opacity)
        self.layout_visible = QCheckBox("Visible"); pf.addRow("", self.layout_visible)
        apply_btn = QPushButton("APPLY TO LAYOUT")
        apply_btn.clicked.connect(self._apply_layout_selection)
        pf.addRow("", apply_btn)
        lay.addWidget(props)

        add = HudGroupBox("ADD ELEMENT")
        af = QFormLayout(add)
        self.new_kind = QComboBox(); self.new_kind.addItems(["Text", "Button", "Panel", "Separator", "Window", "Monitor", "WebView"]); af.addRow("Type", self.new_kind)
        self.new_name = QLineEdit(); self.new_name.setPlaceholderText("e.g. System Status"); af.addRow("Name", self.new_name)
        self.new_text = QLineEdit(); self.new_text.setPlaceholderText("Display text"); af.addRow("Text", self.new_text)
        add_btn = QPushButton("ADD ELEMENT")
        add_btn.clicked.connect(self._add_layout_element)
        af.addRow("", add_btn)
        lay.addWidget(add)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "LAYOUT")
        self.layout_element.currentTextChanged.connect(self._on_layout_selection_changed)

    def _build_web_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        intro = QLabel("WEB SURFACE  //  WEBVIEW + TASK QUEUE")
        intro.setStyleSheet(f'color:{ACCENT};font:800 9pt "Orbitron";padding:2px 2px 5px 2px;')
        lay.addWidget(intro)
        box = HudGroupBox("WEBVIEW")
        form = QFormLayout(box)
        self.web_homepage = QLineEdit(str(self._data.get("web_homepage", DEFAULTS["web_homepage"])))
        self.web_homepage.setPlaceholderText("https://")
        form.addRow("Homepage", self.web_homepage)
        self._web_ontop = QCheckBox("Keep HUD windows always on top")
        self._web_ontop.setChecked(bool(self._data.get("panel_always_on_top", True)))
        form.addRow("", self._web_ontop)
        self._web_engine = QCheckBox("Use the internal WebView engine for search and video")
        self._web_engine.setChecked(bool(self._data.get("features", {}).get("webview_engine", True)))
        form.addRow("", self._web_engine)
        self._web_music = QCheckBox("Play music inside the internal WebView")
        self._web_music.setChecked(bool(self._data.get("features", {}).get("webview_music", True)))
        form.addRow("", self._web_music)
        lay.addWidget(box)
        tasks = HudGroupBox("WEB TASK DEFAULTS")
        tv = QVBoxLayout(tasks)
        _wfeat = self._data.get("features", {}) or {}
        self._web_js = QCheckBox("Allow JavaScript in Webview")
        self._web_js.setChecked(bool(_wfeat.get("webview_js", True)))
        self._web_js.setToolTip("Off = the embedded browser blocks page scripts.")
        self._web_external = QCheckBox("Open unknown schemes in the system browser")
        self._web_external.setChecked(bool(_wfeat.get("webview_external", True)))
        self._web_external.setToolTip(
            "Off = links the embedded browser cannot load are reported instead "
            "of being handed to your default browser."
        )
        tv.addWidget(self._web_js); tv.addWidget(self._web_external)
        hint = QLabel("Web Task queues search, news, research, price, and browse jobs. Browse opens a dedicated Webview window.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        tv.addWidget(hint)
        lay.addWidget(tasks)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "WEB")

    def _build_memory_settings_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        intro = QLabel("MEMORY CORE  //  LOCAL LONG-TERM STORE")
        intro.setStyleSheet(f'color:{ACCENT};font:800 9pt "Orbitron";padding:2px 2px 5px 2px;')
        lay.addWidget(intro)
        box = HudGroupBox("STORE")
        v = QVBoxLayout(box)
        self._memory_autosave = QCheckBox("Auto-save remembered facts to memory/long_term.json")
        self._memory_autosave.setChecked(bool(self._data.get("memory_autosave", True)))
        v.addWidget(self._memory_autosave)
        self._memory_index = QCheckBox("Keep a prompt index so JARVIS can recall facts that are not in the core prompt")
        self._memory_index.setChecked(bool(self._data.get("features", {}).get("memory_index", True)))
        self._memory_index.setToolTip(
            "When on, the [ALSO REMEMBERED] index is added to the prompt so "
            "JARVIS can search local memory for facts that do not fit inline."
        )
        v.addWidget(self._memory_index)
        info = QLabel("The Memory window lists every stored fact, lets you add keys, search, and forget entries. It is a real HUD window — not a popup on the tiny reactor.")
        info.setWordWrap(True); info.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        v.addWidget(info)
        lay.addWidget(box)
        mon = HudGroupBox("WORLD MONITOR")
        mv = QVBoxLayout(mon)
        self.world_refresh = self._slider_row(mv, "Headline refresh", int(self._data.get("world_monitor_refresh", 60)), 15, 600, " s")
        note = QLabel("World Monitor shows global clocks, CPU/RAM/GPU telemetry, a grid sweep, and live world headlines.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        mv.addWidget(note)
        lay.addWidget(mon)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "MEMORY")

    def _build_3d_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        status = QLabel("3D PREVIEW — load OBJ/STL/PLY and use the existing JARVIS 3D renderer.")
        status.setWordWrap(True); status.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        lay.addWidget(status)
        self._3d_host = QFrame(); self._3d_host.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(0,18,28,235),stop:0.5 rgba(0,8,14,245),stop:1 rgba(0,27,38,220));border:none;border-radius:14px;")
        hv = QVBoxLayout(self._3d_host)
        self._3d_status = QLabel("No 3D model loaded")
        self._3d_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._3d_status.setMinimumHeight(300)
        hv.addWidget(self._3d_status, 1)
        lay.addWidget(self._3d_host, 1)
        row = QHBoxLayout()
        load_btn = QPushButton("OPEN 3D MODEL")
        load_btn.clicked.connect(self._open_3d_model)
        row.addWidget(load_btn)
        reset = QPushButton("RESET VIEW")
        reset.clicked.connect(self._reset_3d_view)
        row.addWidget(reset)
        wire = QPushButton("WIREFRAME")
        wire.clicked.connect(self._toggle_3d_wireframe)
        row.addWidget(wire)
        lay.addLayout(row)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "3D")

    def _scan_camera_indexes(self) -> list[int]:
        """Probe short-lived opens so the picker shows only live sources, and
        always expose 0/1 (built-in webcam / OBS virtual camera convention).f"""
        idxs: list[int] = []
        try:
            from vision.camera import probe_camera
            for i in range(4):
                try:
                    if probe_camera(i):
                        idxs.append(i)
                except Exception:
                    pass
        except Exception:
            pass
        for fallback in (0, 1):
            if fallback not in idxs:
                idxs.append(fallback)
        return idxs

    def _build_video_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self._tab_intro(lay, "VIDEO  //  CAMERA SOURCE · LOCAL PLAYBACK")

        camera_box = HudGroupBox("CAMERA SOURCE")
        cam_form = QFormLayout(camera_box)
        cam_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.camera_source = QComboBox()
        self.camera_source.setMinimumWidth(250)
        current = str((self._data or {}).get("camera_index", "auto"))
        found = self._scan_camera_indexes()
        source_labels = [("auto", "AUTO DETECT")]
        for idx in found:
            tag = "OBS VIRTUAL CAMERA" if idx == 1 else "WEBCAM" if idx == 0 else "CAMERA"
            if idx != 0 and idx != 1:
                tag = f"DEVICE {idx}"
            source_labels.append((str(idx), f"{tag} (INDEX {idx})"))
        for value, label in source_labels:
            self.camera_source.addItem(label, value)
        set_i = self.camera_source.findData(current)
        if set_i >= 0:
            self.camera_source.setCurrentIndex(set_i)
        elif str(current).lstrip("-").isdigit():
            self.camera_source.addItem(f"CAMERA (INDEX {current})", str(current))
            self.camera_source.setCurrentIndex(self.camera_source.count() - 1)
        cam_form.addRow("Video source", self.camera_source)
        src_hint = QLabel("Choose the live camera for vision and the webcam button. "
                          "Index 0 is the built-in webcam; authors using OBS should "
                          "pick the OBS Virtual Camera entry. Applies after a re-open.")
        src_hint.setWordWrap(True)
        src_hint.setStyleSheet(f"color:{TEXT_MID};background:transparent;padding:2px;")
        cam_form.addRow(src_hint)
        lay.addWidget(camera_box)
        self._video_host = QFrame(); self._video_host.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {BG_DEEP},stop:0.5 {BG},stop:1 {SURFACE_ALT});border:none;border-radius:14px;")
        vv = QVBoxLayout(self._video_host)
        self._video_widget = None
        self._video_status = QLabel("No video loaded")
        self._video_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_status.setMinimumHeight(300)
        vv.addWidget(self._video_status, 1)
        lay.addWidget(self._video_host, 1)
        row = QHBoxLayout()
        open_btn = QPushButton("OPEN VIDEO")
        open_btn.clicked.connect(self._open_video)
        row.addWidget(open_btn)
        self.video_play = QPushButton("PLAY / PAUSE")
        self.video_play.clicked.connect(self._toggle_video)
        row.addWidget(self.video_play)
        back = QPushButton("◀ 5s")
        back.clicked.connect(lambda: self._seek_video(-5000))
        row.addWidget(back)
        fwd = QPushButton("5s ▶")
        fwd.clicked.connect(lambda: self._seek_video(5000))
        row.addWidget(fwd)
        stop = QPushButton("STOP")
        stop.clicked.connect(self._stop_video)
        row.addWidget(stop)
        lay.addLayout(row)
        self.video_seek = QSlider(Qt.Orientation.Horizontal)
        self.video_seek.setRange(0,0); self.video_seek.setTracking(True)
        self.video_seek.sliderMoved.connect(lambda v: self._set_video_position(v))
        lay.addWidget(self.video_seek)
        self._video_time = QLabel("00:00 / 00:00")
        self._video_time.setStyleSheet(f"color:{TEXT_MID};")
        lay.addWidget(self._video_time)
        self._video_file = QLabel("File: —")
        self._video_file.setStyleSheet(f"color:{TEXT_MID};")
        lay.addWidget(self._video_file)
        self.tabs.addTab(scroll, "VIDEO")

    def _build_image_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self._image_preview = QLabel("No image loaded")
        self._image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_preview.setMinimumHeight(430)
        self._image_preview.setStyleSheet(f'background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(1,13,22,235),stop:1 rgba(2,24,34,210));border:none;border-radius:14px;color:{TEXT_FAINT};font:700 10pt "Exo 2";')
        lay.addWidget(self._image_preview, 1)
        row = QHBoxLayout()
        open_btn = QPushButton("OPEN IMAGE")
        open_btn.clicked.connect(self._open_image)
        row.addWidget(open_btn)
        clear = QPushButton("CLEAR")
        clear.clicked.connect(lambda: self._image_preview.clear())
        row.addWidget(clear)
        lay.addLayout(row)
        self.tabs.addTab(scroll, "IMAGE")

    def _build_system_tab(self):
        page, lay, scroll = self._scroll_tab()
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(10)
        self._tab_intro(lay, "SYSTEM  //  INTERFACE · WINDOWS · SOUND")
        box = HudGroupBox("FEATURES")
        v = QVBoxLayout(box)
        feats = self._data.get("features", {}) or {}
        self._feature_checks = {}
        for key, label in [
            ("audio_reactive", "Audio-reactive Arc Reactor"),
            ("smooth_animations", "Smooth animations"),
            ("chat_history", "Keep chat history"),
            ("compact_mode", "Start in compact mode"),
            ("drag_reactor", "Drag the Arc Reactor"),
            ("remember_position", "Remember window position"),
            ("advanced_layout", "Enable advanced layout editor"),
            ("preview_tabs", "Enable preview tabs"),
            ("webview", "Enable Webview windows"),
            ("web_task", "Enable Web Task queue"),
            ("world_monitor", "Enable World Monitor"),
            ("floating_windows", "Allow multiple independent HUD windows"),
            ("draggable_panels", "Drag windows from the title bar"),
            ("enable_sfx", "Futuristic UI sound effects"),
        ]:
            v.addWidget(self._flag_checkbox(self._feature_checks, key, label, True))
        lay.addWidget(box)

        # ── CHAT LOG ────────────────────────────────────────────────
        chat = HudGroupBox("CHAT LOG")
        cv = QVBoxLayout(chat)
        zr = QHBoxLayout()
        zlab = QLabel("Chat text size"); zlab.setMinimumWidth(185)
        self.chat_zoom = QSlider(Qt.Orientation.Horizontal)
        self.chat_zoom.setRange(80, 200)          # 80% .. 200%
        self.chat_zoom.setValue(int(float(self._data.get("chat_font_scale", 100))))
        zval = QLabel(f"{self.chat_zoom.value()}%"); zval.setMinimumWidth(44)
        self.chat_zoom.valueChanged.connect(lambda val: zval.setText(f"{val}%"))
        zr.addWidget(zlab); zr.addWidget(self.chat_zoom, 1); zr.addWidget(zval)
        cv.addLayout(zr)
        frow = QHBoxLayout()
        flab = QLabel("Show in chat"); flab.setMinimumWidth(185)
        self.chat_filter = QComboBox()
        self.chat_filter.addItem("Everything (system + conversation)", userData="all")
        self.chat_filter.addItem("Only JARVIS and me (hide SYS/VIS noise)", userData="chat")
        self.chat_filter.addItem("Only system events (SYS / VIS / ERR)", userData="system")
        _cf = str(self._data.get("chat_filter", "all") or "all")
        _ci = self.chat_filter.findData(_cf)
        self.chat_filter.setCurrentIndex(_ci if _ci >= 0 else 0)
        self.chat_filter.setToolTip("Quick preset for the chat log. Use the individual toggles below for precise SYS/VIS/JARVIS/You control.")
        frow.addWidget(flab); frow.addWidget(self.chat_filter, 1)
        cv.addLayout(frow)

        cat_row = QHBoxLayout()
        cat_label = QLabel("Show specific")
        cat_label.setMinimumWidth(185)
        cat_row.addWidget(cat_label)
        saved_cats = self._data.get("chat_filter_categories", {}) or {}
        self.chat_category_checks = {}
        for key, label in (("sys", "SYS"), ("vis", "VIS"), ("jarvis", "JARVIS"), ("you", "YOU"), ("file", "FILE"), ("err", "ERR"), ("warn", "WARN")):
            cb = QCheckBox(label)
            cb.setChecked(bool(saved_cats.get(key, True)))
            self.chat_category_checks[key] = cb
            cat_row.addWidget(cb)
        cat_row.addStretch(1)
        cv.addLayout(cat_row)
        lay.addWidget(chat)
        info = QLabel("All options are saved to config/api_keys.json. Preview tabs do not create or reveal the old large HUD.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{TEXT_MID};background:transparent;padding:5px;")
        lay.addWidget(info)
        lay.addStretch(1)
        self.tabs.addTab(scroll, "SYSTEM")

    def _slider_row(self, parent_layout, label, value, lo, hi, suffix):
        row = QHBoxLayout()
        lab = QLabel(label); lab.setMinimumWidth(185)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(max(lo, min(hi, int(value))))
        slider.setTracking(True)
        slider.setSingleStep(1)
        slider.setPageStep(max(1, (hi - lo) // 10))
        val = QLabel(f"{slider.value()}{suffix}")
        val.setMinimumWidth(78)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val.setStyleSheet(f'color:{ACCENT_BRIGHT};font:800 8pt "Orbitron";background:rgba(0,28,40,210);border:none;border-radius:7px;padding:4px 7px;')
        slider.valueChanged.connect(lambda v, out=val, s=suffix: out.setText(f"{int(v)}{s}"))
        row.addWidget(lab)
        row.addWidget(slider, 1)
        row.addWidget(val)
        parent_layout.addLayout(row)
        return slider

    def _set_color(self, color: str):
        self._color = color
        self.color_btn.setText(color)

    def pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Choose JARVIS accent color")
        if c.isValid():
            self._set_color(c.name())

    # ------------------------------------------------------------------ layout
    def _load_layout_values(self):
        self.layout_element.blockSignals(True)
        self.layout_element.clear()
        self._layout_rows = {}
        parent = self.parent()
        items = []
        if parent is not None:
            for name, w in getattr(parent, "_system_layout_widgets", {}).items():
                if w is None:
                    continue
                g = w.geometry()
                items.append((name, {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(), "opacity": 100, "visible": w.isVisible()}))
            for name, w in getattr(parent, "_layout_widgets", {}).items():
                if w is None:
                    continue
                g = w.geometry()
                items.append((name, {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(), "opacity": 100, "visible": w.isVisible()}))
            for name, elem in getattr(parent, "_layout_elements", {}).items():
                self._layout_rows[name] = dict(elem)
                items.append((name, dict(elem)))
        seen = set()
        for name, elem in items:
            if name in seen:
                continue
            seen.add(name)
            self.layout_element.addItem(name)
            self._layout_rows[name] = dict(self._layout_rows.get(name, {}))
            self._layout_rows[name].update(elem)
        if self.layout_element.count():
            self.layout_element.setCurrentIndex(0)
        self.layout_element.blockSignals(False)
        self._on_layout_selection_changed(self.layout_element.currentText())

    def _on_layout_selection_changed(self, name):
        if not name or name not in self._layout_rows:
            return
        self._layout_selected_name = name
        e = self._layout_rows[name]
        self.layout_x.setValue(int(e.get("x", 50)))
        self.layout_y.setValue(int(e.get("y", 50)))
        self.layout_w.setValue(int(e.get("w", 180)))
        self.layout_h.setValue(int(e.get("h", 50)))
        self.layout_opacity.setValue(int(e.get("opacity", 100)))
        self.layout_visible.setChecked(bool(e.get("visible", True)))

    def _apply_layout_selection(self):
        name = self._layout_selected_name
        parent = self.parent()
        if not name or parent is None:
            return
        elem = self._layout_rows.setdefault(name, {})
        elem.update({"name": name, "x": self.layout_x.value(), "y": self.layout_y.value(), "w": self.layout_w.value(), "h": self.layout_h.value(), "opacity": self.layout_opacity.value(), "visible": self.layout_visible.isChecked()})
        widget = getattr(parent, "_system_layout_widgets", {}).get(name) or getattr(parent, "_layout_widgets", {}).get(name)
        if widget is not None:
            widget.setGeometry(self.layout_x.value(), self.layout_y.value(), self.layout_w.value(), self.layout_h.value())
            widget.setVisible(self.layout_visible.isChecked())
        if hasattr(parent, "_layout_elements"):
            parent._layout_elements[name] = dict(elem)
            if hasattr(parent, "_apply_layout_elements"):
                parent._apply_layout_elements()
            if hasattr(parent, "_save_layout_elements"):
                parent._save_layout_elements()
        self._status.setText("LAYOUT APPLIED")

    def _add_layout_element(self):
        parent = self.parent()
        if parent is None or not hasattr(parent, "_layout_elements"):
            return
        name = self.new_name.text().strip() or f"{self.new_kind.currentText()} {len(parent._layout_elements)+1}"
        text = self.new_text.text().strip() or name
        idx = 1
        base = name
        while name in parent._layout_elements:
            idx += 1
            name = f"{base} {idx}"
        parent._layout_elements[name] = {"name": name, "kind": self.new_kind.currentText(), "x": 70, "y": 70, "w": 240, "h": 50, "opacity": 100, "visible": True, "text": text, "color": self._color}
        if hasattr(parent, "_apply_layout_elements"):
            parent._apply_layout_elements()
        if hasattr(parent, "_save_layout_elements"):
            parent._save_layout_elements()
        self.new_name.clear(); self.new_text.clear()
        self._load_layout_values()
        self.layout_element.setCurrentText(name)
        self._status.setText("ELEMENT ADDED")

    def _feature_flag(self, key: str, default: bool = True) -> bool:
        """Current value of one feature switch (saved config first, widgets
        second) — so a guard reads what is actually stored, not what a button
        happens to show.
        """
        try:
            val = (self._data.get("features", {}) or {}).get(key)
            if isinstance(val, bool):
                return val
        except Exception:
            pass
        try:
            cb = (getattr(self, "_feature_checks", {}) or {}).get(key)
            if cb is not None:
                return bool(cb.isChecked())
            cb = (getattr(self, "_voice_checks", {}) or {}).get(key)
            if cb is not None:
                return bool(cb.isChecked())
        except Exception:
            pass
        return bool(default)

    def _open_external_layout_editor(self):
        parent = self.parent()
        if not self._feature_flag("advanced_layout", True):
            self._status.setText(
                "ADVANCED LAYOUT EDITOR IS DISABLED — SYSTEM → Enable advanced layout editor")
            return
        try:
            if parent is not None and hasattr(parent, "_open_layout_editor"):
                parent._open_layout_editor()
                self._status.setText("ADVANCED EDITOR OPEN")
        except Exception as exc:
            self._status.setText(f"LAYOUT ERROR: {exc}")

    # ---------------------------------------------------------------- preview tabs
    def _open_3d_model(self):
        if not self._feature_flag("preview_tabs", True):
            self._status.setText(
                "PREVIEW TABS ARE OFF — SYSTEM → Enable preview tabs")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open 3D model", "", "3D Models (*.obj *.stl *.ply *.off *.glb *.gltf);;All Files (*.*)")
        if not path:
            return
        try:
            parent = self.parent()
            viewer_cls = None
            if parent is not None and parent.__class__.__module__:
                from ui import Model3DView as viewer_cls  # late import avoids circular import at module load
            if viewer_cls is None:
                raise RuntimeError("3D renderer unavailable")
            if getattr(self, "_model_view", None) is None:
                self._model_view = viewer_cls(self._3d_host)
                old = self._3d_status
                old.hide()
                self._3d_host.layout().addWidget(self._model_view, 1)
            self._model_view.load_model(path)
            self._3d_file = path
            self._3d_status.hide()
            self._status.setText("3D MODEL LOADED")
        except Exception as exc:
            self._3d_status.setText(f"3D preview error: {exc}")
            self._3d_status.show()

    def _reset_3d_view(self):
        viewer = getattr(self, "_model_view", None)
        if viewer is not None and hasattr(viewer, "reset_view"):
            viewer.reset_view()

    def _toggle_3d_wireframe(self):
        viewer = getattr(self, "_model_view", None)
        if viewer is not None and hasattr(viewer, "toggle_wireframe"):
            viewer.toggle_wireframe()

    def _open_video(self):
        if not self._feature_flag("preview_tabs", True):
            self._status.setText(
                "PREVIEW TABS ARE OFF — SYSTEM → Enable preview tabs")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open video", "", "Video Files (*.mp4 *.avi *.mkv *.mov *.webm *.wmv);;All Files (*.*)")
        if not path:
            return
        self._video_file.setText(f"File: {Path(path).name}")
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PyQt6.QtMultimediaWidgets import QVideoWidget
            if self._video_widget is None:
                self._video_widget = QVideoWidget(self._video_host)
                self._video_host.layout().insertWidget(0, self._video_widget, 1)
                self._video_player = QMediaPlayer(self)
                self._audio_output = QAudioOutput(self)
                self._video_player.setAudioOutput(self._audio_output)
                self._video_player.setVideoOutput(self._video_widget)
                self._video_player.positionChanged.connect(self._settings_video_position_changed)
                self._video_player.durationChanged.connect(lambda d: self.video_seek.setRange(0, int(d)))
            self._video_player.setSource(__import__('PyQt6.QtCore', fromlist=['QUrl']).QUrl.fromLocalFile(path))
            self._video_status.hide()
            self._video_player.play()
            self._status.setText("VIDEO PLAYING")
        except Exception as exc:
            self._video_status.setText(f"Video preview unavailable: {exc}")
            self._video_status.show()

    def _settings_video_position_changed(self, pos):
        if hasattr(self, "video_seek") and not self.video_seek.isSliderDown():
            self.video_seek.setValue(int(pos))
        if hasattr(self, "_video_time"):
            dur = self._video_player.duration() if getattr(self, "_video_player", None) is not None else 0
            self._video_time.setText(f"{self._fmt_ms(pos)} / {self._fmt_ms(dur)}")

    @staticmethod
    def _fmt_ms(ms):
        s=max(0,int(ms)//1000); m,sec=divmod(s,60); h,m=divmod(m,60)
        return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    def _set_video_position(self, value):
        if getattr(self, "_video_player", None) is not None:
            self._video_player.setPosition(int(value))

    def _seek_video(self, delta):
        if getattr(self, "_video_player", None) is not None:
            self._video_player.setPosition(max(0, min(self._video_player.duration(), self._video_player.position()+int(delta))))

    def _toggle_video(self):
        p = getattr(self, "_video_player", None)
        if p is None:
            return
        from PyQt6.QtMultimedia import QMediaPlayer
        p.setPosition(p.position())
        p.play() if p.playbackState() != QMediaPlayer.PlaybackState.PlayingState else p.pause()

    def _stop_video(self):
        p = getattr(self, "_video_player", None)
        if p is not None:
            p.stop()

    def _open_image(self):
        if not self._feature_flag("preview_tabs", True):
            self._status.setText(
                "PREVIEW TABS ARE OFF — SYSTEM → Enable preview tabs")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open image", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*.*)")
        if not path:
            return
        from PyQt6.QtGui import QPixmap
        px = QPixmap(path)
        if px.isNull():
            self._image_preview.setText("Unable to load image")
            return
        self._image_path = path
        self._image_preview.setPixmap(px.scaled(self._image_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self._status.setText("IMAGE LOADED")

    def _on_onnx_voice_picked(self, index: int) -> None:
        """Selecting a discovered voice fills the model path automatically —
        and overwrites the typed override so the combo always wins visibly."""
        try:
            data = self.onnx_voice.itemData(index)
            if data:
                self.onnx_model_path.setText(str(data["model"]))
                self.onnx_status.setText(
                    f"Selected: {data['name']} — local, offline, no API key. "
                    "SAVE to apply; TEST SELECTED ENGINE to hear it."
                )
        except Exception:
            pass

    def _test_piper_voice(self) -> None:
        """TEST PIPER VOICE: load the picked Piper voice with the picked
        execution provider and speak one short line.

        Runs off the GUI thread and touches only the cached Piper path — it does
        not save settings and does not switch the active voice output mode. The
        provider ONNX Runtime actually used is reported, so a GPU request that
        fell back to CPU is visible instead of silent.
        """
        import threading
        _vdata = self.onnx_voice.currentData() or {}
        model = self.onnx_model_path.text().strip() or str(_vdata.get("model") or "")
        if not model:
            self.onnx_status.setText("Pick a Piper voice first (config/voices).")
            return
        config = str(_vdata.get("config") or DEFAULTS["onnx_voice_config"])
        provider = str(self.onnx_execution_provider.currentData() or "cpu")
        self.onnx_status.setText(f"Testing {Path(model).name} on {provider.upper()}…")

        def _run():
            try:
                from core.tts import create_tts_player, voice_status
                cfg = dict(self._data or {})
                cfg.update({
                    "tts_engine": "onnx",
                    "onnx_voice_model": model,
                    "onnx_voice_config": config,
                    "onnx_voice_speaker": self.onnx_speaker.text().strip() or "default",
                    "onnx_execution_provider": provider,
                    "voice_speed": float(self.voice_speed.value()) / 100.0,
                    "voice_volume": float(self.voice_volume.value()) / 100.0,
                })
                player = create_tts_player(cfg)
                player.warmup()
                used = voice_status().provider or "?"
                player.speak("Piper voice test. This is the JARVIS high voice model.")
                msg = f"PIPER OK · {Path(model).name} · {used}"
                QTimer.singleShot(0, self, lambda: self.onnx_status.setText(msg))
                QTimer.singleShot(0, self, lambda: self._status.setText(msg))
            except Exception as exc:
                msg = f"PIPER FAILED: {str(exc)[:120]}"
                QTimer.singleShot(0, self, lambda: self.onnx_status.setText(msg))
                QTimer.singleShot(0, self, lambda: self._status.setText(msg))
                self.write_log_safe(f"Piper voice test failed: {exc}")
        threading.Thread(target=_run, daemon=True).start()

    def _preview_sapi_voice(self) -> None:
        """Speak a preview through the selected Windows SAPI voice."""
        import threading
        vid = self.sapi_voice.currentData() or ""
        self._status.setText("PREVIEWING SAPI…")

        def _run():
            try:
                from core.tts import SapiTTSEngine
                SapiTTSEngine(voice=str(vid)).speak("This is a preview of the Windows voice.")
                QTimer.singleShot(0, self, lambda: self._status.setText("SAPI PREVIEW OK"))
            except Exception as exc:
                QTimer.singleShot(0, self, lambda: self._status.setText(f"SAPI PREVIEW FAILED: {str(exc)[:120]}"))
        threading.Thread(target=_run, daemon=True).start()

    # ---------------------------------------------------------------- settings
    def _collect_camera_index(self) -> object:
        value = None
        try:
            value = self.camera_source.currentData()
        except Exception:
            value = None
        if value is None:
            return getattr(self, "_data", {}).get("camera_index", "auto")
        return value

    def _collect(self):
        # Provider isolation: only the SELECTED provider's panel values are
        # harvested into the flat keys it owns; the other providers' legacy
        # keys are carried through from the loaded data untouched.
        try:
            _active_pid = self._selected_provider()
            _panel_values = self._provider_values(_active_pid)
        except Exception:
            _active_pid, _panel_values = "", {}
        _fish = _panel_values if _active_pid == "fishAudio" else None
        _eleven = _panel_values if _active_pid == "elevenLabs" else None
        _piper = _panel_values if _active_pid == "piperOnnx" else None
        _edge = _panel_values if _active_pid == "edgeTts" else None
        _sapi_vals = _panel_values if _active_pid == "sapi" else None
        # Derive voice_output_mode from provider + pipeline selection. The
        # Gemini pipeline modes (gemini / gemini_piper) are pipeline choices
        # that survive regardless of which provider panel is open — the Piper
        # provider IS the gemini_piper renderer — while any other provider
        # selected with a pipeline mode means local rendering.
        try:
            _vod = str(self.voice_output_mode.currentData() or "gemini")
        except Exception:
            _vod = "gemini"
        if _active_pid and _active_pid not in ("gemini", "piperOnnx") and _vod != "local":
            _vod = "local"
        features = {k: v.isChecked() for k, v in getattr(self, "_feature_checks", {}).items()}
        features["drag_reactor"] = self._drag.isChecked()
        features["remember_position"] = self._remember.isChecked()
        features["compact_mode"] = self._compact.isChecked()
        features["autostart"] = self._autostart.isChecked()
        features["hide_taskbar_button"] = self._taskbar.isChecked()
        features["hide_taskbar"] = False  # legacy key: never hide the Windows taskbar itself
        features["webview_engine"] = self._web_engine.isChecked()
        features["webview_music"] = self._web_music.isChecked()
        features["webview_js"] = self._web_js.isChecked()
        features["webview_external"] = self._web_external.isChecked()
        # MEMORY → "Keep a prompt index" was not collected at all, so the
        # checkbox was a no-op that reset itself to on every save.
        try:
            features["memory_index"] = self._memory_index.isChecked()
        except Exception:
            pass
        features.update({k: v.isChecked() for k, v in self._voice_checks.items()})
        features.update({k: v.isChecked() for k, v in getattr(self, "_perm_checks", {}).items()})
        chat_categories = {k: cb.isChecked() for k, cb in getattr(self, "chat_category_checks", {}).items()}
        return dict(
            ui_font=self.font.currentText(),
            ui_color=self._color,
            ui_opacity=self.ui_opacity.value(),
            compact_size=self.reactor_size.value(),
            reactor_opacity=self.reactor_opacity.value(),
            reactor_stroke_opacity=self.reactor_stroke_opacity.value(),
            reactor_animation_speed=self.anim_speed.value() / 100.0,
            equalizer_sensitivity=self.eq_sens.value() / 100.0,
            voice_speed=self.voice_speed.value() / 100.0,
            voice_pitch=self.voice_pitch.value() / 10.0,
            voice_volume=self.voice_volume.value() / 100.0,
            voice_mode=self.voice_mode.currentText(),
            tts_engine={"fishAudio": "fish_audio", "elevenLabs": "elevenlabs", "gemini": "edgetts",
                        "piperOnnx": "onnx", "edgeTts": "edgetts", "kokoro": "kokoro",
                        "sapi": "sapi"}.get(_active_pid,
                        str(self._data.get("tts_engine", "edgetts"))),
            tts_voice=(_edge or {}).get("voice", self._data.get("tts_voice", DEFAULTS["tts_voice"])),
            onnx_voice_model=(_piper or {}).get("modelPath", self._data.get("onnx_voice_model", "")),
            onnx_voice_config=(_piper or {}).get("configPath",
                                self._data.get("onnx_voice_config", DEFAULTS["onnx_voice_config"])),
            onnx_voice_speaker=(_piper or {}).get("speaker", self._data.get("onnx_voice_speaker", "default")),
            onnx_execution_provider=(_piper or {}).get("executionProvider",
                                      self._data.get("onnx_execution_provider", "cpu")),
            # A deliberate save records that the one-time gemini -> gemini_piper
            # lift has happened, so an explicit Gemini Native Audio choice made
            # from here on is never silently migrated again.
            voice_pipeline_migrated=True,
            # voice_name belongs to the GEMINI provider only — a save for any
            # other provider must not rewrite it (the Fish→Charon bug).
            voice_name=(self._data.get("voice_name", DEFAULTS["voice_name"])
                        if _active_pid != "gemini" else self.live_voice.currentText()),
            fish_audio_api_key=(_fish or {}).get("apiKey", self._data.get("fish_audio_api_key", "")),
            fish_audio_model_id=(_fish or {}).get("model", self._data.get("fish_audio_model_id", DEFAULTS["fish_audio_model_id"])),
            fish_audio_voice_id=(_fish or {}).get("voice", self._data.get("fish_audio_voice_id", "")),
            fish_audio_endpoint=(_fish or {}).get("endpoint", self._data.get("fish_audio_endpoint", DEFAULTS["fish_audio_endpoint"])),
            fish_audio_format=(_fish or {}).get("format", self._data.get("fish_audio_format", "mp3")),
            fish_audio_latency=(_fish or {}).get("latency", self._data.get("fish_audio_latency", "normal")),
            elevenlabs_api_key=(_eleven or {}).get("apiKey", self._data.get("elevenlabs_api_key", "")),
            elevenlabs_voice_id=(_eleven or {}).get("voice", self._data.get("elevenlabs_voice_id", DEFAULTS["elevenlabs_voice_id"])),
            elevenlabs_model_id=(_eleven or {}).get("model", self._data.get("elevenlabs_model_id", DEFAULTS["elevenlabs_model_id"])),
            sapi_voice=(_sapi_vals or {}).get("voice", self._data.get("sapi_voice", "")),
            voice_output_mode=_vod,
            assistant_name=self.assistant_name.text().strip() or "JARVIS",
            user_name=self.user_name.text().strip(),
            wake_words=[w.strip().lower() for w in self.wake_words.text().replace("\n", ",").split(",") if w.strip()],
            chat_font_scale=int(self.chat_zoom.value()),
            chat_filter=str(self.chat_filter.currentData() or "all"),
            chat_filter_categories=chat_categories,
            camera_index=self._collect_camera_index(),
            ai_provider=str(self.ai_provider.currentData() or "gemini"),
            ai_model=self.ai_model.currentText().strip(),
            model_roles={
                role: edit.text().strip()
                for role, edit in getattr(self, "_role_edits", {}).items()
                if edit.text().strip()
            },
            openai_api_key=self.openai_api_key.text().strip(),
            anthropic_api_key=self.anthropic_api_key.text().strip(),
            groq_api_key=self.groq_api_key.text().strip(),
            custom_provider_name=self.custom_provider_name.text().strip(),
            custom_ai_base_url=self.custom_ai_base_url.text().strip().rstrip("/"),
            custom_ai_api_key=self.custom_ai_api_key.text().strip(),
            custom_ai_model=self.custom_ai_model.text().strip(),
            web_homepage=self.web_homepage.text().strip() or DEFAULTS["web_homepage"],
            world_monitor_refresh=self.world_refresh.value(),
            memory_autosave=self._memory_autosave.isChecked(),
            panel_always_on_top=self._web_ontop.isChecked(),
            features=features,
        )

    def reset_defaults(self):
        try:
            self._memory_index.setChecked(True)
        except Exception:
            pass
        try:
            self._web_js.setChecked(True)
            self._web_external.setChecked(True)
        except Exception:
            pass
        self.font.setCurrentText(DEFAULTS["ui_font"])
        self._set_color(DEFAULTS["ui_color"])
        self.ui_opacity.setValue(DEFAULTS["ui_opacity"])
        self.reactor_size.setValue(DEFAULTS["compact_size"])
        self.reactor_opacity.setValue(DEFAULTS["reactor_opacity"])
        self.reactor_stroke_opacity.setValue(DEFAULTS["reactor_stroke_opacity"])
        self.anim_speed.setValue(100)
        self.eq_sens.setValue(100)
        self.voice_speed.setValue(100)
        self.voice_pitch.setValue(0)
        self.voice_volume.setValue(100)
        self.voice_mode.setCurrentText(DEFAULTS["voice_mode"])
        self.provider_selector.setCurrentIndex(self.provider_selector.findData("edgeTts"))
        self.tts_voice.setText(DEFAULTS["tts_voice"])
        self.live_voice.setCurrentText(DEFAULTS["voice_name"])
        self.fish_api_key.clear()
        self.fish_model_id.setText(DEFAULTS["fish_audio_model_id"])
        self.fish_voice_id.clear()
        self.fish_endpoint.setText(DEFAULTS["fish_audio_endpoint"])
        self.fish_format.setCurrentText(DEFAULTS["fish_audio_format"])
        self.fish_latency.setCurrentText(DEFAULTS["fish_audio_latency"])
        self.elevenlabs_api_key.clear()
        self.elevenlabs_voice_id.setText(DEFAULTS["elevenlabs_voice_id"])
        self.elevenlabs_model_id.setCurrentText(DEFAULTS["elevenlabs_model_id"])
        self.assistant_name.setText("JARVIS")
        try:
            self.wake_words.clear()
        except Exception:
            pass
        self.user_name.clear()
        self.ai_provider.setCurrentIndex(max(0, self.ai_provider.findData("gemini")))
        self.ai_model.setCurrentText("")
        self.openai_api_key.clear()
        self.anthropic_api_key.clear()
        self.groq_api_key.clear()
        self.custom_provider_name.clear()
        self.custom_ai_base_url.clear()
        self.custom_ai_api_key.clear()
        self.custom_ai_model.clear()
        if hasattr(self, "web_homepage"):
            self.web_homepage.setText(DEFAULTS["web_homepage"])
        if hasattr(self, "world_refresh"):
            self.world_refresh.setValue(DEFAULTS["world_monitor_refresh"])
        if hasattr(self, "_memory_autosave"):
            self._memory_autosave.setChecked(True)
        if hasattr(self, "_web_ontop"):
            self._web_ontop.setChecked(True)
        for _key, _cb in getattr(self, "chat_category_checks", {}).items():
            _cb.setChecked(True)
        if hasattr(self, "_web_engine"):
            self._web_engine.setChecked(True)
        if hasattr(self, "_web_music"):
            self._web_music.setChecked(True)
        self._compact.setChecked(True)
        self._drag.setChecked(True)
        self._remember.setChecked(True)
        self._autostart.setChecked(False)
        self._taskbar.setChecked(False)
        for key, cb in self._feature_checks.items():
            cb.setChecked(bool(DEFAULTS["features"].get(key, True)))
        for key, cb in self._voice_checks.items():
            cb.setChecked(key not in {"push_to_talk", "always_on"})
        for key, cb in getattr(self, "_perm_checks", {}).items():
            cb.setChecked(bool(DEFAULTS["features"].get(key, True)))
        self._status.setText("DEFAULTS READY — SAVE TO APPLY")

    def save_settings(self):
        try:
            new_data = self._collect()
            # Voice settings only take effect on engines built AFTER the save,
            # so drop the cached engine when a voice key actually changed —
            # otherwise SAVE looked like a no-op for the TEST button.
            _VOICE_KEYS = (
                "tts_engine", "tts_voice", "voice_name", "voice_output_mode",
                "onnx_voice_model", "onnx_voice_speaker",
                "elevenlabs_api_key", "elevenlabs_voice_id", "elevenlabs_model_id",
                "fish_audio_api_key", "fish_audio_model_id", "fish_audio_voice_id",
                "voice_speed", "voice_pitch", "voice_volume",
            )
            voice_changed = any(
                str(new_data.get(k)) != str((self._data or {}).get(k))
                for k in _VOICE_KEYS
            )
            # Guarded write (spec §39): backup, validate, write atomically,
            # re-read and verify, and roll the file back if the save came out
            # unreadable. A bad save used to leave the config broken until the
            # user noticed.
            warnings = ""
            try:
                from core import config_guard as _guard
                _merged = dict(self._data or {})
                _merged.update(new_data)
                _ok, _msg, _bak = _guard.save_with_history(_merged, tag="settings")
                if not _ok:
                    raise RuntimeError(_msg)
                if _msg.startswith("saved with warnings"):
                    warnings = " · " + _msg.split(":", 1)[-1].strip()
                self._data = _guard.read(self._config_path)
            except Exception as _guard_exc:
                # Guard unavailable or failed: fall back to the plain save so a
                # broken guard can never block the user's settings.
                self._data = save(self._config_path, **new_data)
                warnings = f" · guard: {str(_guard_exc)[:80]}"
            if voice_changed:
                try:
                    from core.tts import clear_tts_cache
                    clear_tts_cache()
                except Exception:
                    pass
            self.setWindowTitle("J.A.R.V.I.S Settings — SAVED")
            self._status.setText("SAVED"
                                 + (" · voice engine will rebuild" if voice_changed else "")
                                 + warnings)
            self.settings_saved.emit(dict(self._data))
            parent = self.parent()
            if parent is not None and hasattr(parent, "_apply_full_settings"):
                parent._apply_full_settings(dict(self._data))
                if hasattr(parent, '_check_autostart') and hasattr(parent, '_toggle_autostart'):
                    desired = bool(self._autostart.isChecked())
                    if parent._check_autostart() != desired:
                        parent._toggle_autostart()
            return self._data
        except Exception as exc:
            self._status.setText(f"SAVE ERROR: {exc}")
            return None

    def _autosave_quiet(self):
        """Auto-save after a slider release (resize/opacity tweaks apply live)."""
        try:
            data = self.save_settings()
            if data is not None:
                self._status.setText("AUTO-SAVED")
        except Exception:
            pass

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _header_move(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _header_release(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()

    def closeEvent(self, event):
        try:
            self._stop_video()
        except Exception:
            pass
        self.hide()
        event.ignore()


def launch_settings(parent=None):
    created = QApplication.instance() is None
    app = QApplication.instance() or QApplication(sys.argv)
    win = SettingsWindow(parent=parent)
    win.show(); win.raise_(); win.activateWindow()
    return app.exec() if created else 0


if __name__ == "__main__":
    raise SystemExit(launch_settings())
