from __future__ import annotations

import enum
import json
import math
import os
import platform
import random
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import numpy as np

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QAbstractAnimation, QEasingCurve, QElapsedTimer, QMimeData, QObject, QPoint, QPointF,
    QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal, QPropertyAnimation, QEvent,
    QVariantAnimation,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QDesktopServices,
    QPen, QPixmap, QRadialGradient, QShortcut, QMouseEvent, QTextCursor,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _MEDIA_OK = True
except Exception:
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    _MEDIA_OK = False

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    _WEB_OK = True
except Exception:
    QWebEngineView = QWebEnginePage = QWebEngineSettings = None
    _WEB_OK = False

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QSlider,
    QListWidget, QListWidgetItem, QGridLayout, QGraphicsOpacityEffect, QInputDialog,
    QAbstractItemView, QTextBrowser,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

# ==================== LOGO ADJUSTMENTS (edit these) ====================
LOGO_TITLE        = "JARVIS"
LOGO_RING_RADIUS  = 108.0
LOGO_TEXT_SIZE    = 28
LOGO_GLOW         = 3.20
LOGO_PARTICLES    = 0
LOGO_PARTICLE_GLOW = 1.4
LOGO_SPEED        = 2.0
LOGO_CLICK_SWELL  = 0.015
LOGO_CLICK_TIME   = 0.76
LOGO_DOT_RINGS    = True
LOGO_DOT_SIZE     = 0.95
LOGO_DOT_GAP      = 0.3
LOGO_DOT_COUNT    = 14
LOGO_STARTUP_ANIM = True
# Subtle startup scale: the logo opens slightly larger, then settles to the
# normal compact logo size. Keep this independent from LOGO_BG_FILL.
STARTUP_LOGO_SIZE = 190
# Settled logo diameter after the startup shrink. Same asset, only geometry
# changes: the animation interpolates STARTUP_LOGO_SIZE -> STARTUP_LOGO_END_SIZE.
STARTUP_LOGO_END_SIZE = 180
# How far the settled logo widget may exceed the compact window without
# clipping (the painted artwork keeps a transparent margin).
STARTUP_END_FIT_SLACK = 6
# Settled-logo clamp is reported once per process (JarvisUI._settled_logo_size).
_STARTUP_END_CLAMP_LOGGED = False


class _StartupPhase(enum.Enum):
    """Single owner of the startup-logo lifecycle (replaces five booleans).

    STARTUP   — big presentation is on screen, waiting for the voice trigger,
                the fallback timer or a click.
    ANIMATING — the one geometry timeline (position + size together) runs.
    SETTLED   — compact home reached; normal layout writers own the geometry.
    """

    STARTUP = "startup"
    ANIMATING = "animating"
    SETTLED = "settled"
# Startup-only visual tuning. The black core is a soft transparent disc and
# the cyan bloom fades out smoothly without changing the top-level window size.
STARTUP_CORE_FADE_IN_MS = 420
STARTUP_GLOW_FADE_MS = 1800
STARTUP_GLOW_SCALE = 1.62
STARTUP_HITBOX_PAD = 30
# Core backdrop painted under the logo rings. 0 = fully transparent (default):
# the logo then composites directly over whatever panel is behind it, so its
# background ALWAYS matches the main panel / command deck exactly. Raise it
# toward 255 only if you want a self-contained dark core (old look ~= 225).
LOGO_BG_FILL = 80
# ======================================================================

from ui_settings import load as _ui_load, save as _ui_save, SettingsWindow, _ControlCenterBackdrop
from ui_layout import clamp_size as _clamp_size, LayoutEditorDialog, load_layout as _load_layout_file, save_layout as _save_layout_file
from ui_theme import (
    button_css as _theme_button_css, primary_button_css as _theme_primary_css,
    field_css as _theme_field_css, card_css as _theme_card_css,
    header_css as _theme_header_css, chip_css as _theme_chip_css,
    section_css as _theme_section_css, title_css as _theme_title_css,
    subtitle_css as _theme_subtitle_css, scrollbar_css as _theme_scrollbar_css,
    hidden_scrollbar_css as _theme_hidden_scrollbar_css, tab_css as _theme_tab_css,
    slider_css as _theme_slider_css, checkbox_css as _theme_checkbox_css,
    status_color as _theme_status_color, meter_color as _theme_meter_color,
    icon_font_css as _theme_icon_font_css,
    panel_title_css as _theme_panel_title_css,
    panel_subtitle_css as _theme_panel_subtitle_css,
    panel_icon_css as _theme_panel_icon_css,
    status_chip_css as _theme_status_chip_css,
    separator_css as _theme_separator_css,
    micro_label_css as _theme_micro_label_css,
    PALETTE as _THEME_PALETTE, HUE_LINKED as _THEME_HUE_LINKED,
    PALETTE_DEFAULTS as _THEME_PALETTE_DEFAULTS, DEFAULT_ACCENT,
    migrate_accent as _theme_migrate_accent, qcolor as _theme_qcolor,
    CONTROL_SM, CONTROL_MD, CONTROL_LG,
    SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL,
    HEADER_MIN_H, PANEL_MIN_W, PANEL_MIN_H,
    rgba as _theme_rgba, mix as _theme_mix, shade as _theme_shade,
    SURFACE as _THEME_SURFACE, SURFACE_ALT as _THEME_SURFACE_ALT,
)


def _read_full_config() -> dict:
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()


class C:
    """Alias table onto the shared token set in ``ui_theme``.

    The HUD has ~550 call sites that read ``C.PRI`` / ``C.BORDER`` / … and the
    accent picker rotates this table's hues at runtime, so the names stay — but
    no colour is *decided* here any more. Every value resolves to a ui_theme
    token, which is the only place in the project that defines one. Values are
    opaque hex because these reach ``QColor()``/``QPen()`` as well as QSS.
    """
    BG        = _THEME_PALETTE["BG"]
    PANEL     = _THEME_PALETTE["PANEL"]
    PANEL2    = _THEME_PALETTE["PANEL2"]
    BORDER    = _THEME_PALETTE["BORDER"]
    BORDER_B  = _THEME_PALETTE["BORDER_B"]
    BORDER_A  = _THEME_PALETTE["BORDER_A"]
    PRI       = _THEME_PALETTE["PRI"]
    PRI_DIM   = _THEME_PALETTE["PRI_DIM"]
    PRI_GHO   = _THEME_PALETTE["PRI_GHO"]
    # ACC was a second, orange accent. The design language allows exactly one
    # signature accent and reserves it for the live/armed state, so it is now
    # the shared gold token and ACC2 is the status amber.
    ACC       = _THEME_PALETTE["ACC"]
    ACC2      = _THEME_PALETTE["ACC2"]
    GREEN     = _THEME_PALETTE["GREEN"]
    GREEN_D   = _THEME_PALETTE["GREEN_D"]
    RED       = _THEME_PALETTE["RED"]
    MUTED_C   = _THEME_PALETTE["MUTED_C"]
    TEXT      = _THEME_PALETTE["TEXT"]
    TEXT_DIM  = _THEME_PALETTE["TEXT_DIM"]
    TEXT_MED  = _THEME_PALETTE["TEXT_MED"]
    WHITE     = _THEME_PALETTE["WHITE"]
    DARK      = _THEME_PALETTE["DARK"]
    BAR_BG    = _THEME_PALETTE["BAR_BG"]

    # Control heights: three allowed values, not a free-for-all of 26/28/30/34.
    H_SM      = CONTROL_SM
    H_MD      = CONTROL_MD
    H_LG      = CONTROL_LG


class FuturisticBackdrop(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())
            self.lower()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(obj.rect())
            self.lower()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self.rect()
            w, h = max(1, r.width()), max(1, r.height())
            bounds = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
            cut = max(10, min(16, min(w, h) * 0.045))
            path = QPainterPath()
            path.moveTo(QPointF(bounds.left() + cut, bounds.top()))
            path.lineTo(QPointF(bounds.right() - cut, bounds.top()))
            path.lineTo(QPointF(bounds.right(), bounds.top() + cut))
            path.lineTo(QPointF(bounds.right(), bounds.bottom() - cut))
            path.lineTo(QPointF(bounds.right() - cut, bounds.bottom()))
            path.lineTo(QPointF(bounds.left() + cut, bounds.bottom()))
            path.lineTo(QPointF(bounds.left(), bounds.bottom() - cut))
            path.lineTo(QPointF(bounds.left(), bounds.top() + cut))
            path.closeSubpath()
            p.setClipPath(path)

            # Solid main-panel background — no gradient or radial glow.
            p.fillPath(path, QBrush(QColor(C.PANEL)))

            p.setPen(QPen(QColor(0, 170, 210, 28), 1))
            step = 24
            for x in range(0, w + step, step):
                p.drawLine(x, 0, x, h)
            for y in range(0, h + step, step):
                p.drawLine(0, y, w, y)

            p.setPen(QPen(QColor(0, 212, 255, 70), 1))
            p.drawLine(int(w * 0.08), int(h * 0.76), int(w * 0.92), int(h * 0.12))
            p.drawLine(int(w * 0.02), int(h * 0.28), int(w * 0.72), int(h * 0.98))
            ring = min(w, h) * 0.42
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 212, 255, 48), 1))
            p.drawEllipse(QPointF(w * 0.78, h * 0.76), ring, ring)
            p.setPen(QPen(QColor(255, 107, 0, 48), 1))
            p.drawArc(QRectF(w * 0.58, h * 0.48, ring * 2, ring * 2), 25 * 16, 92 * 16)

            p.setClipping(False)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(C.BORDER_B), 0.8))
            p.drawPath(path)
        except Exception:
            pass
        p.end()


# The hue-linked keys and their defaults come from the token set, so a user
# accent rotates exactly the same set whether it is applied from the token
# module or from here.
_HUE_LINKED = _THEME_HUE_LINKED
_PALETTE_DEFAULTS: dict[str, str] = dict(_THEME_PALETTE_DEFAULTS)

DEFAULT_UI_COLOR = DEFAULT_ACCENT


def apply_ui_accent(accent_hex: str) -> bool:
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    """QColor for a token or a plain colour string.

    Delegates to ui_theme so rgba()/8-digit token values work here too — the
    old ``QColor(h)`` returned an invalid black for those.
    """
    try:
        return _theme_qcolor(h, a)
    except Exception:
        c = QColor(h)
        c.setAlpha(a)
        return c


def _sfx(name: str) -> None:
    try:
        from core import sfx as _sfx_mod
        fn = getattr(_sfx_mod, name, None)
        if callable(fn):
            fn()
    except Exception:
        pass


def _sfx_enabled(host=None) -> bool:
    try:
        feats = getattr(host, '_features', {}) if host is not None else {}
        if isinstance(feats, dict) and 'enable_sfx' in feats:
            return bool(feats['enable_sfx'])
        cfg = _ui_load(API_FILE)
        return bool((cfg.get('features', {}) or {}).get('enable_sfx', True))
    except Exception:
        return True


_nvml_lib: object = None
_nvml_ok:  object = None


def _nvml_gpu_windows() -> float:
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0
        self.net_down = 0.0
        self.net_up   = 0.0
        self.gpu  = -1.0
        self.tmp  = -1.0
        self.disk = 0.0
        self.batt = -1.0
        self.plugged = False
        self.uptime  = 0
        self.procs   = 0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net_down = recv / (1024 * 1024)
            net_up   = sent / (1024 * 1024)
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = net_down = net_up = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()
        tmp = self._get_temp()

        try:
            disk = float(psutil.disk_usage('C:\\' if _OS == 'Windows' else '/').percent)
        except Exception:
            disk = 0.0
        try:
            batt_info = psutil.sensors_battery()
            batt = float(batt_info.percent) if batt_info else -1.0
            plugged = bool(batt_info.power_plugged) if batt_info else False
        except Exception:
            batt, plugged = -1.0, False
        try:
            uptime = int(time.time() - psutil.boot_time())
        except Exception:
            uptime = 0
        try:
            procs = len(psutil.pids())
        except Exception:
            procs = 0

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.net_down = net_down
            self.net_up   = net_up
            self.gpu = gpu
            self.tmp = tmp
            self.disk = disk
            self.batt = batt
            self.plugged = plugged
            self.uptime = uptime
            self.procs = procs

    def _get_gpu(self) -> float:
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass

        if _OS == "Windows":
            return _nvml_gpu_windows()

        try:
            import ctypes
            _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            nv = ctypes.CDLL(_lib)
            nv.nvmlInit_v2()
            dev = ctypes.c_void_p()
            nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "net_down": self.net_down,
                "net_up": self.net_up,
                "gpu": self.gpu,
                "tmp": self.tmp,
                "disk": self.disk,
                "batt": self.batt,
                "plugged": self.plugged,
                "uptime": self.uptime,
                "procs": self.procs,
            }


def _fmt_uptime(seconds) -> str:
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "--"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _fmt_rate(mbps: float) -> str:
    """Format a MB/s rate as a compact human string for the HUD cards."""
    try:
        kb = max(0.0, float(mbps)) * 1024.0
    except Exception:
        return "--"
    if kb >= 1024.0:
        return f"{kb / 1024.0:.1f} MB/s"
    if kb >= 10.0:
        return f"{kb:.0f} KB/s"
    if kb >= 1.0:
        return f"{kb:.1f} KB/s"
    return f"{kb * 1024.0:.0f} B/s"


_LOCAL_IP_CACHE: str = ""


def _local_ip() -> str:
    """Best-effort LAN address of this machine (cached after the first call)."""
    global _LOCAL_IP_CACHE
    if _LOCAL_IP_CACHE:
        return _LOCAL_IP_CACHE
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.4)
            s.connect(("8.8.8.8", 80))
            _LOCAL_IP_CACHE = str(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        try:
            _LOCAL_IP_CACHE = socket.gethostbyname(socket.gethostname())
        except Exception:
            _LOCAL_IP_CACHE = "127.0.0.1"
    return _LOCAL_IP_CACHE


# Processes that are never "active apps" from the user's point of view.
_APP_SKIP_NAMES = {
    "system idle process", "system", "registry", "memory compression",
    "secure system", "idle", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "winlogon.exe", "fontdrvhost.exe",
    "dwm.exe", "spoolsv.exe", "conhost.exe", "wudfhost.exe",
    "audiodg.exe", "wmiapsrv.exe", "wmiprvse.exe", "searchindexer.exe",
    "runtimebroker.exe", "sihost.exe", "ctfmon.exe", "dllhost.exe",
    "python.exe", "pythonw.exe", "jarvis.exe",
}

# Persistent {pid: psutil.Process} map. psutil.cpu_percent() needs two samples
# on the SAME Process object, so the handles have to survive between polls.
_APP_PROC_CACHE: dict = {}


def _top_active_apps(limit: int = 3) -> list:
    """Top running applications by live CPU usage — [(name, cpu_percent), ...].

    Uses a persistent handle cache so ``cpu_percent()`` reports a real delta
    instead of the 0.0 a freshly created Process always returns.
    """
    rows: list = []
    try:
        current: dict = {}
        for proc in psutil.process_iter(["name"]):
            try:
                current[proc.pid] = proc
            except Exception:
                continue
        for pid in [p for p in _APP_PROC_CACHE if p not in current]:
            _APP_PROC_CACHE.pop(pid, None)
        for pid, proc in current.items():
            if pid == 0 or pid == os.getpid():
                continue
            cached = _APP_PROC_CACHE.get(pid)
            if cached is None:
                # First sighting: prime the counter, report nothing yet.
                _APP_PROC_CACHE[pid] = proc
                try:
                    proc.cpu_percent(interval=None)
                except Exception:
                    _APP_PROC_CACHE.pop(pid, None)
                continue
            try:
                pct = float(cached.cpu_percent(interval=None))
                name = str((cached.info or {}).get("name") or "").strip()
            except Exception:
                continue
            if not name or pct <= 0.0:
                continue
            if name.lower() in _APP_SKIP_NAMES:
                continue
            rows.append((name[:-4] if name.lower().endswith(".exe") else name, pct))
        rows.sort(key=lambda r: r[1], reverse=True)
    except Exception:
        return []

    out: list = []
    seen: set = set()
    for name, pct in rows:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, pct))
        if len(out) >= limit:
            break
    return out


_metrics = _SysMetrics()


class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        self._base_scale = 1.0
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def set_audio_level(self, level: float) -> None:
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    @property
    def assistant_name(self) -> str:
        return self._assistant_name

    @assistant_name.setter
    def assistant_name(self, value: str) -> None:
        self._assistant_name = str(value).strip() or "J.A.R.V.I.S"

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()

        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._base_scale = 1.03
                self._base_halo  = 122.0
            elif self.muted:
                self._base_scale = random.uniform(0.998, 1.002)
                self._base_halo  = random.uniform(15, 28)
            else:
                self._base_scale = random.uniform(1.001, 1.008)
                self._base_halo  = random.uniform(48, 68)
            self._last_t = now

        if self.muted:
            self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
        elif self.speaking:
            self._tgt_scale = self._base_scale + amp * 0.13
            self._tgt_halo  = self._base_halo  + amp * 95.0
        else:
            self._tgt_scale = self._base_scale + amp * 0.06
            self._tgt_halo  = self._base_halo  + amp * 75.0

        sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        boost  = 1.0 + amp * 1.6
        speeds = ([1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9])
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd * boost) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3) * boost) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75) * boost) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31

        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        if self._face_px:
            fsz    = int(fw * 0.62 * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
        else:
            orb_r = int(fw * 0.27 * self._scale)
            oc    = (200, 0, 50) if self.muted else (0, 60, 110)
            for i in range(8, 0, -1):
                r2  = int(orb_r * i / 8)
                frc = i / 8
                a   = max(0, min(255, int(self._halo * 1.1 * frc)))
                p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), a)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
            p.setFont(QFont("Exo 2", 13, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 80, cy - 14, 160, 28),
                       Qt.AlignmentFlag.AlignCenter, self._assistant_name)

        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  {self.state}",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Exo 2", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()


class MetricBar(QWidget):
    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Exo 2", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Orbitron", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()




class MarkdownTextBrowser(QTextBrowser):
    """JARVIS markdown surface with safe links and one-click code copying.

    Supports the Markdown features most useful for AI output and .md notes:
    headings, **bold**, *italic*, inline `code`, fenced code blocks, lists,
    blockquotes, horizontal rules, Markdown links and plain http(s) URLs.
    External links are opened in Google Chrome on Windows when available.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._md_clipboard_items: dict[str, str] = {}
        self.anchorClicked.connect(self._on_anchor_clicked)

    @staticmethod
    def _escape(text: str) -> str:
        import html
        return html.escape(str(text or ''), quote=False)

    @staticmethod
    def _safe_url(url: str) -> str | None:
        from urllib.parse import urlparse
        u = str(url or '').strip().strip('<>')
        try:
            parsed = urlparse(u)
        except Exception:
            return None
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.netloc:
            return None
        return u

    def _open_chrome(self, url: str) -> None:
        """Open a web link in Chrome, falling back to the normal desktop browser."""
        try:
            if platform.system() == 'Windows':
                candidates = [
                    Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
                    Path(os.environ.get('PROGRAMFILES', '')) / 'Google/Chrome/Application/chrome.exe',
                    Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Google/Chrome/Application/chrome.exe',
                ]
                for exe in candidates:
                    if exe and exe.is_file():
                        subprocess.Popen([str(exe), url], **_WIN_HIDE)
                        return
                try:
                    subprocess.Popen(['cmd', '/c', 'start', '', 'chrome', url], **_WIN_HIDE)
                    return
                except Exception:
                    pass
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            try:
                QDesktopServices.openUrl(QUrl(url))
            except Exception:
                pass

    def _on_anchor_clicked(self, url: QUrl) -> None:
        raw = url.toString()
        if raw.startswith('copy://'):
            key = raw[len('copy://'):]
            text = self._md_clipboard_items.get(key)
            if text is not None:
                try:
                    QApplication.clipboard().setText(text)
                except Exception:
                    pass
            return
        safe = self._safe_url(raw)
        if safe:
            self._open_chrome(safe)
            return
        # mailto and other non-web links are intentionally left to Qt's desktop handler.
        if raw.lower().startswith('mailto:'):
            try:
                QDesktopServices.openUrl(QUrl(raw))
            except Exception:
                pass

    def _stash(self, html_fragment: str) -> str:
        key = f'block_{len(self._md_clipboard_items) + 1}'
        self._md_clipboard_items[key] = html_fragment
        return key

    def _inline_html(self, text: str) -> str:
        """Convert inline Markdown without ever treating source text as raw HTML."""
        import re, html
        raw = str(text or '')
        protected: list[str] = []

        def protect(value: str) -> str:
            token = f'\x00MD{len(protected)}\x00'
            protected.append(value)
            return token

        # Escape first so AI output can never inject arbitrary HTML.
        out = html.escape(raw, quote=False)

        # Images become safe clickable links to the image instead of silently
        # loading remote content in QTextBrowser.
        def image_repl(m):
            alt = html.escape(m.group(1), quote=False)
            url = self._safe_url(m.group(2))
            if not url:
                return alt
            return protect(f'<a href="{html.escape(url, quote=True)}" style="color:{C.PRI};text-decoration:none;">[IMAGE: {alt}]</a>')

        out = re.sub(r'!\[([^\]]*)\]\((https?://[^)\s]+)\)', image_repl, out)

        # Markdown links.
        def link_repl(m):
            label = html.escape(m.group(1), quote=False)
            url = self._safe_url(m.group(2))
            if not url:
                return label
            return protect(f'<a href="{html.escape(url, quote=True)}" style="color:{C.PRI};text-decoration:underline;">{label}</a>')

        out = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', link_repl, out)

        # Autolink bare URLs, preserving trailing punctuation outside the anchor.
        def url_repl(m):
            url = m.group(1)
            trail = ''
            while url and url[-1] in '.,;:!?)]':
                trail = url[-1] + trail
                url = url[:-1]
            safe = self._safe_url(url)
            if not safe:
                return m.group(0)
            return protect(f'<a href="{html.escape(safe, quote=True)}" style="color:{C.PRI};text-decoration:underline;">{html.escape(url, quote=False)}</a>') + html.escape(trail)

        out = re.sub(r'(?<!["\'>])(https?://[^\s<>]+)', url_repl, out)

        # Inline code, bold, then italic. Code/links are protected from formatting regexes.
        out = re.sub(r'`([^`\n]+)`', lambda m: protect(
            f'<span style="background:{C.DARK};border:1px solid {C.BORDER_B};padding:1px 4px;">'
            f'<font face="Share Tech Mono" color="{C.TEXT_MED}">{html.escape(m.group(1), quote=False)}</font></span>'
        ), out)
        out = re.sub(r'(\*\*|__)(.+?)\1', r'<b>\2</b>', out)
        out = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', out)
        out = re.sub(r'(?<!_)_([^_\n]+)_(?!_)', r'<i>\1</i>', out)

        for i, fragment in enumerate(protected):
            out = out.replace(f'\x00MD{i}\x00', fragment)
        return out

    def _code_block_html(self, code: str, language: str = '') -> str:
        import html
        key = self._stash(str(code))
        lang = html.escape(str(language or '').strip().lower(), quote=False)
        label = f'CODE · {lang}' if lang else 'CODE'
        safe_code = html.escape(str(code), quote=False)
        return (
            f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:8px 0;">'
            f'<tr><td style="background:{C.DARK};border:1px solid {C.BORDER_B};padding:0;">'
            f'<div style="background:rgba(0,92,120,55);border-bottom:1px solid {C.BORDER_B};padding:5px 8px;">'
            f'<span style="color:{C.PRI};font-weight:700;font-size:8pt;">{label}</span>'
            f'&nbsp;&nbsp;<a href="copy://{key}" style="color:{C.WHITE};text-decoration:none;">[ COPY ]</a>'
            f'</div>'
            f'<pre style="margin:0;padding:9px;color:{C.TEXT};font-family:\'Share Tech Mono\';font-size:8.5pt;white-space:pre-wrap;">{safe_code}</pre>'
            f'</td></tr></table>'
        )

    def markdown_to_html(self, markdown: str) -> str:
        import re
        text = str(markdown or '').replace('\r\n', '\n').replace('\r', '\n')
        lines = text.split('\n')
        chunks: list[str] = []
        para: list[str] = []
        in_code = False
        code_lines: list[str] = []
        code_lang = ''
        list_kind: str | None = None
        list_items: list[str] = []
        quote_lines: list[str] = []

        def flush_para():
            nonlocal para
            if not para:
                return
            body = '<br/>'.join(self._inline_html(x) for x in para)
            chunks.append(f'<p style="margin:7px 0;line-height:1.4;">{body}</p>')
            para = []

        def flush_list():
            nonlocal list_kind, list_items
            if not list_items:
                list_kind = None
                return
            tag = 'ul' if list_kind == 'ul' else 'ol'
            items = ''.join(f'<li style="margin:3px 0;">{x}</li>' for x in list_items)
            chunks.append(f'<{tag} style="margin:5px 0 8px 18px;padding-left:14px;">{items}</{tag}>')
            list_items = []
            list_kind = None

        def flush_quote():
            nonlocal quote_lines
            if not quote_lines:
                return
            body = '<br/>'.join(self._inline_html(x) for x in quote_lines)
            chunks.append(
                f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:7px 0;">'
                f'<tr><td style="background:rgba(0,76,102,45);border-left:3px solid {C.PRI};padding:7px 10px;color:{C.TEXT_MED};">{body}</td></tr></table>'
            )
            quote_lines = []

        for line in lines:
            fence = re.match(r'^\s*```\s*([\w.+-]*)\s*$', line)
            if fence:
                flush_para(); flush_list(); flush_quote()
                if not in_code:
                    in_code = True; code_lang = fence.group(1); code_lines = []
                else:
                    chunks.append(self._code_block_html('\n'.join(code_lines), code_lang))
                    in_code = False; code_lang = ''; code_lines = []
                continue
            if in_code:
                code_lines.append(line)
                continue

            if not line.strip():
                flush_para(); flush_list(); flush_quote(); continue
            if re.match(r'^\s*(---+|\*\*\*+)\s*$', line):
                flush_para(); flush_list(); flush_quote()
                chunks.append(f'<hr style="border:none;border-top:1px solid {C.BORDER_B};margin:10px 0;">')
                continue

            m = re.match(r'^\s*(#{1,6})\s+(.+?)\s*#*\s*$', line)
            if m:
                flush_para(); flush_list(); flush_quote()
                level = len(m.group(1)); sizes = {1:'14pt',2:'12pt',3:'11pt',4:'10pt',5:'9.5pt',6:'9pt'}
                chunks.append(f'<h{level} style="color:{C.WHITE if level<=2 else C.PRI};font-family:Orbitron;font-size:{sizes[level]};margin:10px 0 5px 0;">{self._inline_html(m.group(2))}</h{level}>')
                continue

            m = re.match(r'^\s*>\s?(.*)$', line)
            if m:
                flush_para(); flush_list(); quote_lines.append(m.group(1)); continue

            m = re.match(r'^\s*[-+*]\s+(.+)$', line)
            if m:
                flush_para(); flush_quote()
                if list_kind not in (None, 'ul'): flush_list()
                list_kind = 'ul'; list_items.append(self._inline_html(m.group(1))); continue

            m = re.match(r'^\s*\d+[.)]\s+(.+)$', line)
            if m:
                flush_para(); flush_quote()
                if list_kind not in (None, 'ol'): flush_list()
                list_kind = 'ol'; list_items.append(self._inline_html(m.group(1))); continue

            flush_list(); flush_quote(); para.append(line)

        if in_code:
            chunks.append(self._code_block_html('\n'.join(code_lines), code_lang))
        flush_para(); flush_list(); flush_quote()
        return f'<div style="color:{C.TEXT};font-family:Rajdhani;font-size:10pt;">{"".join(chunks)}</div>'

    def set_markdown(self, markdown: str) -> None:
        self._md_clipboard_items.clear()
        self.setHtml(self.markdown_to_html(markdown))

    def append_markdown(self, markdown: str) -> None:
        self.insertHtml(self.markdown_to_html(markdown))
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.ensureCursorVisible()

    def copy_all(self) -> None:
        try:
            QApplication.clipboard().setText(self.toPlainText())
        except Exception:
            pass


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    _FILTER_cfg = {"scale": 100, "mode": "all", "categories": {"sys": True, "vis": True, "jarvis": True, "you": True, "file": True, "err": True, "warn": True}}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        try:
            _cfg = _read_full_config()
            LogWidget._FILTER_cfg["scale"] = int(float(_cfg.get("chat_font_scale", 100) or 100))
            LogWidget._FILTER_cfg["mode"] = str(_cfg.get("chat_filter", "all") or "all")
            cats = _cfg.get("chat_filter_categories", {})
            if isinstance(cats, dict):
                LogWidget._FILTER_cfg["categories"] = {k: bool(cats.get(k, True)) for k in LogWidget._FILTER_cfg["categories"]}
        except Exception:
            pass
        self._filter_mode = LogWidget._FILTER_cfg["mode"]
        _scale = max(60, min(220, LogWidget._FILTER_cfg["scale"]))
        self.setFont(QFont("Share Tech Mono", max(7, round(9 * _scale / 100))))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 0px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: transparent;
                border: none;
                min-height: 20px;
            }}
            QScrollBar:horizontal {{ background: transparent; height: 0px; border: none; }}
            QScrollBar::handle:horizontal {{ background: transparent; border: none; }}
        """)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"
        self._stream_active = False
        self._stream_cur = None
        self._stream_who = 'J.A.R.V.I.S.'
        self._stream_col = C.PRI
        self._stream_has_text = False
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def set_filter(self, mode: str) -> None:
        self._filter_mode = str(mode or "all").lower()
        if self._filter_mode not in ("all", "chat", "system"):
            self._filter_mode = "all"

    def set_font_scale(self, pct: int) -> None:
        _scale = max(60, min(220, int(pct)))
        self.setFont(QFont("Share Tech Mono", max(7, round(9 * _scale / 100))))

    def set_filter_categories(self, categories: dict) -> None:
        if isinstance(categories, dict):
            LogWidget._FILTER_cfg["categories"] = {k: bool(categories.get(k, True)) for k in LogWidget._FILTER_cfg["categories"]}
        self.update()

    def _passes_filter(self, tl: str) -> bool:
        _ai_pfx = f"{self._ai_name_lc}:"
        if tl.startswith("you:"): category = "you"
        elif tl.startswith("vis:"): category = "vis"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): category = "jarvis"
        elif tl.startswith("file:"): category = "file"
        elif tl.startswith("err:") or "error" in tl: category = "err"
        elif tl.startswith("warn:") or "warning" in tl: category = "warn"
        else: category = "sys"
        if not bool(LogWidget._FILTER_cfg.get("categories", {}).get(category, True)): return False
        if self._filter_mode == "chat": return category in {"you", "jarvis"}
        if self._filter_mode == "system": return category not in {"you", "jarvis"}
        return True

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        if not self._passes_filter(tl):
            self._typing = False
            QTimer.singleShot(0, self._next)
            return
        try:
            stamp = time.strftime("%H:%M:%S")
        except Exception:
            stamp = "--:--:--"
        _labels = {
            "you":  ("YOU", C.WHITE), "ai":  (self._ai_name_lc.upper(), C.PRI),
            "err":  ("ERROR", C.RED), "file": ("FILE", C.GREEN), "sys": ("SYS", C.ACC2),
        }
        _lbl, _col = _labels.get(self._tag, ("SYS", C.ACC2))
        try:
            cur = self.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertHtml(
                f'<b style="color:{_col}">{_lbl}</b>'
                f'&nbsp;<span style="color:#3a6a7a;font-size:small;">{stamp}</span><br/>'
            )
        except Exception:
            pass
        for _p in ("you:", f"{self._ai_name_lc}:", "jarvis:", "file:", "sys:", "err:"):
            if tl.startswith(_p):
                self._text = self._text[len(_p):].lstrip()
                break
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)


_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Exo 2", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Exo 2", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Exo 2", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Exo 2", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Exo 2", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Exo 2", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Exo 2", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Exo 2", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


_face_cascade = None


def _detect_faces(jpg_bytes: bytes) -> list[tuple[float, float, float, float]]:
    global _face_cascade
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    try:
        if _face_cascade is None:
            _face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            if _face_cascade.empty():
                _face_cascade = None
                return []
        arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return []
        h, w = img.shape[:2]
        scale = 320.0 / max(1, w)
        small = cv2.resize(img, (max(80, int(w * scale)), max(60, int(h * scale))))
        faces = _face_cascade.detectMultiScale(small, scaleFactor=1.15, minNeighbors=4, minSize=(36, 36))
        sw = w / small.shape[1]
        return [(float(x * sw / w), float(y * sw / h),
                 float(fw * sw / w), float(fh * sw / h)) for (x, y, fw, fh) in faces]
    except Exception:
        return []


class _ScanOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pos = 0.0
        self._single = False
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self.hide()

    def start(self, single: bool = False) -> None:
        try:
            self.setGeometry(self.parentWidget().rect())
        except Exception:
            pass
        self._pos = 0.0
        self._single = single
        self.show()
        self.raise_()
        if not self._tmr.isActive():
            self._tmr.start(30)

    def stop(self) -> None:
        try:
            self._tmr.stop()
        except Exception:
            pass
        self.hide()

    def _step(self):
        try:
            if self.parentWidget() is not None and self.geometry() != self.parentWidget().rect():
                self.setGeometry(self.parentWidget().rect())
        except Exception:
            pass
        self._pos += 0.028
        if self._pos >= 1.0:
            if self._single:
                self.stop()
                return
            self._pos = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        W, H = self.width(), self.height()
        y = self._pos * H
        for dy, alpha, color in ((-9, 40, C.PRI), (-5, 90, C.PRI), (-2, 170, C.PRI)):
            p.fillRect(QRectF(0, y + dy, W, 5 if dy == -2 else 4), qcol(color, alpha))
        p.fillRect(QRectF(0, y - 1, W, 2), qcol(C.WHITE, 235))
        p.end()


class _FeatheredCamView(QWidget):
    def __init__(self, parent=None):
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._img = QImage()
        self._faces: list[tuple[float, float, float, float]] = []
        self._obj_boxes: list[tuple] = []
        self._gest_boxes: list[tuple] = []
        self._labels: list[tuple] = []
        self._frame_n = 0
        self._had_faces = False
        self._feather = 34
        self._mask: QImage | None = None
        self._mask_size: tuple[int, int] = (0, 0)
        self._last_paint_ms = 0.0
        self._scan = _ScanOverlay(self)
        self.resize(480, 360)
        self.setMinimumSize(240, 180)
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinMaxButtonsHint)
        self._resizing = False
        self._drag_from = None
        self._drag_origin = None
        self._resize_from = None
        self._resize_w0 = self.width()
        self._resize_h0 = self.height()
        self._geom_save_timer = QTimer(self)
        self._geom_save_timer.setSingleShot(True)
        self._geom_save_timer.setInterval(600)
        self._geom_save_timer.timeout.connect(self._save_geom)
        self._load_geom()

    def _build_mask(self, w: int, h: int) -> QImage:
        if self._mask is not None and self._mask_size == (w, h):
            return self._mask
        try:
            import numpy as _np
            f = max(14.0, min(72.0, min(w, h) * 0.16))
            xs = _np.arange(w, dtype=_np.float32)
            ys = _np.arange(h, dtype=_np.float32)
            dx = _np.minimum(xs + 0.5, w - 0.5 - xs)
            dy = _np.minimum(ys + 0.5, h - 0.5 - ys)
            d = _np.minimum.outer(dy, dx)
            a = _np.clip(d / f, 0.0, 1.0)
            a = a * a * (3.0 - 2.0 * a)
            buf = _np.empty((h, w, 4), dtype=_np.uint8)
            buf[..., 0] = 255
            buf[..., 1] = 255
            buf[..., 2] = 255
            buf[..., 3] = (a * 255.0).astype(_np.uint8)
            img = QImage(buf.tobytes(), w, h, w * 4,
                         QImage.Format.Format_ARGB32_Premultiplied).copy()
        except Exception:
            img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0xFFFFFFFF)
        self._mask = img
        self._mask_size = (w, h)
        return img

    def set_frame(self, px: QPixmap) -> None:
        if px.isNull():
            return
        now = time.monotonic() * 1000.0
        if self._resizing:
            return
        if (now - self._last_paint_ms) < 40.0 and self._frame_n > 0:
            return
        self._last_paint_ms = now
        try:
            target = self.size()
            if target.width() < 8 or target.height() < 8:
                return
            scaled = px.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
            x = max(0, (scaled.width() - target.width()) // 2)
            y = max(0, (scaled.height() - target.height()) // 2)
            self._img = scaled.copy(x, y, target.width(), target.height()).toImage().convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied)
            self._frame_n += 1
            self.update()
        except Exception:
            pass

    def set_faces(self, rects) -> None:
        try:
            self._faces = [(float(a), float(b), float(c), float(d)) for (a, b, c, d) in (rects or [])]
            self.update()
        except Exception:
            pass

    def set_scan(self, on: bool, single: bool = False) -> None:
        try:
            if on:
                self._scan.start(single=single)
            else:
                self._scan.stop()
        except Exception:
            pass

    def set_overlay(self, boxes, kind: str = "obj", scan: bool = True) -> None:
        try:
            rects = [(float(a), float(b), float(c), float(d)) for (a, b, c, d) in (boxes or [])]
            if kind == "face":
                self._faces = rects
            else:
                self._objs = rects
            self._overlay_kind = kind
            if scan:
                self.set_scan(True, single=True)
            self.update()
        except Exception:
            pass

    def resizeEvent(self, e):
        if getattr(self, '_geom_save_timer', None) is None:
            super().resizeEvent(e)
            return
        self._resizing = True
        try:
            self._mask = None
            self._geom_save_timer.start()
        except Exception:
            pass
        QTimer.singleShot(140, self._end_resize)
        super().resizeEvent(e)

    def _end_resize(self):
        self._resizing = False

    def _load_geom(self):
        try:
            import json as _j
            from pathlib import Path as _P
            p = _P(__file__).resolve().parent / 'config' / 'ui_layout.json'
            if p.exists():
                g = (_j.loads(p.read_text(encoding='utf-8')) or {}).get('camera_window', {})
                if g.get('w') and g.get('h'):
                    self.resize(int(g['w']), int(g['h']))
                if g.get('x') is not None:
                    self.move(int(g['x']), int(g['y']))
        except Exception:
            pass

    def _save_geom(self):
        try:
            import json as _j
            from pathlib import Path as _P
            p = _P(__file__).resolve().parent / 'config' / 'ui_layout.json'
            data = {}
            if p.exists():
                try:
                    data = _j.loads(p.read_text(encoding='utf-8')) or {}
                except Exception:
                    data = {}
            sg = QApplication.primaryScreen().availableGeometry()
            x = max(sg.left(), min(self.x(), sg.right() - max(80, self.width())))
            y = max(sg.top(), min(self.y(), sg.bottom() - max(60, self.height())))
            data['camera_window'] = {'x': x, 'y': y,
                                     'w': self.width(), 'h': self.height()}
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_j.dumps(data, indent=2), encoding='utf-8')
        except Exception:
            pass

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._img.isNull():
            p.drawImage(0, 0, self._img)
            try:
                mask = self._build_mask(self.width(), self.height())
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                p.drawImage(0, 0, mask)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            except Exception:
                pass
            self._draw_corner_brackets(p)
            try:
                r = QRectF(1.5, 1.5, self.width() - 3.0, self.height() - 3.0)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(qcol(C.PRI, 150), 1.2))
                path = QPainterPath(); cut = min(14.0, min(r.width(), r.height()) * 0.08)
                path.moveTo(r.left()+cut, r.top()); path.lineTo(r.right()-cut, r.top())
                path.lineTo(r.right(), r.top()+cut); path.lineTo(r.right(), r.bottom()-cut)
                path.lineTo(r.right()-cut, r.bottom()); path.lineTo(r.left()+cut, r.bottom())
                path.lineTo(r.left(), r.bottom()-cut); path.lineTo(r.left(), r.top()+cut); path.closeSubpath()
                p.drawPath(path)
            except Exception:
                pass
        W, H = self.width(), self.height()
        for (fx, fy, fw, fh) in self._faces:
            x, y, w, h = fx * W, fy * H, fw * W, fh * H
            L = max(10, min(w, h) * 0.28)
            p.setPen(QPen(qcol(C.PRI, 220), 2.0))
            for cx0, cy0, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1),
                                     (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
                p.drawLine(QPointF(cx0, cy0), QPointF(cx0 + dx * L, cy0))
                p.drawLine(QPointF(cx0, cy0), QPointF(cx0, cy0 + dy * L))
        p.end()

    def _draw_corner_brackets(self, p: QPainter) -> None:
        try:
            W, H = float(self.width()), float(self.height())
            m, L = 14.0, 26.0
            pen = QPen(qcol(C.PRI, 200), 2.0)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for cx0, cy0, dx, dy in ((m, m, 1, 1), (W - m, m, -1, 1),
                                     (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
                p.drawLine(QPointF(cx0, cy0), QPointF(cx0 + dx * L, cy0))
                p.drawLine(QPointF(cx0, cy0), QPointF(cx0, cy0 + dy * L))
        except Exception:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.hide()
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            gp = e.globalPosition().toPoint()
            if self.width() - e.position().x() <= 22 and self.height() - e.position().y() <= 22:
                self._begin_resize_tracking(gp)
            else:
                self._drag_from = gp
                self._drag_origin = self.frameGeometry().topLeft()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        gp = e.globalPosition().toPoint()
        if getattr(self, '_resize_from', None) is not None and e.buttons() & Qt.MouseButton.LeftButton:
            dw = gp.x() - self._resize_from.x()
            dh = gp.y() - self._resize_from.y()
            self.resize(max(self.minimumSize().width(), self._resize_w0 + dw),
                        max(self.minimumSize().height(), self._resize_h0 + dh))
            return
        if getattr(self, '_drag_from', None) is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(self._drag_origin + (gp - self._drag_from))
            return
        if self.width() - e.position().x() <= 22 and self.height() - e.position().y() <= 22:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if getattr(self, '_resize_from', None) is not None:
            self._resize_from = None
            self._geom_save_timer.start()
        elif getattr(self, '_drag_from', None) is not None:
            self._geom_save_timer.start()
        self._drag_from = None
        super().mouseReleaseEvent(e)

    def _begin_resize_tracking(self, gp):
        self._resize_from = gp
        self._resize_w0 = self.width()
        self._resize_h0 = self.height()


class _CameraPreview(QWidget):
    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Exo 2", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Exo 2", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self._drag_pos = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.hide()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.position().toPoint()
            e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(self.pos() + (e.position().toPoint() - self._drag_pos))
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        try:
            if getattr(self, '_scan', None) is None:
                self._scan = _ScanOverlay(self._img_lbl)
            self._scan.start(single=True)
        except Exception:
            pass
        self._timer.start(6_000)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Exo 2", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Exo 2", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Exo 2", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Exo 2", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.position().toPoint()
            e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag_pos", None) is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(self.pos() + (e.position().toPoint() - self._drag_pos))
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


class HueWheel(QWidget):
    hue_picked    = pyqtSignal(str)
    hue_committed = pyqtSignal(str)

    _RING = 16

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()
        ang = math.atan2(dy, dx)
        return (ang / (2 * math.pi)) % 1.0

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    saved = pyqtSignal(str, str, str, str)
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Exo 2", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Exo 2", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Exo 2", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Exo 2", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Exo 2", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Exo 2", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Exo 2", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Exo 2", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Exo 2", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Exo 2", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Exo 2", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Exo 2", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Exo 2", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Exo 2", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Exo 2", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Exo 2", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Exo 2", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Exo 2", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    picked = pyqtSignal()
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Exo 2", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Exo 2", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Exo 2", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Exo 2", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Exo 2", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Exo 2", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        orb_wrap = QWidget(); orb_wrap.setStyleSheet('background: transparent;')
        olay = QVBoxLayout(orb_wrap); olay.setContentsMargins(0, 0, 0, 0)
        try:
            from dashboard.brain3d import BrainGraph3D
            orb = BrainGraph3D(get_graph=self._brain_graph)
            orb.setMinimumHeight(250)
            orb.set_activity('remembering')
            olay.addWidget(orb)
        except Exception as exc:
            orb = None
            cap = QLabel(f'Orb unavailable: {exc}')
            cap.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;")
            olay.addWidget(cap)
        self._lay.addWidget(orb_wrap)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} memories · attached to the JARVIS core · lives only on this machine")
        cap.setWordWrap(True)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setFont(QFont("Exo 2", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setFont(QFont("Exo 2", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(200, 30 * min(len(rows), 6) + 8))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: none; background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 2, 6, 2)
            ilay.setSpacing(3)

            for r in rows[:8]:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Exo 2", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Exo 2", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Exo 2", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: none; }}
                    QPushButton:hover {{ color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Exo 2", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _brain_graph(self):
        try:
            from memory.manager import get_brain_memory
            g = get_brain_memory().graph()
        except Exception:
            g = {"nodes": [{"id": 0, "label": "JARVIS CORE", "category": "CORE", "activity": 0.0}], "links": []}
        nodes = {n.get("id"): n for n in (g.get("nodes") or [])}
        links = list(g.get("links") or [])
        seen_ids = set(nodes)
        try:
            from memory.memory_manager import all_entries_for_ui
            for i, r in enumerate(all_entries_for_ui()):
                nid = f"legacy_{i}"
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                nodes[nid] = {
                    "id": nid, "label": f"{r.get('category', '?')}: "
                    f"{str(r.get('key', ''))[:22]} — {str(r.get('value', ''))[:22]}",
                    "category": str(r.get('category', 'DEFAULT')).upper(),
                    "activity": 1.0,
                }
                links.append({"s": 0, "t": nid, "type": "has_memory", "weight": 0.6})
        except Exception:
            pass
        return {"nodes": list(nodes.values()), "links": links}

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Exo 2", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Exo 2", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Exo 2", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", f"Translate this text to English: {text}"),
            ("SUMMARISE", f"Summarise this: {text}"),
            ("EXPLAIN",   f"Explain this: {text}"),
            ("FIX",       f"Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Exo 2", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.hide()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.position().toPoint()
            e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(self.pos() + (e.position().toPoint() - self._drag_pos))
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class RemoteKeyOverlay(QWidget):
    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 3px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Exo 2", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 3px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Exo 2", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Orbitron", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 2px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Exo 2", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Exo 2", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Exo 2", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Exo 2", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 3px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Exo 2", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 3px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 2px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Orbitron", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 3px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 2px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class _ArcLogo(QWidget):
    def __init__(self, parent=None, size=220):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._sweep_a = 200.0
        self._sweep_b = 40.0
        self._orbit = 0.0
        self._twinkle = 0.0
        self._live_amp = 0.0
        self._amp_disp = 0.0
        self._pulse = 0.0
        # LOGO_BG_FILL is intentionally startup-only. The compact/panel logo
        # must stay transparent so it never paints a dark disc over the HUD.
        self._startup_bg_fill_active = False
        try:
            _rng = random.Random(7)
            self._dots = [
                (_rng.uniform(0, 360), _rng.uniform(0.40, 0.485),
                 _rng.uniform(-6, 6) or 2.0, _rng.uniform(1.0, 2.1),
                 _rng.uniform(0, 6.28))
                for _ in range(26)
            ]
            self._stars = [
                (_rng.uniform(0, 360), _rng.uniform(0.30, 0.49),
                 _rng.uniform(0.8, 1.6), _rng.uniform(0, 6.28))
                for _ in range(46)
            ]
        except Exception:
            self._dots, self._stars = [], []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(20)
        self._graphic_opacity = 1.0
        self._stroke_opacity = 1.0
        # Paint-time opacity — NOT QGraphicsOpacityEffect. A graphics effect
        # makes Qt route input through an offscreen pixmap, which on some
        # platforms only treats the *opaque* painted pixels as a valid mouse
        # hit-target — so thin strokes (the ring/arcs) stop registering
        # clicks while denser fills (the core glow, the "JARVIS" text) still
        # do. That's what made the logo draggable only near its center/text.
        # Painting the opacity ourselves keeps the full widget rect clickable.

    def set_logo_size(self, size: int) -> None:
        self.setFixedSize(max(120, int(size)), max(120, int(size)))
        self.update()

    def set_graphic_opacity(self, percent: int) -> None:
        self._graphic_opacity = max(0.0, min(1.0, int(percent) / 100.0))
        self.update()

    def set_stroke_opacity(self, percent: int) -> None:
        self._stroke_opacity = max(0.0, min(1.0, int(percent) / 100.0))
        self.update()

    @property
    def stroke_opacity(self) -> int:
        return int(round(self._stroke_opacity * 100))

    @property
    def graphic_opacity(self) -> int:
        return int(round(self._graphic_opacity * 100))

    def set_audio_level(self, level: float) -> None:
        try:
            lv = max(0.0, min(1.0, float(level)))
        except Exception:
            return
        self._live_amp = max(self._live_amp, min(1.0, (lv ** 0.62) * 1.85))

    def _tick(self):
        speed = max(0.25, float(getattr(self, '_animation_speed', 1.0)))
        boost = 1.0 + self._amp_disp * 2.2
        self._sweep_a = (self._sweep_a + 0.55 * speed * boost) % 360.0
        self._sweep_b = (self._sweep_b - 0.38 * speed * boost) % 360.0
        self._orbit = (self._orbit + 0.12 * speed) % 360.0
        self._twinkle = (self._twinkle + 0.05 * speed) % (math.pi * 2.0)
        self._pulse = (self._pulse + 0.075 * speed) % (math.pi * 2.0)
        self._live_amp *= 0.94
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.42
        self.update()

    def enterEvent(self, e):
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().enterEvent(e)

    def leaveEvent(self, e):
        super().leaveEvent(e)

    def _line(self, p, cx, cy, ang_deg, r1, r2, pen):
        a = math.radians(ang_deg)
        p.setPen(pen)
        p.drawLine(QPointF(cx + math.cos(a) * r1, cy + math.sin(a) * r1),
                   QPointF(cx + math.cos(a) * r2, cy + math.sin(a) * r2))

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setOpacity(self._graphic_opacity)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        cx, cy = W * 0.5, H * 0.5
        b = float(min(W, H))
        amp = self._amp_disp
        so = self._stroke_opacity
        Rs = 1.0 + amp * 0.07
        R = b * 0.335 * Rs

        # LOGO_BG_FILL applies ONLY to the startup presentation. Once the
        # logo settles onto the panel, the background becomes fully transparent
        # regardless of the configured LOGO_BG_FILL value.
        bg_fill = LOGO_BG_FILL if getattr(self, "_startup_bg_fill_active", False) else 0
        core = QRadialGradient(QPointF(cx, cy), R)
        core.setColorAt(0.0, qcol("#04070c", int(bg_fill * so)))
        core.setColorAt(0.82, qcol("#04070c", int(bg_fill * 0.95 * so)))
        core.setColorAt(1.0, qcol("#04070c", 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(QPointF(cx, cy), R, R)

        p.setBrush(Qt.BrushStyle.NoBrush)
        for rr, aa in ((0.40, 26), (0.435, 16), (0.465, 9)):
            a = int(min(255, aa * (0.75 + amp * 1.1)) * so)
            if a > 0:
                p.setPen(QPen(qcol(C.PRI, a), max(1.0, b * 0.006)))
                r = b * rr * (1.0 + amp * 0.03)
                p.drawEllipse(QPointF(cx, cy), r, r)

        ring_rect = QRectF(cx - R, cy - R, R * 2, R * 2)
        for w, a in ((max(2.2, b * 0.016), 90), (max(1.0, b * 0.006), 235)):
            p.setPen(QPen(qcol(C.PRI, int(min(255, a * (0.8 + amp * 0.5)) * so)), w))
            p.drawEllipse(ring_rect)

        p.setPen(QPen(qcol(C.WHITE, int(225 * so)), max(1.0, b * 0.0045)))
        r_in = R * 0.90
        p.drawEllipse(QPointF(cx, cy), r_in, r_in)

        for phase, span, frac, alpha, w in (
            (self._sweep_a, 52, 1.06, 245, 2.4),
            (self._sweep_b, 34, 1.13, 170, 1.8),
            (self._sweep_a * 0.5 + 140, 22, 0.97, 130, 1.4),
        ):
            rr = R * frac
            p.setPen(QPen(qcol(C.WHITE, int(alpha * so)), max(1.0, b * 0.004 * w / 2)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2),
                      int(phase * 16), int(span * 16))

        orb_r = b * 0.455
        p.setPen(QPen(qcol(C.PRI, int(70 * so)), 1.0))
        p.drawEllipse(QPointF(cx, cy), orb_r, orb_r)
        for k in range(8):
            deg = k * 45.0 + self._orbit
            rad = math.radians(deg)
            for off in (-0.012, 0.012):
                a2 = rad + off
                p.setPen(QPen(qcol(C.WHITE, int(150 * so)), max(1.0, b * 0.004)))
                p.drawLine(
                    QPointF(cx + math.cos(a2) * orb_r, cy + math.sin(a2) * orb_r),
                    QPointF(cx + math.cos(a2) * (orb_r - b * 0.028),
                            cy + math.sin(a2) * (orb_r - b * 0.028)))
        nd = math.radians(self._sweep_b * 1.7)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.WHITE, int(230 * so))))
        p.drawEllipse(QPointF(cx + math.cos(nd) * orb_r, cy + math.sin(nd) * orb_r),
                      max(1.4, b * 0.006), max(1.4, b * 0.006))

        for ang0, frac, spd, sz, ph in getattr(self, '_dots', []):
            ang = math.radians(ang0 + self._orbit * spd)
            tw = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self._twinkle * 2.0 + ph))
            a = int(min(255, 200 * tw) * so)
            if a <= 0:
                continue
            rr = b * frac
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.WHITE, a)))
            d = max(1.0, b * 0.0032 * sz)
            p.drawEllipse(QPointF(cx + math.cos(ang) * rr, cy + math.sin(ang) * rr), d, d)
        for ang0, frac, sz, ph in getattr(self, '_stars', []):
            tw = 0.30 + 0.40 * (0.5 + 0.5 * math.sin(self._twinkle + ph))
            a = int(min(255, 150 * tw) * so)
            if a <= 0:
                continue
            ang = math.radians(ang0)
            rr = b * frac
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            d = max(1.0, b * 0.0022 * sz)
            p.drawEllipse(QPointF(cx + math.cos(ang) * rr, cy + math.sin(ang) * rr), d, d)

        font = QFont('Rajdhani', max(9, int(b * 0.072)), QFont.Weight.Bold)
        try:
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 135)
        except Exception:
            pass
        p.setFont(font)
        p.setPen(QPen(qcol(C.WHITE, int(248 * so)), 1))
        p.drawText(QRectF(cx - b * 0.44, cy - b * 0.06, b * 0.88, b * 0.12),
                   Qt.AlignmentFlag.AlignCenter, 'JARVIS')

        p.end()


class VideoPreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('VideoPreview')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(f"QFrame#VideoPreview{{background:transparent;border:none;}}")
        self._player = None
        self._audio = None
        self._video = None
        self._duration = 0
        self._drag_target = self.window()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        self._controls = QWidget(self)
        self._controls.setStyleSheet("background:rgba(3,20,30,140);border:1px solid rgba(74,196,239,120);border-radius:9px;")
        cr = QHBoxLayout(self._controls); cr.setContentsMargins(8,6,8,6); cr.setSpacing(6)
        self._title = QLabel('VIDEO PREVIEW · NO FILE')
        self._title.setStyleSheet("color:#9ae8ff;font:700 8pt 'Exo 2';background:transparent;")
        cr.addWidget(self._title,1)
        open_btn = _GlowButton('OPEN','＋',compact=True); open_btn.clicked.connect(self._choose_file); cr.addWidget(open_btn)
        self._play_btn = _GlowButton('PLAY','▶',compact=True); self._play_btn.clicked.connect(self.toggle_play); cr.addWidget(self._play_btn)
        stop_btn = _GlowButton('STOP','■',compact=True); stop_btn.clicked.connect(self.stop); cr.addWidget(stop_btn)
        browser_btn = _GlowButton('BROWSER','🌐',compact=True); browser_btn.setToolTip('Open current file in system browser (codec fallback)'); browser_btn.clicked.connect(self.open_in_browser); cr.addWidget(browser_btn)
        layout.addWidget(self._controls)
        self._controls.setCursor(Qt.CursorShape.SizeAllCursor)
        self._controls.installEventFilter(self)

        self._label = QLabel('No video loaded')
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumHeight(210)
        self._label.setStyleSheet(_theme_card_css() + "color:#5796ad;")
        layout.addWidget(self._label, 1)

        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0,0); self._seek.setSingleStep(1000); self._seek.setPageStep(5000); self._seek.setTracking(True)
        self._seek.setStyleSheet(_theme_slider_css())
        layout.addWidget(self._seek)

        time_row = QHBoxLayout(); time_row.setSpacing(8)
        self._time = QLabel('00:00 / 00:00'); self._time.setMinimumWidth(110)
        self._time.setStyleSheet(_theme_chip_css() + "font:8pt 'Orbitron';")
        time_row.addWidget(self._time); time_row.addStretch(1)
        back = _GlowButton('−5s','◀',compact=True); back.clicked.connect(lambda:self._seek_by(-5000)); time_row.addWidget(back)
        fwd = _GlowButton('+5s','▶',compact=True); fwd.clicked.connect(lambda:self._seek_by(5000)); time_row.addWidget(fwd)
        layout.addLayout(time_row)

        self._seek.sliderMoved.connect(self._on_slider_moved)
        self._seek.sliderReleased.connect(self._on_slider_released)

    @staticmethod
    def _fmt(ms: int) -> str:
        secs=max(0,int(ms//1000)); h,rem=divmod(secs,3600); m,s=divmod(rem,60)
        return f'{h:02d}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'

    def eventFilter(self,obj,event):
        if obj is getattr(self,'_controls',None):
            if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                self._drag_origin=event.globalPosition().toPoint(); self._window_origin=self.window().frameGeometry().topLeft(); return False
            if event.type()==QEvent.Type.MouseMove and getattr(self,'_drag_origin',None) is not None and event.buttons() & Qt.MouseButton.LeftButton:
                self.window().move(self._window_origin + (event.globalPosition().toPoint()-self._drag_origin)); return True
            if event.type()==QEvent.Type.MouseButtonRelease: self._drag_origin=None; return False
        return super().eventFilter(obj,event)

    _AUDIO_EXTS = {'.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'}

    def _ensure_player(self):
        if self._player is not None: return True
        if QMediaPlayer is None or QAudioOutput is None:
            self._label.show()
            self._label.setText('Qt Multimedia is unavailable — install PyQt6 + system codecs, or use OPEN IN BROWSER.')
            return False
        try:
            self._audio = QAudioOutput(self)
            try:
                self._audio.setVolume(0.9)
            except Exception:
                pass
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio)
            self._player.positionChanged.connect(self._position_changed)
            self._player.durationChanged.connect(self._duration_changed)
            self._player.playbackStateChanged.connect(self._playback_state_changed)
            self._player.errorOccurred.connect(self._on_player_error)
            try:
                self._player.mediaStatusChanged.connect(self._on_media_status)
            except Exception:
                pass
            return True
        except Exception as exc:
            self._show_error(f'Player init failed: {exc}')
            return False

    def _ensure_video_surface(self):
        if getattr(self, '_video', None) is not None:
            return
        if QVideoWidget is None:
            return
        try:
            self._video = QVideoWidget(self)
            self._video.setStyleSheet(_theme_card_css())
            idx = self.layout().indexOf(self._label)
            self._label.hide()
            self.layout().insertWidget(idx, self._video, 1)
            if self._player is not None:
                self._player.setVideoOutput(self._video)
        except Exception:
            pass

    def _on_player_error(self, _err, msg):
        detail = str(msg or '').strip() or 'unsupported codec or file'
        self._show_error(
            f'Cannot decode with system codecs: {detail}\n'
            'Try OPEN IN BROWSER below, or convert to H.264 MP4.'
        )
        try:
            from core import sfx as _sfx
            _sfx.error()
        except Exception:
            pass

    def _on_media_status(self, status):
        try:
            from PyQt6.QtMultimedia import QMediaPlayer as _MP
            if status == _MP.MediaStatus.InvalidMedia:
                self._on_player_error(None, 'invalid or corrupt media')
            elif status == _MP.MediaStatus.LoadedMedia and self._duration <= 0:
                self._time.setText('LIVE / AUDIO')
        except Exception:
            pass

    def _show_error(self, msg):
        self._label.show()
        self._label.setText(str(msg))
        try:
            self._title.setText('VIDEO PREVIEW · PLAYBACK ISSUE')
        except Exception:
            pass

    def open_in_browser(self):
        try:
            import webbrowser
            p = getattr(self, '_current_path', None)
            if p:
                webbrowser.open(Path(p).as_uri())
        except Exception as exc:
            self._show_error(f'Browser fallback failed: {exc}')

    def load_file(self, path):
        try:
            p = Path(str(path)).expanduser()
        except Exception:
            self._show_error('Invalid file path.')
            return False
        if not p.exists() or not p.is_file():
            self._show_error(f'File not found: {p.name}')
            return False
        if not self._ensure_player():
            return False
        is_audio = p.suffix.lower() in self._AUDIO_EXTS
        try:
            self._current_path = str(p)
            if is_audio:
                try:
                    if getattr(self, '_video', None) is not None:
                        self._video.hide()
                except Exception:
                    pass
                self._label.show()
                self._label.setText(f'♪  {p.name}\nAudio preview — video surface not needed.')
            else:
                self._ensure_video_surface()
                try:
                    if getattr(self, '_video', None) is not None:
                        self._video.show()
                        self._label.hide()
                    else:
                        self._label.show()
                        self._label.setText('Loading…')
                except Exception:
                    pass
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(str(p)))
            self._seek.setValue(0)
            self._seek.setRange(0, 0)
            self._duration = 0
            self._time.setText('00:00 / 00:00')
            self._title.setText(f'{"AUDIO" if is_audio else "VIDEO"} PREVIEW · {p.name.upper()[:60]}')
            self._player.play()
            QTimer.singleShot(9000, self._check_stalled_load)
            return True
        except Exception as exc:
            self._show_error(f'Playback failed: {exc}')
            return False

    def _check_stalled_load(self):
        try:
            if self._player is None:
                return
            from PyQt6.QtMultimedia import QMediaPlayer as _MP
            stalled = self._duration <= 0 and self._player.playbackState() != _MP.PlaybackState.PlayingState
            if stalled and getattr(self, '_current_path', None):
                self._show_error(
                    'Still loading — the system codec pack may not support this file.\n'
                    'Try OPEN IN BROWSER below, or convert to H.264 MP4.'
                )
        except Exception:
            pass

    def _choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Open video',str(Path.home()),'Video (*.mp4 *.avi *.mkv *.mov *.webm *.wmv *.m4v)')
        if path: self.load_file(path)

    def _position_changed(self,pos):
        if not self._seek.isSliderDown(): self._seek.setValue(int(pos))
        self._time.setText(f'{self._fmt(pos)} / {self._fmt(self._duration)}')

    def _duration_changed(self,dur):
        self._duration=max(0,int(dur)); self._seek.setRange(0,self._duration)
        self._time.setText(f'{self._fmt(self._player.position() if self._player else 0)} / {self._fmt(self._duration)}')

    def _on_slider_moved(self,value): self._time.setText(f'{self._fmt(value)} / {self._fmt(self._duration)}')
    def _on_slider_released(self):
        if self._player is not None: self._player.setPosition(self._seek.value())
    def _seek_by(self,delta):
        if self._player is not None: self._player.setPosition(max(0,min(self._duration,self._player.position()+int(delta))))

    def toggle_play(self):
        if self._player is None:
            self._choose_file(); return
        try:
            if self._player.playbackState()==QMediaPlayer.PlaybackState.PlayingState: self._player.pause()
            else: self._player.play()
        except Exception as exc: self._show_error(str(exc))

    def _playback_state_changed(self,state):
        self._play_btn.setText('PAUSE' if state==QMediaPlayer.PlaybackState.PlayingState else 'PLAY')

    def stop(self):
        if self._player is not None: self._player.stop()
        self._seek.setValue(0); self._time.setText(f'00:00 / {self._fmt(self._duration)}')

    def close_player(self): self.stop()


class Model3DView(QFrame):
    def __init__(self,parent=None,transparent=False):
        super().__init__(parent); self.setMouseTracking(True); self.setMinimumSize(280,240)
        self._vertices=self._faces=self._edges=None; self._path=None; self._yaw=35.0; self._pitch=-18.0; self._zoom=1.0
        self._pan_x=self._pan_y=0.0; self._wireframe=False; self._drag_pos=None; self._window_drag_offset=None; self._rotate_mode=False; self._transparent=bool(transparent)
        self._status='3D display ready · double-click to rotate · wheel to zoom'
        self.setStyleSheet('background:transparent;border:none;') if transparent else self.setStyleSheet(f'background:rgba(0,6,10,120);border:1px solid {C.BORDER};border-radius:8px;')
    def load_model(self,path):
        self._path=str(path)
        try:
            import trimesh; loaded=trimesh.load(self._path,force='scene',process=False); meshes=list(loaded.geometry.values()) if hasattr(loaded,'geometry') else [loaded]; verts=[]; faces=[]; off=0
            for m in meshes:
                if m is None or not hasattr(m,'vertices') or not hasattr(m,'faces'): continue
                v=np.asarray(m.vertices,dtype=np.float32); f=np.asarray(m.faces,dtype=np.int32)
                if len(v) and len(f): verts.append(v); faces.append(f+off); off+=len(v)
            if not verts: raise ValueError('No renderable mesh geometry found')
            self._vertices=np.vstack(verts); self._faces=np.vstack(faces); center=(self._vertices.min(0)+self._vertices.max(0))*0.5; self._vertices-=center; radius=float(np.max(np.linalg.norm(self._vertices,axis=1))) or 1.0; self._vertices/=radius; self.reset_view(); self._status=f'{Path(path).name} · drag to move · double-click rotate · wheel zoom · right-click close'
        except Exception as e:
            self._vertices=self._faces=self._edges=None; self._status=f'3D load failed: {e}'
        self.update()
    def reset_view(self): self._yaw,self._pitch,self._zoom=35.0,-18.0,1.0; self._pan_x=self._pan_y=0.0; self.update()
    def toggle_wireframe(self): self._wireframe=not self._wireframe; self.update()
    def wheelEvent(self,e): self._zoom=max(0.22,min(5.0,self._zoom*(1.13**(e.angleDelta().y()/120.0)))); self.update(); e.accept()
    def mouseDoubleClickEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton: self._rotate_mode=not self._rotate_mode; self.setCursor(Qt.CursorShape.OpenHandCursor if self._rotate_mode else Qt.CursorShape.SizeAllCursor); e.accept(); return
        super().mouseDoubleClickEvent(e)
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.RightButton: self.window().close(); e.accept(); return
        if e.button()==Qt.MouseButton.LeftButton:
            self._drag_pos=e.position(); self._window_drag_offset=(e.globalPosition().toPoint()-self.window().frameGeometry().topLeft()) if e.modifiers() & Qt.KeyboardModifier.AltModifier else None; e.accept(); return
        super().mousePressEvent(e)
    def mouseMoveEvent(self,e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._window_drag_offset is not None: self.window().move(e.globalPosition().toPoint()-self._window_drag_offset)
            else:
                d=e.position()-self._drag_pos; base=max(1.0,min(self.width(),self.height()))
                if self._rotate_mode:
                    self._yaw+=d.x()*0.65; self._pitch=max(-89.0,min(89.0,self._pitch+d.y()*0.65))
                else:
                    self._pan_x+=d.x()/base*2.0; self._pan_y+=d.y()/base*2.0
                self._drag_pos=e.position(); self.update()
            e.accept(); return
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton: self._drag_pos=None; self._window_drag_offset=None; e.accept(); return
        super().mouseReleaseEvent(e)
    def paintEvent(self,_):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._transparent: p.fillRect(self.rect(),qcol(C.BG,105))
        W,H=self.width(),self.height()
        if self._vertices is None or self._faces is None:
            if not self._transparent: p.setPen(qcol(C.TEXT_DIM)); p.setFont(QFont('Exo 2',9)); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,self._status)
            p.end(); return
        yaw,pitch=math.radians(self._yaw),math.radians(self._pitch); cy,sy=math.cos(yaw),math.sin(yaw); cx,sx=math.cos(pitch),math.sin(pitch); v=self._vertices; x=v[:,0]*cy-v[:,2]*sy; z=v[:,0]*sy+v[:,2]*cy; y=v[:,1]*cx-z*sx; z2=v[:,1]*sx+z*cx; scale=min(W,H)*0.40*self._zoom; sxp=W*0.5+self._pan_x*scale+x*scale; syp=H*0.47+self._pan_y*scale-y*scale
        tris=[]
        for face in self._faces:
            if len(face)>=3:
                a,b,c=face[:3]; tris.append((float((z2[a]+z2[b]+z2[c])/3.0),a,b,c))
        tris.sort(reverse=True)
        for depth,a,b,c in tris:
            light=max(0.15,min(1.0,0.58+0.32*(depth+1.0)/2.0)); col=qcol(C.PRI,int(115+125*light)); path=QPainterPath(); path.moveTo(float(sxp[a]),float(syp[a])); path.lineTo(float(sxp[b]),float(syp[b])); path.lineTo(float(sxp[c]),float(syp[c])); path.closeSubpath(); p.setBrush(Qt.BrushStyle.NoBrush if self._wireframe else QBrush(col)); p.setPen(QPen(col,1.0) if self._wireframe else QPen(qcol(C.PRI,90),0.5)); p.drawPath(path)
        if not self._transparent: p.setPen(qcol(C.TEXT_DIM,190)); p.setFont(QFont('Exo 2',7)); p.drawText(8,H-9,self._status[:110])
        p.end()


class _3DDisplayWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(680,680)
        self.viewer=Model3DView(self, transparent=True)
        self._drag_origin = None
        self.viewer.installEventFilter(self)
        lay=QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.addWidget(self.viewer)
    def load_model(self,path): self.viewer.load_model(path)
    def eventFilter(self, obj, event):
        if obj is self.viewer:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self.grabMouse()
                return False
            if event.type() == QEvent.Type.MouseMove and self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_origin)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = None
                self.releaseMouse()
                try:
                    if getattr(self._three_d_display, '_jarvis_floating_panel', False):
                        self._remember_panel_position(self._three_d_display)
                except Exception:
                    pass
        return super().eventFilter(obj, event)


class _ArcLogoButton(_ArcLogo):
    clicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_start = None
        self._moved = False

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        win = self.window()
        if not getattr(win, '_drag_reactor_enabled', True):
            super().mouseMoveEvent(e); return
        if self._drag_start is not None and e.buttons() & Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._moved = True
                win = self.window()
                if win:
                    win.move(win.pos() + delta)
                    if hasattr(win, '_save_window_position'):
                        win._save_window_position()
                self._drag_start = e.globalPosition().toPoint()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._moved:
            self.clicked.emit()
        self._drag_start = None
        super().mouseReleaseEvent(e)


class _MiniWaveform(QWidget):
    """Microphone soundwave driven by actual mic volume levels.

    The waveform reacts to real microphone input: quiet speech produces
    small movements, loud speech produces larger waveform movement, and
    when there is no voice input the bars stay nearly idle. Updates are
    smoothed to avoid flickering.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self._amp = 0.0          # smoothed display amplitude
        self._live = 0.0         # instantaneous mic level (0.0 - 1.0)
        self._peak_hold = 0.0    # peak detector for visual punch
        self._tick = 0
        self._idle_phase = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(33)

    def set_audio_level(self, level: float) -> None:
        """Feed the actual microphone volume into the waveform.

        Called from the GUI thread (via the host's _audio_level_sig).
        """
        try:
            lv = max(0.0, min(1.0, float(level)))
        except Exception:
            return
        # Track the instantaneous level so the waveform can react in real time.
        if lv > self._live:
            self._live = lv
        if lv > self._peak_hold:
            self._peak_hold = lv

    def _step(self):
        self._tick += 1
        # Decay the live level so the waveform reacts to new input, not stale data.
        self._live *= 0.82
        # Smooth the display amplitude toward the live level to prevent flickering.
        self._amp += (self._live - self._amp) * 0.40
        # Decay the peak hold so loud speech produces a brief visual punch.
        self._peak_hold *= 0.90
        self._idle_phase += 0.09
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        N, bw = 48, 7
        wx0 = (W - N * bw) / 2
        mid = (N - 1) / 2.0

        # Determine whether we're effectively idle (no voice input).
        is_idle = self._amp < 0.03

        for i in range(N):
            # Envelope: bars near the centre are taller.
            env = (1.0 - abs(i - mid) / mid) ** 0.7

            if is_idle:
                # Calm, nearly idle state when there is no voice input.
                idle = 2.0 + 1.2 * math.sin(self._idle_phase + i * 0.6)
                hgt = int(max(1.5, min(H * 0.35, idle)))
                cl = qcol(C.BORDER_B)
            else:
                # Real-time response: scale bar height by actual mic volume.
                # Add a small shimmer so the waveform feels alive.
                shimmer = 0.60 + 0.40 * math.sin(self._tick * 0.18 + i * 0.7)
                # Combine smoothed amp with peak hold for a more dynamic visual.
                drive = (self._amp * 0.75 + self._peak_hold * 0.25)
                hgt = int(max(2, min(H - 6,
                           3.0 + drive * (H - 8) * env * shimmer)))
                # Colour shifts toward bright cyan when loud, dimmer when quiet.
                if hgt > H * 0.45:
                    cl = qcol(C.PRI)
                elif drive > 0.04:
                    cl = qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, (H - hgt) / 2, bw - 1.5, hgt), cl)
        p.end()


class _CommandWindow(QFrame):
    closed = pyqtSignal()

    def __init__(self, host):
        super().__init__(None)
        self._host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setProperty('_jarvis_floating_panel', True)
        self.setProperty('_jarvis_panel_kind', 'command')
        self.setProperty('_jarvis_panel_key', 'command_window')
        self.setMinimumSize(420, 525)
        self.setMaximumSize(520, 650)
        self.resize(470, 600)
        self._drag_pos = None
        self._build()

    def run_on_ui(self, fn) -> None:
        """Run ``fn`` on the GUI thread — the host owns the marshalling.

        Background helpers (face detection, process sampling) call this; without
        it the callback died with an AttributeError inside its worker thread and
        the result was silently lost.
        """
        host = getattr(self, '_host', None)
        runner = getattr(host, 'run_on_ui', None)
        if callable(runner):
            runner(fn)
            return
        try:
            fn()
        except Exception:
            pass

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(7)
        try:
            backdrop = FuturisticBackdrop(self)
            backdrop.lower()
            self._futuristic_backdrop = backdrop
        except Exception:
            pass

        hdr = QFrame(self)
        hdr.setObjectName('CmdHeader')
        hdr.setFixedHeight(40)
        hdr.setStyleSheet(
            f'QFrame#CmdHeader {{ background: transparent; '
            f'border: none; border-radius: 3px; }}'
        )
        hb = QHBoxLayout(hdr)
        hb.setContentsMargins(10, 4, 6, 4)
        hb.setSpacing(6)
        hb.addStretch(1)
        brand = QLabel('J.A.R.V.I.S.')
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet(f'color:{C.PRI};font:800 11pt "Exo 2";background:transparent;letter-spacing:4px;')
        hb.addWidget(brand)
        self._online = QLabel('●  ONLINE')
        self._online.setStyleSheet(f'color:{C.GREEN};font:700 7pt "Exo 2";background:transparent;border:none;padding:0px;')
        hb.addWidget(self._online)
        hb.addStretch(1)
        close_btn = QPushButton('×')
        close_btn.setToolTip('Close J.A.R.V.I.S. completely')
        close_btn.setFixedSize(26, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f'QPushButton {{ background:transparent;color:{C.TEXT_MED};border:none;font-size:14px; }}'
            f'QPushButton:hover {{ color:{C.RED};background:rgba(60,8,16,160); }}'
        )
        close_btn.clicked.connect(self._close_jarvis)
        hb.addWidget(close_btn)
        lay.addWidget(hdr)
        hdr.mousePressEvent = self._hdr_press
        hdr.mouseMoveEvent = self._hdr_move
        hdr.mouseReleaseEvent = self._hdr_release

        tabs = QHBoxLayout()
        tabs.setSpacing(3)
        self._tab_btns: list[QPushButton] = []
        for i, label in enumerate(('CHAT', 'ACTIVITY', 'TOOLS', 'WEB', 'MEMORY', 'SYSTEM', 'SETTINGS')):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(34)
            b.setStyleSheet(
                f'QPushButton {{ color:{C.TEXT_DIM};background:transparent;'
                f'border:none;border-radius:2px;font:700 7pt "Exo 2";padding:4px 2px; }}'
                f'QPushButton:hover {{ color:{C.WHITE};border-color:{C.PRI}; }}'
                f'QPushButton:checked {{ color:{C.WHITE};background:{C.PRI_GHO};border-color:{C.PRI}; }}'
            )
            b.clicked.connect(lambda _=False, idx=i: self._on_tab(idx))
            tabs.addWidget(b, 1)
            self._tab_btns.append(b)
        cam_b = QPushButton('WEBCAM')
        cam_b.setCheckable(False)
        cam_b.setCursor(Qt.CursorShape.PointingHandCursor)
        cam_b.setMinimumHeight(34)
        cam_b.setStyleSheet(
            f'QPushButton {{ color:{C.PRI};background:{C.PRI_GHO};'
            f'border:1px solid {C.PRI};border-radius:2px;font:700 7pt "Exo 2";padding:4px 6px; }}'
            f'QPushButton:hover {{ color:{C.WHITE};border-color:{C.PRI}; }}'
        )
        cam_b.clicked.connect(lambda _=False: self._host.start_camera_stream())
        tabs.addWidget(cam_b, stretch=0)
        self._webcam_btn = cam_b
        self._tab_btns[0].setChecked(True)
        lay.addLayout(tabs)

        core = QFrame(self)
        core.setStyleSheet('background:transparent;border:none;')
        cl = QVBoxLayout(core)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)
        self.globe = _ArcLogo(self, size=200)
        self.globe.setMinimumHeight(190)
        self.globe.setMaximumHeight(230)
        cl.addWidget(self.globe, 0, Qt.AlignmentFlag.AlignCenter)
        self.wave = _MiniWaveform(self)
        cl.addWidget(self.wave)
        lay.addWidget(core)

        self.chat = QTextEdit(self)
        self.chat.setReadOnly(True)
        self.chat.setAutoFillBackground(False)
        self.chat.setMinimumHeight(120)
        self.chat.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat.setStyleSheet(
            f'QTextEdit {{ background:rgba(0,12,20,105);color:{C.TEXT};'
            f'border:1px solid {C.BORDER_B};border-radius:3px;padding:7px;'
            f'font:8pt "Space Grotesk"; }}'
            f'QScrollBar:vertical {{ background:transparent;width:0px;border:none; }}'
            f'QScrollBar::handle:vertical {{ background:transparent;border:none; }}'
            f'QScrollBar:horizontal {{ background:transparent;height:0px;border:none; }}'
            f'QScrollBar::handle:horizontal {{ background:transparent;border:none; }}'
        )
        self.chat.setHtml(
            f'<p><b style="color:{C.WHITE}">You</b> '
            f'<span style="color:{C.TEXT_DIM}">11:32 AM</span><br/>What\'s the status of my system?</p>'
            f'<p><b style="color:{C.PRI}">J.A.R.V.I.S.</b> '
            f'<span style="color:{C.TEXT_DIM}">11:32 AM</span><br/>Everything appears to be in order, sir.</p>'
        )
        lay.addWidget(self.chat, 1)
        self.chips = QLabel('CPU 24%  |  RAM 41%  |  GPU 18%  |  TEMP 42°C')
        self.chips.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chips.setStyleSheet(
            f'color:{C.PRI};font:700 8pt "Exo 2";background:rgba(0,24,36,140);'
            f'border:1px solid {C.BORDER_B};border-radius:8px;padding:5px;'
        )
        lay.addWidget(self.chips)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText('Type a message or speak...')
        self.input.setFixedHeight(36)
        self.input.setStyleSheet(
            f'QLineEdit {{ background:rgba(0,12,20,130);color:{C.WHITE};'
            f'border:1px solid {C.BORDER_B};border-radius:14px;padding:4px 12px; }}'
            f'QLineEdit:focus {{ border-color:{C.PRI}; }}'
        )
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        self.mic_btn = QPushButton('🎙')
        self.mic_btn.setToolTip('Mute / unmute (F4)')
        self.mic_btn.setFixedSize(36, 36)
        self.mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_btn.setStyleSheet(
            f'QPushButton {{ background:rgba(0,30,50,160);color:{C.PRI};'
            f'border:1px solid {C.PRI};border-radius:18px;font-size:15px; }}'
            f'QPushButton:hover {{ background:{C.PRI_GHO}; }}'
        )
        self.mic_btn.clicked.connect(self._toggle_mute)
        row.addWidget(self.mic_btn)
        send = QPushButton('➤')
        send.setFixedSize(36, 36)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(
            f'QPushButton {{ background:rgba(0,30,50,160);color:{C.PRI};'
            f'border:1px solid {C.BORDER_B};border-radius:18px;font-size:14px; }}'
            f'QPushButton:hover {{ border-color:{C.PRI};background:{C.PRI_GHO}; }}'
        )
        send.clicked.connect(self._send)
        row.addWidget(send)
        lay.addLayout(row)

    def _hdr_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def _hdr_move(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def _hdr_release(self, e):
        self._drag_pos = None
        try:
            self._host._remember_panel_position(self)
        except Exception:
            pass

    def hideEvent(self, e):
        try:
            self.closed.emit()
        except Exception:
            pass
        super().hideEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(1, 1, -1, -1)
        cut = 14
        path = QPainterPath()
        path.moveTo(r.left() + cut, r.top())
        path.lineTo(r.right() - cut, r.top())
        path.lineTo(r.right(), r.top() + cut)
        path.lineTo(r.right(), r.bottom() - cut)
        path.lineTo(r.right() - cut, r.bottom())
        path.lineTo(r.left() + cut, r.bottom())
        path.lineTo(r.left(), r.bottom() - cut)
        path.lineTo(r.left(), r.top() + cut)
        path.closeSubpath()
        p.fillPath(path, QBrush(QColor(1, 13, 20, 255)))   # #010d14 = C.PANEL
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.BORDER_B), 1.2))
        p.drawPath(path)
        p.end()
        super().paintEvent(e)

    def _close_jarvis(self):
        try:
            if _sfx_enabled(getattr(self, '_host', None)):
                _sfx('close')
            self._host._close_all_ui()
        except Exception:
            try:
                self.hide()
            except Exception:
                pass

    def _toggle_mute(self):
        try:
            self._host._toggle_mute()
        except Exception:
            pass

    def _on_tab(self, idx: int):
        if _sfx_enabled(getattr(self, '_host', None)):
            _sfx('click')
        for i, b in enumerate(self._tab_btns):
            b.setChecked(i == idx)
        try:
            h = self._host
            if idx == 1:
                h._open_activity_panel()
            elif idx == 2:
                h._open_web_task_panel()
            elif idx == 3:
                h._open_webview_panel()
            elif idx == 4:
                h._open_memory_panel()
            elif idx == 5:
                h._open_world_monitor()
            elif idx == 6:
                h._open_full_settings()
            else:
                self.input.setFocus()
        except Exception:
            pass

    def _show_chat_tab(self) -> None:
        """Reopen on the CHAT log (circle-logo contract: the chat is always
        enabled and front-most when the command window is opened)."""
        try:
            for i, b in enumerate(self._tab_btns):
                b.setChecked(i == 0)
            self.input.setFocus()
        except Exception:
            pass

    def _send(self):
        txt = self.input.text().strip()
        if not txt:
            return
        if _sfx_enabled(getattr(self, '_host', None)):
            _sfx('send')
        self.input.clear()
        self.append_msg('You', txt, you=True)
        try:
            self._host._send_backend_command(txt, log=False)
            self._host._activity_add(f'YOU: {txt}')
        except Exception:
            pass
        self.input.setFocus()

    def append_msg(self, who: str, text: str, you: bool = False):
        safe = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        col = C.WHITE if you else C.PRI
        try:
            self._stop_typing_dots()
            self._clear_trailing_dots()
            if you:
                self.chat.append(f'<b style="color:{col}">{who}</b><br/>{safe}')
                sb = self.chat.verticalScrollBar()
                sb.setValue(sb.maximum())
                return
            if self._tw_enabled():
                self._tw_who = str(who)
                self._tw_col = col
                self._tw_text = safe
                self._tw_i = 0
                self._start_tw_timer()
            else:
                self.chat.append(f'<b style="color:{col}">{who}</b><br/>{safe}')
                sb = self.chat.verticalScrollBar()
                sb.setValue(sb.maximum())
        except Exception:
            pass

    def _tw_enabled(self) -> bool:
        try:
            cfg = _read_full_config()
            feats = cfg.get('features', {}) if isinstance(cfg.get('features', {}), dict) else {}
            return bool(feats.get('typewriter_effect', True))
        except Exception:
            return True

    def _start_tw_timer(self):
        try:
            self._stop_tw_timer()
            cur = self.chat.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertHtml(f'<b style="color:{self._tw_col}">{self._tw_who}</b><br/>')
            self._tw_cur = cur
            self._tw_timer = QTimer(self)
            self._tw_timer.timeout.connect(self._tw_tick)
            self._tw_timer.start(12)
            self._tw_tick()
        except Exception:
            self._tw_timer = None
            self.chat.append(f'<b style="color:{self._tw_col}">{self._tw_who}</b><br/>{self._tw_text}')
            self._autoscroll_chat()

    def _tw_tick(self):
        try:
            if getattr(self, '_tw_timer', None) is None:
                return
            nxt = self._tw_i + 2
            part = self._tw_text[self._tw_i:nxt]
            self._tw_i = nxt
            if part:
                self._tw_cur.insertText(part)
                self._autoscroll_chat()
            if self._tw_i >= len(self._tw_text):
                self._stop_tw_timer()
                self._autoscroll_chat()
        except Exception:
            self._stop_tw_timer()

    def _stop_tw_timer(self):
        try:
            t = getattr(self, '_tw_timer', None)
            if t is not None:
                t.stop()
                t.deleteLater()
            self._tw_timer = None
        except Exception:
            self._tw_timer = None

    def _autoscroll_chat(self):
        try:
            sb = self.chat.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass

    def begin_assistant_stream(self, who: str = 'J.A.R.V.I.S.') -> None:
        try:
            self._stop_tw_timer()
            self._stop_typing_dots()
            self._stream_who = str(who or 'J.A.R.V.I.S.')
            self._stream_col = C.PRI
            cur = self.chat.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertHtml(f'<b style="color:{self._stream_col}">{self._stream_who}</b><br/>')
            self._stream_cur = cur
            self._stream_active = True
            self._stream_has_text = False
            self._autoscroll_chat()
        except Exception:
            self._stream_active = False

    def append_assistant_stream(self, chunk: str) -> None:
        if chunk is None:
            return
        text = str(chunk)
        if not text:
            return
        try:
            if not getattr(self, '_stream_active', False):
                self.begin_assistant_stream()
            cur = getattr(self, '_stream_cur', None)
            if cur is None:
                return
            cur.insertText(text)
            self._stream_cur = cur
            self._stream_has_text = True
            self._autoscroll_chat()
        except Exception:
            pass

    def finish_assistant_stream(self) -> None:
        try:
            if not getattr(self, '_stream_active', False):
                return
            cur = getattr(self, '_stream_cur', None)
            if cur is not None:
                cur.movePosition(QTextCursor.MoveOperation.End)
                cur.insertText('\n')
                self._stream_cur = cur
            self._stream_active = False
            self._stream_has_text = False
            self._autoscroll_chat()
        except Exception:
            self._stream_active = False

    def _show_typing_dots(self):
        self._stop_typing_dots()

    def _dots_tick(self):
        try:
            if getattr(self, '_dots_timer', None) is None:
                return
            c = QTextCursor(self.chat.document().lastBlock())
            c.select(QTextCursor.SelectionType.BlockUnderCursor)
            c.removeSelectedText()
            c.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.MoveAnchor)
            c.insertHtml(f'<b style="color:{C.PRI}">J.A.R.V.I.S.</b>  '.replace(f'{C.PRI}', str(C.PRI)))
            c.insertText('· · ·' if self._dots_n < 2 else '· ·')
            self._dots_n = (self._dots_n + 1) % 3
            self._autoscroll_chat()
        except Exception:
            pass

    def _clear_trailing_dots(self):
        try:
            if not getattr(self, '_dots_active', False):
                return
            doc = self.chat.document()
            blk = doc.lastBlock()
            c = QTextCursor(blk)
            c.select(QTextCursor.SelectionType.BlockUnderCursor)
            c.removeSelectedText()
            c.insertBlock()
            c.deletePreviousChar()
            self._dots_active = False
        except Exception:
            pass

    def _stop_typing_dots(self):
        try:
            d = getattr(self, '_dots_timer', None)
            if d is not None:
                d.stop()
                d.deleteLater()
            self._dots_timer = None
        except Exception:
            self._dots_timer = None

    def set_audio_level(self, level: float):
        try:
            self.wave.set_audio_level(level)
        except Exception:
            pass
        try:
            self.globe.set_audio_level(level)
        except Exception:
            pass

    def set_status(self, state: str):
        s = str(state or '').upper()
        try:
            online = getattr(self, '_online', None)
            if online is not None:
                if s == 'MUTED':
                    online.setText('○  MUTED')
                    online.setStyleSheet(f'color:{C.MUTED_C};font:700 7pt "Exo 2";background:transparent;padding:3px 4px;{_theme_icon_font_css()}')
                else:
                    online.setText('●  ONLINE')
                    online.setStyleSheet(f'color:{C.GREEN};font:700 7pt "Exo 2";background:transparent;padding:3px 4px;{_theme_icon_font_css()}')
        except Exception:
            pass
        try:
            globe = getattr(self, 'globe', None)
            if globe is not None and hasattr(globe, 'set_state'):
                globe.set_state(s)
        except Exception:
            pass

    def set_metrics_text(self, text: str):
        try:
            self.chips.setText(text)
        except Exception:
            pass


DISPLAY_FONT = "Segoe UI"

FONT_UI = "Rajdhani"
FONT_HEAD = "Orbitron"
FONT_LOG = "Share Tech Mono"
FONT_NUM = "Orbitron"
FONT_SMALL = "Exo 2"
FONT_CHAT = "Archivo Black"   # chat log, tabs, status
FONT_HUD = "Space Grotesk"     # futuristic clean font for chat panel + nav tabs


def _register_redesign_font() -> str:
    try:
        font_path = Path(__file__).resolve().parent / "assets" / "Nasalization Rg.otf"
        if font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families:
                return families[0]
    except Exception:
        pass
    return "Segoe UI"


def _register_app_fonts() -> None:
    try:
        base = Path(__file__).resolve().parent / "assets" / "fonts"
        for filename in ("Rajdhani-Bold.ttf", "Orbitron-Bold.ttf",
                         "ShareTechMono-Regular.ttf", "Exo2-Bold.ttf",
                         "ArchivoBlack-Regular.ttf", "Futura Bold.ttf"):
            try:
                path = base / filename
                if not path.exists() and filename == "ArchivoBlack-Regular.ttf":
                    path = Path(__file__).resolve().parent / "assets" / filename
                if path.exists():
                    QFontDatabase.addApplicationFont(str(path))
            except Exception:
                pass
    except Exception:
        pass


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


class _StartupLogoHitArea(QWidget):
    """Invisible startup-only click surface surrounding the reactor logo.

    This widget paints nothing and does not touch ``LOGO_BG_FILL``. It exists only
    as an extra mouse target during the startup presentation, so clicking the
    transparent area around the logo can still open the command panel.
    """
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet('QWidget { background: transparent; border: none; }')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self._press_pos = None

    def paintEvent(self, event) -> None:
        # Deliberately paint nothing: this is a pure invisible hit surface.
        return

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # A click anywhere on the startup reactor must settle the sequence,
            # not dead-end it — the hitbox covers the transparent area around
            # the logo where most first clicks land.
            try:
                parent = self.parentWidget()
                settle = getattr(parent, '_startup_click_settle', None)
                if settle is not None:
                    settle()
            except Exception:
                pass
            self._press_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            start = self._press_pos
            self._press_pos = None
            if start is None or (event.globalPosition().toPoint() - start).manhattanLength() < QApplication.startDragDistance():
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        # Keep the transparent surface passive for keyboard focus.
        event.ignore()


class RefinedArcLogo(QFrame):
    LOGO_RADIUS = LOGO_RING_RADIUS
    TEXT_SIZE = LOGO_TEXT_SIZE
    GLOW_STRENGTH = LOGO_GLOW
    PARTICLE_COUNT = LOGO_PARTICLES
    PARTICLE_BRIGHTNESS = LOGO_PARTICLE_GLOW
    ANIMATION_SPEED = LOGO_SPEED
    ACTIVATION_GROWTH = LOGO_CLICK_SWELL
    ACTIVATION_DURATION = LOGO_CLICK_TIME

    def __init__(self, parent=None, size: int = 220):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self._animation_speed = 1.0
        self._stroke_opacity = 1.0
        self._live_amp = 0.0
        self._amp_display = 0.0
        self._activation_at = -10.0
        self._STATE_TARGETS = {
            'IDLE':      {'energy': 0.55, 'glow': 0.50, 'tint': (36, 174, 239),  'spread': 1.00, 'sweep': 1.0, 'dim': 1.00},
            'LISTENING': {'energy': 1.40, 'glow': 0.90, 'tint': (120, 226, 255), 'spread': 1.06, 'sweep': 1.8, 'dim': 1.00},
            'THINKING':  {'energy': 2.20, 'glow': 0.80, 'tint': (150, 210, 255), 'spread': 1.00, 'sweep': 3.0, 'dim': 1.00},
            'EXECUTING': {'energy': 2.60, 'glow': 1.20, 'tint': (255, 200, 120), 'spread': 1.10, 'sweep': 3.4, 'dim': 1.00},
            'SPEAKING':  {'energy': 1.20, 'glow': 1.00, 'tint': (140, 230, 255), 'spread': 1.00, 'sweep': 1.4, 'dim': 1.00},
            'ERROR':     {'energy': 0.80, 'glow': 0.90, 'tint': (255, 90, 110),  'spread': 1.00, 'sweep': 0.8, 'dim': 1.00},
            'SLEEPING':  {'energy': 0.30, 'glow': 0.25, 'tint': (60, 120, 150),  'spread': 0.97, 'sweep': 0.5, 'dim': 0.50},
        }
        self._STATE_ALIASES = {'PROCESSING': 'THINKING', 'THINK': 'THINKING', 'MUTED': 'SLEEPING',
                               'SLEEP': 'SLEEPING', 'TALKING': 'SPEAKING', 'LISTEN': 'LISTENING',
                               'STARTING': 'THINKING', 'INITIALIZING': 'THINKING',
                               'CONNECTING': 'THINKING', 'RECONNECTING': 'THINKING',
                               'READY': 'LISTENING',
                               # Piper pipeline states map onto the existing visual
                               # vocabulary, so the HUD design needs no change.
                               'GEMINI LIVE': 'LISTENING', 'MIC LISTENING': 'LISTENING',
                               'PIPER READY': 'LISTENING',
                               'PIPER SYNTHESIZING': 'THINKING', 'PIPER PLAYING': 'SPEAKING'}
        self._state = 'IDLE'
        self._blend = dict(self._STATE_TARGETS['IDLE'], tint=list(self._STATE_TARGETS['IDLE']['tint']))
        self._target = dict(self._STATE_TARGETS['IDLE'])
        self._clock = QElapsedTimer()
        self._clock.start()
        rng = random.Random(17)
        self._particles = [
            (rng.uniform(0.0, math.tau),
             rng.uniform(126.0, 171.0),
             rng.uniform(-0.05, 0.05),
             rng.uniform(2.0, 7.0),
             rng.uniform(0.0, math.tau),
             rng.choice((1.8, 2.2, 2.7)))
            for _ in range(self.PARTICLE_COUNT)
        ]
        # Paint-time opacity — no QGraphicsOpacityEffect, so the widget's
        # full rect stays a valid mouse hit-target on every platform.
        self._graphic_opacity = 1.0
        # Startup bloom amount — 1.0 = full bloom, 0.0 = normal glow.
        # Animated only during the launch transition.
        self._startup_glow = 0.0
        # Startup core alpha multiplier. This gives LOGO_BG_FILL a smooth fade
        # instead of appearing as a hard black disc.
        self._startup_core_fade = 0.0
        # LOGO_BG_FILL is only active during the startup presentation.
        self._startup_bg_fill_active = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._frame_counter = 0
        self._timer.start(16)

    def seconds(self) -> float:
        return self._clock.elapsed() / 1000.0

    def set_logo_size(self, size: int) -> None:
        size = max(120, int(size))
        self.setFixedSize(size, size)
        self._visual_logo_size = size
        self.update()

    def set_visual_logo_size(self, size: int) -> None:
        """Change only the painted diameter; keep widget/layout geometry stable."""
        try:
            self._visual_logo_size = max(120, int(size))
        except Exception:
            self._visual_logo_size = max(120, min(self.width(), self.height()))
        self.update()

    def set_graphic_opacity(self, percent: int) -> None:
        self._graphic_opacity = max(0.0, min(1.0, int(percent) / 100.0))
        self.update()

    def set_stroke_opacity(self, percent: int) -> None:
        self._stroke_opacity = max(0.0, min(1.0, int(percent) / 100.0))
        self.update()

    @property
    def graphic_opacity(self) -> int:
        return int(round(self._graphic_opacity * 100))

    @property
    def stroke_opacity(self) -> int:
        return int(round(self._stroke_opacity * 100))

    def set_audio_level(self, level: float) -> None:
        try:
            level = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        self._live_amp = max(self._live_amp, level)

    def trigger_activation(self) -> None:
        self._activation_at = self._clock.elapsed() / 1000.0
        self.update()

    def set_state(self, state) -> None:
        try:
            s = str(state or 'IDLE').upper().strip()
        except Exception:
            s = 'IDLE'
        s = self._STATE_ALIASES.get(s, s)
        if s not in self._STATE_TARGETS:
            s = 'IDLE'
        self._state = s
        self._target = dict(self._STATE_TARGETS[s])

    @property
    def state(self) -> str:
        return getattr(self, '_state', 'IDLE')

    def _tick(self) -> None:
        self._frame_counter += 1
        try:
            self._live_amp *= 0.93
            self._amp_display += (self._live_amp - self._amp_display) * 0.14
        except Exception:
            self._live_amp = 0.0
        try:
            k = 0.10
            tgt = self._target
            bld = self._blend
            for key in ('energy', 'glow', 'spread', 'sweep', 'dim'):
                bld[key] += (float(tgt[key]) - float(bld[key])) * k
            tr, tg, tb = tgt['tint']
            cr, cg, cb = bld['tint']
            bld['tint'] = [cr + (tr - cr) * k, cg + (tg - cg) * k, cb + (tb - cb) * k]
        except Exception:
            pass
        self.update()

    @staticmethod
    def _pen(color: QColor, width: float) -> QPen:
        pen = QPen(color)
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return pen

    def _tint(self, mult=1.0, alpha=255):
        try:
            r, g, b = self._blend['tint']
        except Exception:
            r, g, b = (36, 174, 239)
        r = max(0, min(255, int(r * mult)))
        g = max(0, min(255, int(g * mult)))
        b = max(0, min(255, int(b * mult)))
        return QColor(r, g, b, max(0, min(255, int(alpha))))

    @staticmethod
    def _startup(t: float):
        return (
            _ease((t - 0.06) / 0.28),
            _ease((t - 0.46) / 0.42),
            _ease((t - 0.76) / 0.30),
            _ease((t - 0.96) / 0.28),
        )

    def _draw_particles(self, p: QPainter, t: float, startup: float, activation: float,
                        spread: float, stroke_alpha: float) -> None:
        for angle0, radius0, speed, drift, phase, size in self._particles:
            radius = (radius0 + math.sin(t * 0.65 + phase) * drift) * spread + activation * 11
            angle = angle0 + speed * t
            x, y = radius * math.cos(angle), radius * math.sin(angle)
            alpha = int((70 + 78 * (0.5 + 0.5 * math.sin(t * 0.85 + phase)))
                        * startup * self.PARTICLE_BRIGHTNESS)
            if alpha <= 0:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(113, 205, 250, int(alpha * 0.18 * stroke_alpha)))
            p.drawEllipse(QPointF(x, y), size * 2.6, size * 2.6)
            p.setBrush(QColor(232, 248, 255, int(alpha * stroke_alpha)))
            p.drawEllipse(QPointF(x, y), size, size)

    def _draw_dotted_orbit(self, p: QPainter, t: float, radius: float, count: int,
                           speed: float, dot: float, color: QColor, opacity: float,
                           twinkle: bool = True) -> None:
        if count <= 0 or opacity <= 0.004:
            return
        base = t * speed
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(count):
            ang = math.radians(base + i * (360.0 / count))
            a = opacity
            if twinkle:
                a *= 0.72 + 0.28 * (0.5 + 0.5 * math.sin(t * 0.9 + i * 2.39996))
            ai = int(a)
            if ai <= 0:
                continue
            c = QColor(color)
            c.setAlpha(min(255, ai))
            p.setBrush(c)
            d = dot * LOGO_DOT_SIZE * (0.85 + 0.3 * ((i * 37 % 10) / 10.0))
            p.drawEllipse(QPointF(math.cos(ang) * radius, math.sin(ang) * radius), d * 1.9, d * 1.9)
            cw = QColor(235, 250, 255, min(255, ai))
            p.setBrush(cw)
            p.drawEllipse(QPointF(math.cos(ang) * radius, math.sin(ang) * radius), d, d)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_dotted_arc(self, p: QPainter, radius: float, start_deg: float,
                           span_deg: float, step_deg: float, dot: float,
                           color: QColor) -> None:
        try:
            want = int(LOGO_DOT_COUNT)
        except Exception:
            want = 0
        if want > 0:
            n = max(2, int(round(want * span_deg / 67.0)))
            step_deg = span_deg / (n - 1)
        else:
            step_deg = max(1.0, step_deg * LOGO_DOT_GAP)
        if color.alpha() <= 0 or span_deg <= 0 or step_deg <= 0:
            return
        dot = max(0.4, dot * LOGO_DOT_SIZE)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        a = start_deg
        end = start_deg + span_deg
        while a <= end + 0.001:
            r = math.radians(a)
            p.drawEllipse(QPointF(math.cos(r) * radius, math.sin(r) * radius), dot, dot)
            a += step_deg
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_secondary_rings(self, p: QPainter, t: float, opacity: float, sweep: float) -> None:
        for radius, start, span, speed, alpha in (
            (119, 18, 92, 3.0, 72),
            (124, 168, 72, -2.2, 55),
            (129, 252, 52, 1.45, 42),
            (115, 108, 38, -1.8, 46),
        ):
            rect = QRectF(-radius, -radius, radius * 2, radius * 2)
            p.setPen(self._pen(QColor(92, 191, 241, int(alpha * opacity)), 0.85))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rect, int((start + t * speed * sweep) * 16), int(span * 16))

        radius = 136
        p.setPen(self._pen(QColor(130, 210, 249, int(25 * opacity)), 0.65))
        p.drawArc(QRectF(-radius, -radius, radius * 2, radius * 2), int((215 - t * 1.1 * sweep) * 16), 74 * 16)

        radius = 129
        p.setPen(self._pen(QColor(206, 240, 255, int(54 * opacity)), 0.9))
        p.drawArc(QRectF(-radius, -radius, radius * 2, radius * 2), int((224 + t * 0.65 * sweep) * 16), 57 * 16)
        p.setPen(self._pen(QColor(100, 197, 240, int(42 * opacity)), 0.75))
        p.drawArc(QRectF(-radius, -radius, radius * 2, radius * 2), int((48 - t * 0.8 * sweep) * 16), 44 * 16)

    def _draw_radial_marks(self, p: QPainter, t: float, opacity: float) -> None:
        for angle, radius, length, alpha in (
            (-2, 143, 9, 105),
            (92, 147, 5, 60),
            (178, 144, 8, 86),
            (270, 146, 5, 52),
            (42, 149, 4, 48),
        ):
            flicker = 0.82 + 0.18 * math.sin(t * 0.62 + angle)
            p.save()
            p.rotate(angle)
            p.setPen(self._pen(QColor(227, 247, 255, int(alpha * opacity * flicker)), 1.15))
            p.drawLine(QPointF(0, -radius), QPointF(0, -radius - length))
            p.restore()

    def _draw_main_ring(self, p: QPainter, t: float, glow: float, inner: float,
                        pulse: float, blink: float, sweep: float, amp: float, state: str) -> None:
        glow = min(1.4, glow * self.GLOW_STRENGTH)
        radius = self.LOGO_RADIUS
        rect = QRectF(-radius, -radius, radius * 2, radius * 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((28, 10), (18, 16), (12, 26), (7, 52)):
            p.setPen(self._pen(self._tint(0.55, alpha * glow * pulse * blink), width))
            p.drawEllipse(rect)
        p.setPen(self._pen(self._tint(1.0, 205 * glow * pulse * blink), 2.3))
        p.drawEllipse(rect)
        p.setPen(self._pen(self._tint(1.5, 100 * glow * blink), 1.1))
        p.drawEllipse(QRectF(-radius - 3, -radius - 3, (radius + 3) * 2, (radius + 3) * 2))
        if LOGO_DOT_RINGS:
            self._draw_dotted_arc(p, radius, 32 + t * 3.2 * sweep, 67, 5.5, 1.6,
                                  QColor(189, 238, 255, int(150 * glow)))
            p.setPen(self._pen(QColor(228, 249, 255, int(104 * glow * pulse)), 0.85))
            p.drawArc(rect, int((224 - t * 1.8 * sweep) * 16), 26 * 16)
        else:
            p.setPen(self._pen(QColor(189, 238, 255, int(150 * glow)), 1.5))
            p.drawArc(rect, int((32 + t * 3.2 * sweep) * 16), 67 * 16)
            p.setPen(self._pen(QColor(228, 249, 255, int(104 * glow * pulse)), 0.85))
            p.drawArc(rect, int((224 - t * 1.8 * sweep) * 16), 26 * 16)
        inner_radius = radius - 8
        breathe = 1.0 + (amp * 0.05 if state == 'SPEAKING' else 0.0)
        ir = inner_radius * breathe
        irect = QRectF(-ir, -ir, ir * 2, ir * 2)
        p.setPen(self._pen(self._tint(1.1, 52 * inner), 6.0))
        p.drawEllipse(irect)
        p.setPen(self._pen(QColor(247, 252, 255, int(min(255, 238 * inner + amp * 60 * inner))), 2.1))
        p.drawEllipse(irect)
        if state == 'THINKING':
            self._draw_dotted_orbit(p, t, (radius - 8) * 1.04, 8, -26.0, 1.7,
                                    self._tint(1.2, 255), 175 * inner)

    def _draw_left_arc(self, p: QPainter, t: float, opacity: float,
                       activation: float, sweep: float) -> None:
        radius = 127
        rect = QRectF(-radius, -radius, radius * 2, radius * 2)
        start = 161 + t * 1.15 * sweep
        arc_alpha = int((180 + 75 * activation) * opacity)
        p.setPen(self._pen(QColor(102, 209, 255, int(38 * opacity)), 8.0))
        p.drawArc(rect, int(start * 16), 35 * 16)
        if LOGO_DOT_RINGS:
            p.setPen(self._pen(QColor(250, 253, 255, arc_alpha), 2.0))
            p.drawArc(rect, int(start * 16), 22 * 16)
            p.setPen(self._pen(QColor(250, 253, 255, int(arc_alpha * 0.78)), 1.4))
            p.drawArc(rect, int((start + 27) * 16), 8 * 16)
        else:
            p.setPen(self._pen(QColor(250, 253, 255, arc_alpha), 2.7))
            p.drawArc(rect, int(start * 16), 22 * 16)
            p.setPen(self._pen(QColor(250, 253, 255, int(arc_alpha * 0.78)), 1.8))
            p.drawArc(rect, int((start + 27) * 16), 8 * 16)

    def _draw_text(self, p: QPainter, t: float, opacity: float, activation: float) -> None:
        font = QFont(DISPLAY_FONT)
        font.setPixelSize(self.TEXT_SIZE)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        p.setFont(font)
        text = LOGO_TITLE
        metrics = p.fontMetrics()
        tracking = 4.2
        widths = [metrics.horizontalAdvance(letter) for letter in text]
        total_width = sum(widths) + tracking * (len(text) - 1)
        baseline = (metrics.ascent() - metrics.descent()) / 2.0

        typed = len(text) if not LOGO_STARTUP_ANIM else _ease((t - 1.0) / 0.62) * len(text)
        x = -total_width / 2.0
        for index, (letter, width) in enumerate(zip(text, widths)):
            letter_opacity = max(0.0, min(1.0, typed - index)) * opacity
            if letter_opacity:
                p.setPen(QColor(92, 207, 255, int((34 + 30 * activation) * letter_opacity)))
                p.drawText(QPointF(x + 1.2, baseline + 1.2), letter)
                p.setPen(QColor(249, 252, 255, int((222 + 33 * activation) * letter_opacity)))
                p.drawText(QPointF(x, baseline), letter)
            x += width + tracking

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setOpacity(self._graphic_opacity)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        widget_box = float(max(2, min(self.width(), self.height()) - 6))
        visual_size = float(getattr(self, '_visual_logo_size', min(self.width(), self.height())))
        box = float(max(2, min(widget_box, visual_size) - 6))
        if box < 2:
            p.end()
            return
        try:
            bld = self._blend
            energy, glow = float(bld['energy']), float(bld['glow'])
            spread, sweep = float(bld['spread']), float(bld['sweep'])
            dim = float(bld['dim'])
        except Exception:
            energy, glow, spread, sweep, dim = 0.55, 0.5, 1.0, 1.0, 1.0
        state = getattr(self, '_state', 'IDLE')
        t = self.seconds()
        motion_t = t * self.ANIMATION_SPEED * max(0.15, energy) * max(0.25, float(self._animation_speed))
        if LOGO_STARTUP_ANIM:
            startup_glow, startup_rings, startup_inner, startup_text = self._startup(t)
        else:
            startup_glow = startup_rings = startup_inner = startup_text = 1.0
        stroke_alpha = max(0.0, min(1.0, self._stroke_opacity)) * dim
        glow_o = startup_glow * stroke_alpha
        rings_o = startup_rings * stroke_alpha
        inner_o = startup_inner * stroke_alpha
        text_o = startup_text * stroke_alpha
        act_prog = max(0.0, min(1.0, (t - self._activation_at) / self.ACTIVATION_DURATION))
        activation = math.sin(math.pi * act_prog) ** 2
        speak_boost = 1.5 if state == 'SPEAKING' else 1.0
        amp = self._amp_display
        pulse = (0.80 + 0.20 * math.sin(t * 1.45) ** 2 + 0.33 * activation) * (0.7 + 0.6 * glow)
        if state == 'ERROR':
            blink = 0.55 + 0.45 * math.sin(t * math.pi * 2.0 * 1.6)
        else:
            blink = 1.0
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        s = box / 300.0
        expansion = 1.0 + self.ACTIVATION_GROWTH * activation + amp * 0.05 * speak_boost

        p.translate(c)
        p.scale(s * expansion, s * expansion)

        # Startup-only dark core. The panel logo stays transparent after the
        # startup transition because _startup_bg_fill_active is then cleared.
        bg_fill = LOGO_BG_FILL if getattr(self, '_startup_bg_fill_active', False) else 0
        core_fade = max(0.0, min(1.0, float(getattr(self, '_startup_core_fade', 0.0))))
        if bg_fill > 0 and core_fade > 0.001:
            core_r = self.LOGO_RADIUS * 0.94
            fill_a = int(max(0, min(255, bg_fill)) * stroke_alpha * core_fade)
            core = QRadialGradient(QPointF(), core_r)
            core.setColorAt(0.0, QColor(4, 7, 12, fill_a))
            core.setColorAt(0.74, QColor(4, 7, 12, int(fill_a * 0.96)))
            core.setColorAt(0.90, QColor(4, 7, 12, int(fill_a * 0.44)))
            core.setColorAt(1.0, QColor(4, 7, 12, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(core))
            p.drawEllipse(QPointF(), core_r, core_r)

        # Startup bloom — a bright cyan halo drawn only while the launch
        # transition is running (self._startup_glow ramps 1.0 → 0.0).
        sg = getattr(self, '_startup_glow', 0.0)
        if sg > 0.01:
            # 1.30x keeps the halo safely inside the square widget so the
            # soft fade completes to alpha 0 *before* it would be clipped
            # into a square edge by the widget boundary.
            bloom_r = self.LOGO_RADIUS * STARTUP_GLOW_SCALE
            bloom = QRadialGradient(QPointF(), bloom_r)
            bloom.setColorAt(0.00, QColor(180, 245, 255, int(230 * sg * inner_o)))
            bloom.setColorAt(0.28, QColor(110, 220, 250, int(150 * sg * inner_o)))
            bloom.setColorAt(0.55, QColor(60, 195, 240, int(80  * sg * inner_o)))
            bloom.setColorAt(0.80, QColor(20, 165, 225, int(24  * sg * inner_o)))
            bloom.setColorAt(1.00, QColor(0, 150, 220, 0))
            p.setBrush(QBrush(bloom))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(), bloom_r, bloom_r)

        self._draw_particles(p, motion_t, glow_o, activation, spread, stroke_alpha)
        self._draw_secondary_rings(p, motion_t, rings_o, sweep)
        self._draw_radial_marks(p, motion_t, rings_o)
        self._draw_main_ring(p, motion_t, glow_o, inner_o, pulse, blink, sweep, amp, state)
        self._draw_left_arc(p, motion_t, rings_o, activation, sweep)

        if state == 'LISTENING' and rings_o > 0.01:
            self._draw_dotted_orbit(p, motion_t, 140.0, 8, 14.0, 1.7,
                                    self._tint(1.15, 255), 150 * rings_o)

        if state == 'EXECUTING' and rings_o > 0.01:
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(2):
                ph = (t * 0.9 + i * 0.5) % 1.0
                rr = self.LOGO_RADIUS * (0.9 + ph * 0.65)
                p.setPen(self._pen(self._tint(1.1, (1.0 - ph) * 130 * rings_o), 1.5))
                p.drawEllipse(QPointF(), rr, rr)

        if rings_o > 0.01:
            scan_a = (t * 34.0 * max(0.55, sweep)) % 360.0
            sr = self.LOGO_RADIUS + 12
            p.setPen(self._pen(QColor(225, 250, 255, int(68 * rings_o)), 1.15))
            p.drawArc(QRectF(-sr, -sr, sr * 2, sr * 2), int(scan_a * 16), 16 * 16)
            pulse_r = self.LOGO_RADIUS + 17 + 3.0 * math.sin(t * 2.2)
            p.setPen(self._pen(self._tint(1.08, int(28 * rings_o)), 0.8))
            p.drawEllipse(QPointF(), pulse_r, pulse_r)

        self._draw_text(p, t, text_o, activation)
        p.end()

    def enterEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().enterEvent(event)


class RefinedArcLogoButton(RefinedArcLogo):
    clicked = pyqtSignal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._drag_start = None
        self._moved = False
        # Whole-widget hit testing. The logo is painted as a circle inside a
        # square widget; without these, the transparent corners sometimes eat
        # the first press and the drag "starts late".
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -- helpers -------------------------------------------------------
    def _fast_forward_settle(self) -> None:
        """Click on the startup logo: settle smoothly, never snap.

        Delegates to the window's phase machine; the click still opens the
        command panel afterwards in every phase.
        """
        try:
            window = self.window()
        except Exception:
            return
        try:
            settle = getattr(window, '_startup_click_settle', None)
            if settle is not None:
                settle()
        except Exception:
            pass

    # -- mouse handling ------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._fast_forward_settle()
            self._drag_start = event.globalPosition().toPoint()
            self._moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        window = self.window()
        if (self._drag_start is not None
                and getattr(window, "_drag_reactor_enabled", True)
                and event.buttons() & Qt.MouseButton.LeftButton):
            # A drag must never fight the startup timeline: fast-forward the
            # transition first, then move the already-settled window.
            if getattr(window, '_startup_phase', None) is _StartupPhase.ANIMATING:
                try:
                    self._fast_forward_settle()
                except Exception:
                    pass
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._moved = True
                target = window.pos() + delta
                # Keep the puck on-screen; MainWindow already has this helper.
                if hasattr(window, "_clamp_position_to_screen"):
                    try:
                        target = window._clamp_position_to_screen(target)
                    except Exception:
                        pass
                window.move(target)
                self._drag_start = event.globalPosition().toPoint()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            moved = self._moved
            self._drag_start = None
            self._moved = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if moved:
                try:
                    window = self.window()
                    if hasattr(window, "_save_window_position"):
                        window._save_window_position()
                except Exception:
                    pass
            else:
                self.trigger_activation()
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Unified panel fill: every translucent surface in the command window (dashboard
# row, HUD cards, chat log, input field, tab buttons) shares this one alpha so
# nothing reads as "more see-through" than its neighbour.
# ---------------------------------------------------------------------------
_PANEL_FILL_RGB = (1, 15, 25)
_PANEL_FILL_A = 255
_PANEL_FILL_CSS = 'rgba(%d,%d,%d,%d)' % (_PANEL_FILL_RGB[0], _PANEL_FILL_RGB[1], _PANEL_FILL_RGB[2], _PANEL_FILL_A)


def _panel_fill_qcolor(alpha=_PANEL_FILL_A):
    return QColor(_PANEL_FILL_RGB[0], _PANEL_FILL_RGB[1], _PANEL_FILL_RGB[2], alpha)


class _AngularFrameMixin:
    def _angular_path(self, rect, cut=8):
        cut = max(4.0, min(float(cut), min(rect.width(), rect.height()) * 0.28))
        path = QPainterPath()
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


class _AngularButton(QPushButton, _AngularFrameMixin):
    def __init__(self, text='', parent=None, accent=False, compact=False, icon=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumHeight(32 if compact else 38)
        self._hover = False
        self._accent = accent
        self._icon = icon
        self.setMouseTracking(True)
        self.setStyleSheet('background:transparent;border:none;padding:0;')

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = self._angular_path(r, 7)

        active = self.isChecked() or self.hasFocus() or self._hover
        if self.isDown():
            fill = QColor(0, 120, 157, 185)
        elif self.isChecked():
            fill = QColor(0, 92, 124, 150)
        elif self._hover:
            fill = QColor(0, 72, 98, 125)
        else:
            fill = _panel_fill_qcolor()
        p.fillPath(path, QBrush(fill))

        p.setBrush(Qt.BrushStyle.NoBrush)
        border = QColor(66, 199, 239, 240) if active else QColor(67, 151, 185, 92)
        p.setPen(QPen(border, 1.2))
        p.drawPath(path)

        glyph_col = QColor(233, 252, 255, 255) if active else QColor(168, 239, 255, 235)

        # ── Auto-fit layout: measure icon + text, center the whole group ──
        label = self.text() or ''
        pad_x = 7.0
        avail = max(8.0, r.width() - pad_x * 2)

        is_vision_button = label.strip().upper() == 'VISION'
        icon_size = 13.5 if self._icon else 0.0
        gap       = 5.0  if self._icon else 0.0
        base_pt   = 8 if is_vision_button else 7
        min_pt    = 6 if is_vision_button else 5

        f = QFont('Space Grotesk', base_pt, QFont.Weight.Bold)
        fm = p.fontMetrics()
        # Use a fresh metrics pass against the font we're about to draw with.
        f_fm = QFont(f)
        tmp = QFontMetricsF(f_fm) if False else None  # avoid extra imports

        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(f)
        text_w = fm.horizontalAdvance(label)
        group_w = icon_size + gap + text_w

        # Shrink the point size until the icon+label group fits the button.
        pt = base_pt
        while group_w > avail and pt > min_pt:
            pt -= 1
            f = QFont('Space Grotesk', pt, QFont.Weight.Bold)
            fm = QFontMetrics(f)
            text_w = fm.horizontalAdvance(label)
            group_w = icon_size + gap + text_w

        # If it still doesn't fit, elide the label so nothing overflows.
        if group_w > avail and text_w > 0:
            room = max(4.0, avail - icon_size - gap)
            label = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(room))
            text_w = fm.horizontalAdvance(label)
            group_w = icon_size + gap + text_w

        # Center the group horizontally; align vertically to the middle.
        start_x = r.left() + (r.width() - group_w) / 2.0
        cy      = r.top() + r.height() / 2.0
        text_h  = fm.height()

        if self._icon:
            icon_box = QRectF(start_x, cy - icon_size / 2.0, icon_size, icon_size)
            _paint_tab_icon(p, self._icon, icon_box, glyph_col, size=icon_size)
            text_x = start_x + icon_size + gap
        else:
            text_x = start_x

        p.setFont(f)
        p.setPen(glyph_col)
        text_rect = QRectF(text_x, cy - text_h / 2.0, text_w + 1.0, text_h)
        p.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label,
        )
        p.end()
class _AngularLineEdit(QLineEdit, _AngularFrameMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("QLineEdit{background:transparent;color:#f0fcff;border:none;padding:5px 12px;font:9pt 'Rajdhani';}")
        self._focus = False

    def focusInEvent(self, e):
        self._focus = True
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._focus = False
        self.update()
        super().focusOutEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = self._angular_path(r, 8)
        p.fillPath(path, QBrush(_panel_fill_qcolor()))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(74, 196, 239, 210) if self._focus else QColor(67,151,185,105), 1.2))
        p.drawPath(path)
        p.setPen(QPen(QColor(118,230,252,170 if self._focus else 70), 1.4))
        p.drawLine(QPointF(r.left()+2, r.top()+9), QPointF(r.left()+2, r.top()+2))
        p.drawLine(QPointF(r.left()+2, r.top()+2), QPointF(r.left()+15, r.top()+2))
        p.drawLine(QPointF(r.right()-15, r.bottom()-2), QPointF(r.right()-2, r.bottom()-2))
        p.drawLine(QPointF(r.right()-2, r.bottom()-2), QPointF(r.right()-2, r.bottom()-9))
        p.end()
        super().paintEvent(e)


class _AngularTextEdit(QTextEdit, _AngularFrameMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        # The frame is fully painted by paintEvent; keeping this text widget
        # translucent only forces extra layered-window compositing on Windows.
        self.setAutoFillBackground(False)
        self.setStyleSheet("QTextEdit{background:transparent;color:#b9efff;border:none;padding:9px;font:9pt 'Rajdhani';}QScrollBar:vertical{background:transparent;width:0px;border:none;}QScrollBar:horizontal{background:transparent;height:0px;border:none;}")
        self._focus = False

    def focusInEvent(self, e):
        self._focus = True; self.update(); super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._focus = False; self.update(); super().focusOutEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = self._angular_path(r, 10)
        p.fillPath(path, QBrush(_panel_fill_qcolor()))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(74,196,239,205) if self._focus else QColor(67,151,185,105), 1.2))
        p.drawPath(path)
        p.setPen(QPen(QColor(118,230,252,150 if self._focus else 60), 1.4))
        p.drawLine(QPointF(r.left()+2, r.top()+10), QPointF(r.left()+2, r.top()+2))
        p.drawLine(QPointF(r.left()+2, r.top()+2), QPointF(r.left()+18, r.top()+2))
        p.drawLine(QPointF(r.right()-18, r.bottom()-2), QPointF(r.right()-2, r.bottom()-2))
        p.drawLine(QPointF(r.right()-2, r.bottom()-2), QPointF(r.right()-2, r.bottom()-10))
        p.end()
        super().paintEvent(e)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

# =====================================================================
#  Generic SVG path-data -> QPainterPath converter, used to render the
#  tab-nav glyphs (see _TAB_ICON_DEFS below) straight from raw "d" strings
#  instead of hand-transcribing every curve. Supports the SVG path commands
#  actually used by the movingicons.dev set: M/L/H/V/C/A/Z (upper + lower).
#  Elliptical arcs (A/a) are sampled into short polylines — invisible at
#  icon scale — rather than mapped to Qt's differing arc-angle convention.
# =====================================================================
def _svg_arc_to_polyline(path: 'QPainterPath', x1: float, y1: float,
                          rx: float, ry: float, rot_deg: float,
                          large_arc: float, sweep: float,
                          x2: float, y2: float, segs: int = 16) -> None:
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        path.lineTo(x2, y2)
        return
    phi = math.radians(rot_deg)
    cosp, sinp = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cosp * dx2 + sinp * dy2
    y1p = -sinp * dx2 + cosp * dy2
    rx, ry = abs(rx), abs(ry)
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        s = math.sqrt(lam)
        rx *= s; ry *= s
    sign = -1.0 if large_arc == sweep else 1.0
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = sign * math.sqrt(max(0.0, num / den)) if den else 0.0
    cxp = co * (rx * y1p / ry)
    cyp = -co * (ry * x1p / rx)
    cx = cosp * cxp - sinp * cyp + (x1 + x2) / 2.0
    cy = sinp * cxp + cosp * cyp + (y1 + y2) / 2.0

    def _ang(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, dot / mag))) if mag else 0.0
        return a if (ux * vy - uy * vx) >= 0 else -a

    theta1 = _ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = _ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi
    for k in range(1, segs + 1):
        t = theta1 + dtheta * (k / segs)
        ex = cx + rx * math.cos(t) * cosp - ry * math.sin(t) * sinp
        ey = cy + rx * math.cos(t) * sinp + ry * math.sin(t) * cosp
        path.lineTo(ex, ey)


def _svg_path_to_qpp(d: str) -> 'QPainterPath':
    path = QPainterPath()
    tokens = re.findall(r'[MLHVCAZmlhvcaz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?', d)
    i = 0
    cx = cy = 0.0
    start_x = start_y = 0.0
    cmd = None

    def nextf():
        nonlocal i
        v = float(tokens[i]); i += 1
        return v

    while i < len(tokens):
        t = tokens[i]
        if t in 'MLHVCAZmlhvcaz':
            cmd = t
            i += 1
        if cmd in ('M', 'm'):
            x, y = nextf(), nextf()
            if cmd == 'm':
                x += cx; y += cy
            cx, cy = x, y
            start_x, start_y = cx, cy
            path.moveTo(cx, cy)
            cmd = 'L' if cmd == 'M' else 'l'
        elif cmd in ('L', 'l'):
            x, y = nextf(), nextf()
            if cmd == 'l':
                x += cx; y += cy
            cx, cy = x, y
            path.lineTo(cx, cy)
        elif cmd in ('H', 'h'):
            x = nextf()
            if cmd == 'h':
                x += cx
            cx = x
            path.lineTo(cx, cy)
        elif cmd in ('V', 'v'):
            y = nextf()
            if cmd == 'v':
                y += cy
            cy = y
            path.lineTo(cx, cy)
        elif cmd in ('C', 'c'):
            x1, y1, x2, y2, x, y = (nextf() for _ in range(6))
            if cmd == 'c':
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            path.cubicTo(x1, y1, x2, y2, x, y)
            cx, cy = x, y
        elif cmd in ('A', 'a'):
            rx, ry, rot = nextf(), nextf(), nextf()
            laf, sf = nextf(), nextf()
            x, y = nextf(), nextf()
            if cmd == 'a':
                x += cx; y += cy
            _svg_arc_to_polyline(path, cx, cy, rx, ry, rot, laf, sf, x, y)
            cx, cy = x, y
        elif cmd in ('Z', 'z'):
            path.closeSubpath()
            cx, cy = start_x, start_y
        else:
            i += 1
    return path


# Static (non-hover) glyph shapes lifted straight from jarvis_icon_tabs.md —
# one entry per nav tab, plus the two Vision states. Each entry is a list of
# ('path', d) / ('line', x1,y1,x2,y2) / ('circle', cx,cy,r) / ('rrect', x,y,w,h,r)
# ops in the original SVGs' 0-24 viewBox coordinate space.
_TAB_ICON_DEFS = {
    'chat': [
        ('path', 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'),
        ('line', 16, 10, 16, 10), ('line', 12, 10, 12, 10), ('line', 8, 10, 8, 10),
    ],
    'eye': [
        ('path', 'M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0'),
        ('circle', 12, 12, 3),
    ],
    'eye_off': [
        ('path', 'M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49'),
        ('path', 'M14.084 14.158a3 3 0 0 1-4.242-4.242'),
        ('path', 'M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143'),
        ('path', 'm2 2 20 20'),
    ],
    'activity': [
        ('path', 'M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2'),
    ],
    'tools': [
        ('path', 'm14.5 12.5-8 8a2.119 2.119 0 1 1-3-3l8-8'),
        ('path', 'm16 16 6-6'), ('path', 'm8 8 6-6'),
        ('path', 'm9 7 8 8'), ('path', 'm21 11-8-8'),
    ],
    'web': [
        ('path', 'm21 3-5 5'), ('path', 'm16 3 5 5'),
        ('path', 'M2 12h20A10 10 0 1 1 12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 4-10'),
    ],
    'memory': [
        ('path', 'M12 5a3 3 0 1 0-5.997.142 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588 4 4 0 0 0 7.636 2.106 3.2 3.2 0 0 0 .164-.546c.028-.13.306-.13.335 0a3.2 3.2 0 0 0 .163.546 4 4 0 0 0 7.636-2.106 4 4 0 0 0 .556-6.588 4 4 0 0 0-2.526-5.77A3 3 0 1 0 12 5'),
        ('path', 'M17.599 6.5a3 3 0 0 0 .399-1.375'),
        ('path', 'M6.003 5.125A3 3 0 0 0 6.401 6.5'),
        ('path', 'M3.477 10.896a4 4 0 0 1 .585-.396'),
        ('path', 'M19.938 10.5a4 4 0 0 1 .585.396'),
        ('path', 'M6 18a4 4 0 0 1-1.967-.516'),
        ('path', 'M19.967 17.484A4 4 0 0 1 18 18'),
        ('circle', 12, 12, 3),
        ('path', 'm15.7 10.4-.9.4'), ('path', 'm9.2 13.2-.9.4'),
        ('path', 'm13.6 15.7-.4-.9'), ('path', 'm10.8 9.2-.4-.9'),
        ('path', 'm15.7 13.5-.9-.4'), ('path', 'm9.2 10.9-.9-.4'),
        ('path', 'm10.5 15.7.4-.9'), ('path', 'm13.1 9.2.4-.9'),
    ],
    'system': [
        ('rrect', 4, 4, 16, 16, 2), ('rrect', 9, 9, 6, 6, 1),
        ('line', 15, 2, 15, 4), ('line', 15, 20, 15, 22),
        ('line', 2, 15, 4, 15), ('line', 2, 9, 4, 9),
        ('line', 20, 15, 22, 15), ('line', 20, 9, 22, 9),
        ('line', 9, 2, 9, 4), ('line', 9, 20, 9, 22),
    ],
    'settings': [
        ('path', 'M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z'),
        ('circle', 12, 12, 3),
    ],
}


def _paint_tab_icon(p: 'QPainter', key: str, rect: 'QRectF', color, size: float = 14.0) -> None:
    ops = _TAB_ICON_DEFS.get(key)
    if not ops:
        return
    p.save()
    pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = size / 24.0
    ox = rect.left() + (rect.width() - size) / 2.0
    oy = rect.top() + (rect.height() - size) / 2.0
    p.translate(ox, oy)
    p.scale(s, s)
    for op in ops:
        kind = op[0]
        if kind == 'path':
            p.drawPath(_svg_path_to_qpp(op[1]))
        elif kind == 'line':
            _, x1, y1, x2, y2 = op
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        elif kind == 'circle':
            _, ccx, ccy, r = op
            p.drawEllipse(QPointF(ccx, ccy), r, r)
        elif kind == 'rrect':
            _, rx0, ry0, rw, rh, rr = op
            p.drawRoundedRect(QRectF(rx0, ry0, rw, rh), rr, rr)
    p.restore()


# =====================================================================
#  JARVIS ICON BUTTONS — QPainter ports of the movingicons.dev SVG set
#  (mic-off & arrow-big-right). Original Svelte components converted to
#  PyQt6 with the same hover animations.
# =====================================================================
class _MicIconButton(QPushButton):
    """Mic-off SVG rendered with QPainter. Hover shakes horizontally for
    600 ms — the same gesture as the Svelte original."""
    def __init__(self, parent=None, size=22, color=None):
        super().__init__(parent)
        self._size = int(size)
        self._color = color or C.PRI
        self._hover = False
        self._shake_t = 0.0
        self._shake_on = False
        self.setFixedSize(self._size + 18, self._size + 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background:transparent;border:none;padding:0;")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def enterEvent(self, e):
        self._hover = True
        self._shake_on = True
        self._shake_t = 0.0
        QTimer.singleShot(600, lambda: setattr(self, "_shake_on", False))
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def _tick(self):
        if self._shake_on:
            self._shake_t += 0.016
        self.update()

    def _offset_x(self) -> float:
        if not self._shake_on:
            return 0.0
        p = (self._shake_t / 0.6) % 1.0
        amp = 0.07 * (self._size + 18)
        return math.sin(p * math.pi * 6.0) * amp * (1.0 - p)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(C.WHITE if self._hover else self._color)
        pen = QPen(col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)

        s = self._size / 24.0
        ox = self.width() / 2.0 - self._size / 2.0 + self._offset_x()
        oy = self.height() / 2.0 - self._size / 2.0
        p.translate(ox, oy); p.scale(s, s)

        p.drawLine(QPointF(2, 2), QPointF(22, 22))

        path1 = QPainterPath()
        path1.moveTo(18.89, 13.23)
        path1.arcTo(QRectF(2.0, 5.0, 17.0, 14.0), 20, 80)
        path1.lineTo(19, 12); path1.lineTo(19, 10)
        p.drawPath(path1)

        path2 = QPainterPath()
        path2.moveTo(5, 10); path2.lineTo(5, 12)
        path2.arcTo(QRectF(5.0, 5.0, 14.0, 14.0), 180, 60)
        path2.lineTo(17, 17)
        p.drawPath(path2)

        path3 = QPainterPath()
        path3.moveTo(15, 9.34); path3.lineTo(15, 5)
        path3.arcTo(QRectF(9.32, 2.0, 5.68, 6.0), 180, 180)
        path3.lineTo(9.32, 3.67)
        p.drawPath(path3)

        path4 = QPainterPath()
        path4.moveTo(9, 9); path4.lineTo(9, 12)
        path4.arcTo(QRectF(9.0, 9.0, 6.0, 6.0), 180, 60)
        path4.lineTo(14.12, 14.12)
        p.drawPath(path4)

        p.drawLine(QPointF(12, 19), QPointF(12, 22))
        p.end()


class _SendIconButton(QPushButton):
    """'arrow-big-right' SVG rendered with QPainter. Hover slides +3 px."""
    def __init__(self, parent=None, size=22, color=None):
        super().__init__(parent)
        self._size = int(size)
        self._color = color or C.PRI
        self._hover = False
        self.setFixedSize(self._size + 18, self._size + 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background:transparent;border:none;padding:0;")

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(C.WHITE if self._hover else self._color)
        p.setPen(QPen(col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        s = self._size / 24.0
        ox = self.width() / 2.0 - self._size / 2.0 + (3.0 if self._hover else 0.0)
        oy = self.height() / 2.0 - self._size / 2.0
        p.translate(ox, oy); p.scale(s, s)
        path = QPainterPath()
        path.moveTo(6, 9); path.lineTo(12, 9); path.lineTo(12, 5)
        path.lineTo(19, 12); path.lineTo(12, 19); path.lineTo(12, 15)
        path.lineTo(6, 15); path.closeSubpath()
        p.drawPath(path)
        p.end()



class _VisionSwitch(QCheckBox):
    """Text-first Vision switch used inside the left scroll control surface.

    Symbols/icons are intentionally removed from the visible label.  The full
    control is wide enough to show its name and the switch itself, while the
    containing QScrollArea keeps the Vision stage compact.
    """
    def __init__(self, text: str, parent=None, icon: str = "", tip: str = ""):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(tip or text)
        # Preserve compatibility with existing call sites that still pass icon=.
        self._icon_only = False
        self.setStyleSheet(f"""
            QCheckBox {{
                color:{C.TEXT_MED}; background:rgba(1,15,25,95); border:1px solid rgba(67,170,199,45);
                border-radius:7px; font:900 8.8pt 'Exo 2'; letter-spacing:0.75px;
                spacing:10px; padding:4px 9px;
            }}
            QCheckBox:hover {{
                color:{C.WHITE}; border-color:rgba(88,214,245,120);
                background:rgba(0,72,96,70);
            }}
            QCheckBox:checked {{
                color:{C.WHITE}; border-color:{C.PRI};
                background:rgba(0,134,184,92);
            }}
            QCheckBox::indicator {{
                width:34px; height:17px; border-radius:9px;
                background:rgba(8,24,34,220);
                border:1px solid rgba(60,156,190,110);
            }}
            QCheckBox::indicator:hover {{ border-color:{C.PRI}; }}
            QCheckBox::indicator:checked {{
                background:rgba(0,150,200,220); border-color:{C.PRI};
            }}
            QCheckBox:disabled {{ color:#365664; }}
            QCheckBox::indicator:disabled {{
                background:rgba(8,16,21,120); border-color:rgba(50,70,78,90);
            }}
        """)


class _VisionLoader(QWidget):
    """Three-by-three moving square loader based on the supplied animation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(96, 96)
        self._t = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(55)

    def _step(self):
        self._t = (self._t + 0.14) % 7.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # The path is the same clockwise square route from the supplied CSS.
        points = [(0,0),(32,0),(64,0),(64,32),(32,32),(32,64),(0,64),(0,32)]
        phase = self._t
        for i,(x,y) in enumerate(points):
            x2, y2 = points[i]
            # staggered phase, with a short eased dwell/move approximation
            q = (phase - i * 0.72) % 7.0
            idx = int(q)
            frac = q - idx
            a = points[idx % 8]
            b = points[(idx + 1) % 8]
            # long dwell around each point + smooth movement
            if frac < 0.35:
                e = 0.0
            else:
                t = min(1.0, (frac - 0.35) / 0.65)
                e = t*t*(3.0-2.0*t)
            px = a[0] + (b[0]-a[0])*e
            py = a[1] + (b[1]-a[1])*e
            r = QRectF(1 + px, 1 + py, 24, 24)
            p.setBrush(QBrush(QColor(216,248,255,235)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 1.5, 1.5)
        p.end()


class _InlineVisionView(QFrame):
    """Camera surface used exclusively by the Chat Vision stage."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(210, 190)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._frame = QImage()
        self._faces: list[tuple[float,float,float,float]] = []
        self._object_labels: list[tuple] = []
        self._loading = True
        self._face_track = True
        self._object_track = False
        self._visible_name = True
        self._face_zoom = False   # off by default: the frame is shown uncropped
        self._expression = False
        self._object_detection = True
        self._invert = False
        self._grid_overlay = False
        self._fps_hud = False
        self._low_light = False
        self._center_reticle = False
        self._edge_outline = True
        self._fps_last_t = time.monotonic()
        self._fps_last_count = 0
        self._fps_value = 0.0
        # Additive white lift applied over the live frame — a cheap way to keep
        # a dim webcam readable without touching the capture pipeline.
        self._brightness = 30
        self._status = 'VISION CAMERA · CONNECTING'
        self._frame_count = 0
        self._zoom_target = 1.0
        self._zoom = 1.0
        self._face_center = (0.5, 0.5)
        self.setStyleSheet('QFrame { background:transparent; border:none; }')
        self._loader = _VisionLoader(self)
        self._loader.setFixedSize(96,96)
        self._loader.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._loader.hide()
        self._loading_timer = QTimer(self)
        self._loading_timer.setSingleShot(True)
        self._loading_timer.timeout.connect(self._loading_timeout)

    def set_options(self, *, face_track=None, object_track=None, visible_name=None,
                    face_zoom=None, expression=None, object_detection=None, invert=None,
                    grid_overlay=None, fps_hud=None, low_light=None,
                    center_reticle=None, edge_outline=None):
        if face_track is not None: self._face_track = bool(face_track)
        if object_track is not None: self._object_track = bool(object_track)
        if visible_name is not None: self._visible_name = bool(visible_name)
        if face_zoom is not None: self._face_zoom = bool(face_zoom)
        if expression is not None: self._expression = bool(expression)
        if object_detection is not None: self._object_detection = bool(object_detection)
        if invert is not None: self._invert = bool(invert)
        if grid_overlay is not None: self._grid_overlay = bool(grid_overlay)
        if fps_hud is not None: self._fps_hud = bool(fps_hud)
        if low_light is not None: self._low_light = bool(low_light)
        if center_reticle is not None: self._center_reticle = bool(center_reticle)
        if edge_outline is not None: self._edge_outline = bool(edge_outline)
        self.update()

    def set_frame_bytes(self, data: bytes):
        px = QPixmap(); px.loadFromData(data)
        if px.isNull(): return
        self._frame = px.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._frame_count += 1
        if self._loading:
            self._loading = False
            self._status = 'VISION CAMERA · LIVE'
            self._loader.hide()
        self.update()

    def set_faces(self, faces):
        self._faces = [(float(a),float(b),float(c),float(d)) for a,b,c,d in (faces or [])]
        if self._faces and self._face_track and self._face_zoom:
            fx,fy,fw,fh=max(self._faces,key=lambda r:r[2]*r[3])
            self._face_center=(fx+fw/2.0, fy+fh/2.0)
            self._zoom_target=max(1.0,min(1.75,0.92/max(0.55,fw*2.0)))
        else:
            self._zoom_target=1.0
        self.update()

    def begin_loading(self):
        self._loading = True
        self._status = 'VISION CAMERA · CONNECTING'
        self._frame = QImage()
        self._faces = []
        self._loader.show()
        self._loader.raise_()
        self._position_loader()
        try:
            self._loading_timer.start(9000)
        except Exception:
            pass
        self.update()

    def _loading_timeout(self):
        if self._loading and self._frame_count == 0:
            self._status = 'VISION CAMERA · NO SIGNAL'
            self._loader.hide()
            self.update()

    def _position_loader(self):
        try:
            self._loader.move((self.width() - self._loader.width()) // 2,
                              (self.height() - self._loader.height()) // 2)
        except Exception:
            pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position_loader()

    def _draw_text(self, p, text, x, y, size=7, color=None, bold=True):
        p.setPen(QPen(qcol(color or C.PRI, 235), 1))
        p.setFont(QFont('Exo 2', size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        p.drawText(QPointF(x,y), text)

    def paintEvent(self, _):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r=QRectF(self.rect()).adjusted(1,1,-1,-1)
        W,H=self.width(),self.height()
        live = not self._frame.isNull()
        # Nothing dark sits behind or over a live feed: the glass field, grid
        # and glow bands are painted ONLY while there is no frame to show, so
        # the HUD chrome can never dim the camera picture.
        if not live:
            p.setBrush(QBrush(QColor(2,8,16,235)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 10, 10)
        p.save()
        p.setClipRect(r)
        if not live:
            for x in range(0,W,24):
                p.setPen(QPen(QColor(0,170,210,24),1)); p.drawLine(x,0,x,H)
            for y in range(0,H,24):
                p.setPen(QPen(QColor(0,170,210,24),1)); p.drawLine(0,y,W,y)
            # corner glow bands
            p.setPen(QPen(QColor(0,212,255,55),1)); p.drawLine(0,22,W,22)
            p.setPen(QPen(QColor(244,178,74,30),1)); p.drawLine(0,H-18,W,H-18)
        if live:
            self._zoom += (self._zoom_target-self._zoom)*0.12
            img=self._frame
            sw,sh=img.width(),img.height()
            if self._zoom>1.001:
                cx=int(self._face_center[0]*sw); cy=int(self._face_center[1]*sh)
                cw=max(32,int(sw/self._zoom)); ch=max(32,int(sh/self._zoom))
                left=max(0,min(sw-cw,cx-cw//2)); top=max(0,min(sh-ch,cy-ch//2))
                img=img.copy(left,top,cw,ch)
            # FIT the whole frame inside the view — letterboxed when the
            # camera's aspect differs from the widget. Nothing is cropped.
            scaled=img.scaled(int(W),int(H),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            sx=max(0,(W-scaled.width())//2); sy=max(0,(H-scaled.height())//2)
            if self._invert:
                # Mirror in place — no second pixel copy required.
                p.save(); p.translate(float(W),0.0); p.scale(-1.0,1.0)
                p.drawImage(QPointF(float(sx),float(sy)),scaled)
                p.restore()
            else:
                p.drawImage(QPointF(float(sx),float(sy)),scaled)
            brightness = 72 if self._low_light else self._brightness
            if brightness > 0:
                # Dim sensors read as "broken"; an additive pass lifts the
                # whole image with no measurable cost.
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.fillRect(QRectF(0,0,W,H),QColor(int(brightness),int(brightness),int(brightness),255))
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            if self._grid_overlay:
                p.setPen(QPen(QColor(88,214,245,34), 1))
                for gx in range(0, W + 1, 24):
                    p.drawLine(gx, 0, gx, H)
                for gy in range(0, H + 1, 24):
                    p.drawLine(0, gy, W, gy)

            if self._center_reticle:
                cx, cy = W / 2.0, H / 2.0
                p.setPen(QPen(QColor(140,235,255,155), 1.1))
                p.drawLine(QPointF(cx - 22, cy), QPointF(cx + 22, cy))
                p.drawLine(QPointF(cx, cy - 22), QPointF(cx, cy + 22))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), 13, 13)

            if self._fps_hud and self._frame_count != self._fps_last_count:
                now = time.monotonic()
                dt = now - self._fps_last_t
                if dt >= 0.35:
                    self._fps_value = (self._frame_count - self._fps_last_count) / dt
                    self._fps_last_count = self._frame_count
                    self._fps_last_t = now
                self._draw_text(p, f'VISION {self._fps_value:04.1f} FPS', 10, 18, 6, C.PRI)

            if self._edge_outline:
                p.setPen(QPen(QColor(88,214,245,145),1.4))
            else:
                p.setPen(QPen(QColor(88,214,245,55),1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r,10,10)
        else:
            # Explicit connection state: always show readable status while camera is starting.
            status_x = max(8.0, (W - 240.0) / 2.0)
            self._draw_text(p, self._status, status_x, H/2 + 58, 7, C.PRI)
            if self._loading:
                self._draw_text(p, 'INITIALISING OPTICAL LINK', status_x + 8, H/2 + 74, 6, C.TEXT_DIM)
        # HUD corners
        p.setPen(QPen(QColor(88,214,245,190),1.8))
        L=22; m=7
        for x,y,dx,dy in ((m,m,1,1),(W-m,m,-1,1),(m,H-m,1,-1),(W-m,H-m,-1,-1)):
            p.drawLine(QPointF(x,y),QPointF(x+dx*L,y)); p.drawLine(QPointF(x,y),QPointF(x,y+dy*L))
        p.restore(); p.end()

class RefinedCommandWindow(_CommandWindow):
    """A calmer command deck retaining the original message and panel hooks."""

    # ── Fading divider builder ────────────────────────────────────────────
    def _fading_rule_html(self, width_chars: int = 72) -> str:
        """A horizontal rule whose opacity fades smoothly to the right."""
        parts = []
        for i in range(width_chars):
            pos = i / max(1, width_chars - 1)
            alpha = int(215 * (1.0 - pos) ** 1.6)
            if alpha < 6:
                break
            parts.append(f'<span style="color:rgba(88,214,245,{alpha});">━</span>')
        return ''.join(parts)

    @staticmethod
    def _build_circular_avatar(src_path, out_path, size: int = 64) -> bool:
        """Crop src to a square, resize, and apply a circular alpha mask."""
        try:
            from PIL import Image, ImageDraw
            img = Image.open(str(src_path)).convert("RGBA")
            side = min(img.size)
            left = (img.width - side) // 2
            top  = (img.height - side) // 2
            img  = img.crop((left, top, left + side, top + side))
            img  = img.resize((size, size), Image.LANCZOS)

            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
            img.putalpha(mask)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(out_path), "PNG")
            return True
        except Exception:
            return False

    def _avatar_html(self, initial: str, color: str, category: str) -> str:
        """Circular avatar for the chat log.

        you    → assets/user_avatar.png    (transparent circle, no text)
        jarvis → assets/jarvis_avatar.png  (transparent circle, no text)
        others → letter badge (unchanged)
        """
        base = Path(__file__).resolve().parent
        if category == "you":
            src = base / "assets" / "user_avatar.png"
            tag = "user"
        elif category == "jarvis":
            src = base / "assets" / "jarvis_avatar.png"
            tag = "jarvis"
        else:
            src = None
            tag = None

        if src is not None and src.exists():
            try:
                cache = CONFIG_DIR / f"_chat_avatar_{tag}.png"
                if (not cache.exists()
                        or cache.stat().st_mtime < src.stat().st_mtime):
                    self._build_circular_avatar(src, cache, size=64)
                if cache.exists():
                    url = QUrl.fromLocalFile(str(cache)).toString()
                    return (
                        f'<img src="{url}" width="28" height="28" '
                        f'style="border-radius:14px; background:transparent; '
                        f'display:block;">'
                    )
            except Exception:
                pass

        return (
            f'<div style="color:{color}; background:transparent; '
            f'border:1px solid {color}; border-radius:14px; '
            f'width:26px; height:26px; line-height:26px; '
            f'text-align:center; font-family:Rajdhani; '
            f'font-size:11pt; font-weight:700;">'
            f'{initial}</div>'
        )

    def append_msg(self, who: str, text: str, you: bool = False):
        """Message row with avatar, speaker/time metadata, and fading dividers."""
        try:
            raw_who = str(who or '').strip() or 'JARVIS'
            low = raw_who.lower().replace('.', '').replace(' ', '')
            if you or low == 'you':
                category, color, initial = 'you', C.WHITE, 'Y'
            elif low.startswith('sys'):
                category, color, initial = 'sys', C.ACC2, 'S'
            elif low.startswith('vis'):
                category, color, initial = 'vis', C.PRI, 'V'
            elif low.startswith('file'):
                category, color, initial = 'file', C.GREEN, 'F'
            elif low.startswith('err'):
                category, color, initial = 'err', C.RED, '!'
            elif low.startswith('warn'):
                category, color, initial = 'warn', C.ACC2, 'W'
            elif low.startswith('news'):
                category, color, initial = 'news', C.ACC2, 'N'
            else:
                category, color, initial = 'jarvis', C.PRI, 'J'

            # Toggle state comes from the saved config so the button on the
            # top-right always matches what actually filters the chat — even
            # if a rebuild leaves the in-memory flag stale.
            try:
                _hide = bool(_read_full_config().get(
                    "hide_system_chat",
                    getattr(self, "_hide_system_chat", False),
                ))
            except Exception:
                _hide = bool(getattr(self, "_hide_system_chat", False))
            self._hide_system_chat = _hide
            if _hide and category not in ("jarvis", "you"):
                return

            cats = getattr(self._host, '_chat_filter_categories', {}) or {}
            if not bool(cats.get(category, True)):
                return

            self._stop_typing_dots()
            self._chat_entries.append((raw_who, str(text), bool(you)))
            if len(self._chat_entries) > 250:
                del self._chat_entries[:-250]
            self._clear_trailing_dots()
            safe_who = (raw_who.replace('&', '&amp;')
                                .replace('<', '&lt;')
                                .replace('>', '&gt;'))
            safe = (str(text).replace('&', '&amp;')
                            .replace('<', '&lt;')
                            .replace('>', '&gt;')
                            .replace('\n', '<br/>'))
            stamp = time.strftime('%I:%M %p').lstrip('0') or '12:00 AM'
            rule = '' if getattr(self, '_hide_chat_lines', False) else self._fading_rule_html()
            avatar = self._avatar_html(initial, color, category)

            block = (
                f'<div style="margin:10px 0 2px 0;">{rule}</div>'
                f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:4px 0;">'
                f'<tr>'
                f'  <td width="34" valign="top" style="padding-top:2px;">{avatar}</td>'
                f'  <td valign="top" style="padding-left:8px;">'
                f'    <div style="margin-bottom:3px;">'
                f'      <span style="color:{color}; font-weight:700; font-size:9pt; letter-spacing:0.6px;">{safe_who}</span>'
                f'      <span style="color:{C.TEXT_DIM}; font-size:8pt;">&nbsp;&nbsp;{stamp}</span>'
                f'    </div>'
                f'    <div style="color:{C.TEXT}; font-size:9.8pt; line-height:1.32; font-weight:600;">{safe}</div>'
                f'  </td>'
                f'</tr></table>'
                f'<div style="margin:2px 0 6px 0;">{rule}</div>'
            )
            self.chat.append(block)
            self._autoscroll_chat()
        except Exception:
            try:
                super().append_msg(who, text, you)
            except Exception:
                pass

    def paintEvent(self, event) -> None:
        """Paint only the HUD outline; never fill the whole panel."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        cut = 14.0
        path.moveTo(r.left() + cut, r.top())
        path.lineTo(r.right() - cut, r.top())
        path.lineTo(r.right(), r.top() + cut)
        path.lineTo(r.right(), r.bottom() - cut)
        path.lineTo(r.right() - cut, r.bottom())
        path.lineTo(r.left() + cut, r.bottom())
        path.lineTo(r.left(), r.bottom() - cut)
        path.lineTo(r.left(), r.top() + cut)
        path.closeSubpath()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(26, 92, 122, 205), 1.2))
        p.drawPath(path)
        # very soft edge glow, intentionally transparent inside
        p.setPen(QPen(QColor(0, 212, 255, 48), 3.0))
        p.drawPath(path)
        p.end()

    def _build(self) -> None:
        # Compact cinematic HUD: 4:5 aspect, small enough to sit beside the reactor.
        self.setMinimumSize(520, 640)
        self.setMaximumSize(700, 880)
        self.resize(600, 740)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        # -- Header ------------------------------------------------------
        header = QFrame(self)
        header.setFixedHeight(52)
        header.setStyleSheet('QFrame { background: transparent; border: none; }')
        hl = QHBoxLayout(header); hl.setContentsMargins(10, 2, 6, 2); hl.setSpacing(8)
        title = QLabel('J.A.R.V.I.S')
        title.setStyleSheet(f'color:{C.WHITE}; font:700 15pt "Orbitron"; letter-spacing:4px; background:transparent;')
        hl.addWidget(title)
        rule = QLabel('SECURE AI COMMAND INTERFACE')
        rule.setStyleSheet(f'color:{C.TEXT_DIM}; font:600 6pt "Exo 2"; letter-spacing:2px; background:transparent; padding-top:4px;')
        hl.addWidget(rule, 1, Qt.AlignmentFlag.AlignBottom)
        self._online = QLabel('●  ONLINE')
        self._online.setStyleSheet(f'color:{C.GREEN}; font:800 9pt "Exo 2"; background:transparent;')
        hl.addWidget(self._online)
        close = QPushButton('×')
        close.setFixedSize(30, 30)
        close.setToolTip('Close J.A.R.V.I.S.')
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            f'QPushButton {{ color:{C.TEXT_MED}; background:transparent; border:none; font:16pt "Exo 2"; }}'
            f'QPushButton:hover {{ color:{C.RED}; background:rgba(60,8,16,100); border-radius:6px; }}'
        )
        close.clicked.connect(self._close_jarvis)
        hl.addWidget(close)
        header.mousePressEvent = self._hdr_press
        header.mouseMoveEvent = self._hdr_move
        header.mouseReleaseEvent = self._hdr_release
        layout.addWidget(header)

        # -- Navigation tabs --------------------------------------------
        nav = QHBoxLayout(); nav.setSpacing(5)
        self._tab_btns = []
        _tab_icons = {
            'CHAT': 'chat', 'ACTIVITY': 'activity', 'TOOLS': 'tools', 'WEB': 'web',
            'MEMORY': 'memory', 'SYSTEM': 'system', 'SETTINGS': 'settings',
        }
        for index, label in enumerate(('CHAT', 'ACTIVITY', 'TOOLS', 'WEB', 'MEMORY', 'SYSTEM', 'SETTINGS')):
            b = _AngularButton(label, self, compact=True, icon=_tab_icons.get(label))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(34)
            b.clicked.connect(lambda _=False, i=index: self._on_tab(i))
            nav.addWidget(b, 1)
            self._tab_btns.append(b)
        self._tab_btns[0].setChecked(True)
        layout.addLayout(nav)

        # -- Dashboard row ----------------------------------------------
        dashboard = QFrame(self)
        dashboard.setObjectName('ReferenceDashboard')
        dashboard.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        dashboard.setStyleSheet(f'QFrame#ReferenceDashboard {{ background:rgba(2,8,16,235); border:1px solid rgba(88,214,245,85); border-radius:12px; }}')
        try:
            _chat_backdrop = _ControlCenterBackdrop(dashboard)
            _chat_backdrop.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            _chat_backdrop.setGeometry(dashboard.rect())
            _chat_backdrop.lower()
            dashboard._settings_backdrop = _chat_backdrop
            _old_dash_resize = dashboard.resizeEvent
            def _fit_chat_backdrop(ev, _dash=dashboard, _bg=_chat_backdrop, _old=_old_dash_resize):
                if _old:
                    _old(ev)
                try:
                    _bg.setGeometry(_dash.rect())
                    _bg.lower()
                except Exception:
                    pass
            dashboard.resizeEvent = _fit_chat_backdrop
        except Exception:
            pass
        dashboard.setAttribute(Qt.WidgetAttribute.WA_ClipsChildrenToShape, True) if hasattr(Qt.WidgetAttribute, 'WA_ClipsChildrenToShape') else None
        dash = QHBoxLayout(dashboard); dash.setContentsMargins(8, 8, 8, 8); dash.setSpacing(8)

        # Live value / label widgets of every HUD card, keyed so the telemetry
        # timer can write real numbers into them.
        self._hud_values = {}
        self._hud_labels = {}

        def hud_card(title_text, rows, width=176):
            card = QFrame(dashboard)
            card.setFixedWidth(width)
            card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            card.setStyleSheet(f'QFrame {{ background:rgba(3,18,30,145); border:none; border-radius:10px; }}'
                               'QLabel { background:transparent; border:none; }')
            cl = QVBoxLayout(card); cl.setContentsMargins(10, 8, 10, 8); cl.setSpacing(4)
            head = QHBoxLayout(); head.setContentsMargins(0,0,0,0); head.setSpacing(6)
            cap = QLabel(title_text)
            cap.setStyleSheet(f'color:{C.PRI}; font:900 7.8pt "Orbitron"; letter-spacing:1.4px;')
            head.addWidget(cap, 1)
            if title_text == 'SYSTEM':
                # VISION master toggle: lives in SYSTEM status now; the old
                # standalone VISION navigation tab is intentionally removed.
                self._vision_status_toggle = _AngularButton('VISION', card, compact=True, icon='eye_off')
                self._vision_status_toggle.setCheckable(True)
                self._vision_status_toggle.setFixedSize(82, 30)
                self._vision_status_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self._vision_status_toggle.setToolTip('Show / hide the live Vision camera stage')
                self._vision_status_toggle.toggled.connect(self._on_vision_toggled)
                self._vision_status_toggle.toggled.connect(
                    lambda on, b=self._vision_status_toggle: (setattr(b, '_icon', 'eye' if on else 'eye_off'), b.update())
                )
                head.addWidget(self._vision_status_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
            cl.addLayout(head)
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f'background:{C.BORDER_B}; border:none;')
            cl.addWidget(line)
            for key, name, value, icon in rows:
                row = QHBoxLayout(); row.setSpacing(5)
                ico = QLabel(icon)
                ico.setFixedWidth(16)
                ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ico.setStyleSheet(f'color:{C.PRI}; font:11pt "Segoe UI Symbol";')
                row.addWidget(ico)
                lab = QLabel(name)
                lab.setStyleSheet(f'color:{C.TEXT}; font:700 7.5pt "Rajdhani";')
                row.addWidget(lab, 1)
                val = QLabel(value)
                val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                status_col = C.GREEN if value.strip().startswith('●') else C.WHITE
                val.setStyleSheet(f'color:{status_col}; font:700 7pt "Orbitron"; letter-spacing:0.4px;')
                row.addWidget(val)
                self._hud_labels[key] = lab
                self._hud_values[key] = val
                cl.addLayout(row)
            cl.addStretch(1)
            return card

        left_wrap = QWidget(dashboard)
        left_wrap.setFixedWidth(168)
        left_wrap.setMinimumWidth(0)
        left_wrap.setMaximumWidth(168)
        left_wrap.setObjectName('VisionLeftGroup')
        left_wrap.setStyleSheet('QWidget#VisionLeftGroup { background:transparent; border:none; }')
        left_col = QVBoxLayout(left_wrap); left_col.setContentsMargins(0,0,0,0); left_col.setSpacing(7)
        left_col.addWidget(hud_card('SYSTEM', [
            ('cpu', 'CPU', '--%', '▣'), ('ram', 'RAM', '--%', '▤'),
            ('gpu', 'GPU', '--%', '◈'), ('temp', 'TEMP', '--°C', '♨')
        ]))
        left_col.addWidget(hud_card('NETWORK', [
            ('net_down', 'Down', '--', '↓'), ('net_up', 'Up', '--', '↑'),
            ('os', 'OS', '—', '▦')
        ]))
        left_col.addStretch(1)
        dash.addWidget(left_wrap, 0)
        self._vision_left_wrap = left_wrap

        center = QFrame(dashboard); center.setStyleSheet('background:transparent;border:none;')
        cc = QVBoxLayout(center); cc.setContentsMargins(2, 2, 2, 2); cc.setSpacing(2)
        visual = QStackedWidget(center)
        visual.setObjectName('VisionVisualStack')
        visual.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # The reactor logo sits on its own centred page. A fixed-size widget added
        # straight to a QStackedWidget is anchored to the stack's top-left corner,
        # so any re-layout used to drag the logo sideways.
        self._logo_page = QWidget(visual)
        self._logo_page.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._logo_page.setStyleSheet('background:transparent;border:none;')
        _logo_row = QHBoxLayout(self._logo_page)
        _logo_row.setContentsMargins(0, 0, 0, 0)
        _logo_row.setSpacing(0)
        _logo_row.addStretch(1)
        self.globe = RefinedArcLogo(self._logo_page, size=190)
        self.globe.set_graphic_opacity(100)
        self.globe.set_stroke_opacity(100)
        _logo_row.addWidget(self.globe, 0)
        _logo_row.addStretch(1)
        visual.addWidget(self._logo_page)
        # Dedicated Vision stage: control rails + cinematic camera HUD.
        self._vision_camera_stage = QWidget(visual)
        stage_l = QHBoxLayout(self._vision_camera_stage)
        stage_l.setContentsMargins(2,2,2,2); stage_l.setSpacing(6)

        # Unified left-side Vision control surface. All switches are labeled,
        # icon/emoji-free, and live inside a wheel-scrollable container.
        vision_scroll = QScrollArea(self._vision_camera_stage)
        vision_scroll.setFixedWidth(224)
        vision_scroll.setWidgetResizable(True)
        vision_scroll.setFrameShape(QFrame.Shape.NoFrame)
        vision_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        vision_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        vision_scroll.setStyleSheet(
            'QScrollArea { background:transparent; border:none; }'
            'QScrollBar:vertical, QScrollBar:horizontal { width:0px; height:0px; border:none; background:transparent; }'
            'QScrollBar::handle:vertical, QScrollBar::handle:horizontal { width:0px; height:0px; background:transparent; border:none; }'
        )
        vision_scroll.viewport().setStyleSheet('background:transparent; border:none;')

        vision_controls = QFrame()
        vision_controls.setObjectName('VisionControlScroll')
        vision_controls.setMinimumWidth(206)
        vision_controls.setStyleSheet('QFrame#VisionControlScroll { background:rgba(1,15,25,92); border:1px solid rgba(67,170,199,55); border-radius:9px; }')
        controls_l = QVBoxLayout(vision_controls)
        controls_l.setContentsMargins(8,8,8,8)
        controls_l.setSpacing(6)

        controls_head = QHBoxLayout()
        controls_head.setContentsMargins(1, 0, 1, 0)
        controls_head.setSpacing(7)
        controls_head.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        controls_title = QLabel('VISION CONTROLS')
        controls_title.setStyleSheet(f'color:{C.PRI};font:900 8.2pt "Orbitron";letter-spacing:1.05px;background:transparent;')
        controls_head.addWidget(controls_title, 1)
        exit_btn = QPushButton('EXIT')
        exit_btn.setFixedSize(62, 32)
        exit_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.setToolTip('Exit Vision mode and return to the reactor view')
        exit_btn.setStyleSheet(
            f'QPushButton {{ color:{C.TEXT_MED}; background:rgba(40,12,18,110); border:1px solid rgba(210,88,110,95); border-radius:6px; padding:0 8px; font:900 8.4pt "Exo 2"; letter-spacing:1.05px; }}'
            f'QPushButton:hover {{ color:{C.WHITE}; background:rgba(105,20,34,145); border-color:{C.RED}; }}'
            f'QPushButton:pressed {{ background:rgba(145,28,45,180); }}'
        )
        exit_btn.clicked.connect(lambda _=False: self._on_vision_toggled(False))
        controls_head.addWidget(exit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_l.addLayout(controls_head)

        self._vision_face_track = _VisionSwitch('FACE TRACK', vision_controls, tip='Follow the largest detected face')
        self._vision_face_track.setChecked(True)
        controls_l.addWidget(self._vision_face_track)


        self._vision_object_track = _VisionSwitch('OBJECT TRACK', vision_controls, tip='Highlight tracked objects when object data is available')
        controls_l.addWidget(self._vision_object_track)

        self._vision_expression = _VisionSwitch('EXPRESSION', vision_controls, tip='Enable facial expression analysis')
        controls_l.addWidget(self._vision_expression)

        self._vision_visible_name = _VisionSwitch('VISIBLE NAME', vision_controls, tip='Display the configured name for detected faces')
        self._vision_visible_name.setChecked(True)
        controls_l.addWidget(self._vision_visible_name)

        self._vision_face_zoom = _VisionSwitch('FACE ZOOM', vision_controls, tip='Smoothly zoom toward the tracked face')
        controls_l.addWidget(self._vision_face_zoom)

        self._vision_object_detection = _VisionSwitch('OBJECT DETECT', vision_controls, tip='Enable object-recognition reporting')
        self._vision_object_detection.setChecked(True)
        controls_l.addWidget(self._vision_object_detection)

        self._vision_invert = _VisionSwitch('MIRROR WEBCAM', vision_controls, tip='Mirror the camera horizontally')
        controls_l.addWidget(self._vision_invert)

        self._vision_grid_overlay = _VisionSwitch('GRID OVERLAY', vision_controls, tip='Show a subtle Vision alignment grid over the feed')
        controls_l.addWidget(self._vision_grid_overlay)

        self._vision_fps_hud = _VisionSwitch('FPS HUD', vision_controls, tip='Show the live camera frame rate')
        controls_l.addWidget(self._vision_fps_hud)

        self._vision_low_light = _VisionSwitch('LOW LIGHT BOOST', vision_controls, tip='Increase the display lift for a darker camera feed')
        controls_l.addWidget(self._vision_low_light)

        self._vision_center_reticle = _VisionSwitch('CENTER RETICLE', vision_controls, tip='Show a central alignment reticle')
        controls_l.addWidget(self._vision_center_reticle)

        self._vision_edge_outline = _VisionSwitch('EDGE OUTLINE', vision_controls, tip='Keep a clean cyan outline around the camera surface')
        self._vision_edge_outline.setChecked(True)
        controls_l.addWidget(self._vision_edge_outline)

        controls_l.addStretch(1)
        vision_scroll.setWidget(vision_controls)
        stage_l.addWidget(vision_scroll, 0)

        self._vision_control_scroll = vision_scroll
        self._vision_control_panel = vision_controls

        self._camera_view = _InlineVisionView(self._vision_camera_stage)
        self._camera_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        stage_l.addWidget(self._camera_view, 1)

        visual.addWidget(self._vision_camera_stage)
        visual.setCurrentWidget(self._logo_page)
        cc.addWidget(visual, 1)
        self._vision_visual = visual

        self.wave = _MiniWaveform(center)
        self.wave.setFixedHeight(36)
        cc.addWidget(self.wave)
        dash.addWidget(center, 1)

        right_wrap = QWidget(dashboard)
        right_wrap.setFixedWidth(168)
        right_wrap.setMinimumWidth(0)
        right_wrap.setMaximumWidth(168)
        right_wrap.setObjectName('VisionRightGroup')
        right_wrap.setStyleSheet('QWidget#VisionRightGroup { background:transparent; border:none; }')
        right_col = QVBoxLayout(right_wrap); right_col.setContentsMargins(0,0,0,0); right_col.setSpacing(7)
        right_col.addWidget(hud_card('QUICK INFO', [
            ('uptime', 'Uptime', '--:--', '◷'), ('user', 'User', '—', '●'),
            ('ip', 'IP', '--', '⌁'), ('zone', 'Zone', '—', '⌖')
        ]))
        right_col.addWidget(hud_card('ACTIVE APPS', [
            ('app0', '—', '—', '▸'), ('app1', '—', '—', '▸'), ('app2', '—', '—', '▸')
        ]))
        right_col.addStretch(1)
        dash.addWidget(right_wrap, 0)
        self._vision_right_wrap = right_wrap
        # _vision_btn remains the compatibility/master reference used by the
        # camera stream code, but the actual visible control is now SYSTEM > VISION.
        self._vision_btn = getattr(self, '_vision_status_toggle', None)
        self._vision_full_width = 168
        self._vision_anim_group = None
        self._vision_camera_connected = False
        self._vision_loader_timer = None
        try:
            self._host._cam_frame_sig.connect(self._on_local_camera_frame)
            self._vision_camera_connected = True
        except Exception:
            pass
        for _sw in (self._vision_face_track, self._vision_object_track, self._vision_visible_name,
                    self._vision_face_zoom, self._vision_expression, self._vision_object_detection,
                    self._vision_invert, self._vision_grid_overlay, self._vision_fps_hud,
                    self._vision_low_light, self._vision_center_reticle, self._vision_edge_outline):
            _sw.toggled.connect(self._sync_vision_options)
        layout.addWidget(dashboard, 0)

        # -- Chat log ----------------------------------------------------
        self.chat = _AngularTextEdit(self)
        self.chat.setReadOnly(True)
        self.chat.setMinimumHeight(200)
        self.chat.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat.setPlaceholderText('Command history will appear here.')
        self.chat.setStyleSheet(
            'QTextEdit { background:transparent; color:%s; border:none; padding:10px; font:9pt "Rajdhani"; selection-background-color:rgba(0,134,184,80); }' % C.TEXT +
            'QAbstractScrollArea { background:transparent; border:none; }'
            'QTextEdit > QWidget { background:transparent; border:none; }'
            'QScrollBar:vertical { background:transparent; width:6px; border:none; margin:2px; }'
            'QScrollBar::handle:vertical { background:rgba(30,116,142,115); border-radius:3px; min-height:26px; }'
            f'QScrollBar::handle:vertical:hover {{ background:{C.PRI}; }}'
            'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0px; }'
        )
        self._chat_entries = []
        # ── Chat header: compact toggle to hide SYS/VIS/NEWS ───────────────
        chat_hdr = QHBoxLayout()
        chat_hdr.setSpacing(6)
        _lbl = QLabel('CHAT LOG')
        _lbl.setStyleSheet(
            f'color:{C.PRI}; font:800 7pt "Orbitron"; letter-spacing:1.5px; '
            f'background:transparent;'
        )
        chat_hdr.addWidget(_lbl)
        chat_hdr.addStretch(1)
        self._hide_sys_btn = QPushButton()
        self._hide_sys_btn.setCheckable(True)
        self._hide_sys_btn.setFixedSize(16, 16)
        self._hide_sys_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hide_sys_btn.setToolTip('Hide SYS / VIS / NEWS — show only JARVIS and You')
        self._hide_sys_btn.setAccessibleName('Hide system messages toggle')
        self._hide_sys_btn.setStyleSheet(
            f'QPushButton {{ background:transparent; border:1.5px solid {C.TEXT_DIM}; border-radius:8px; }}'
            f'QPushButton:checked {{ background:{C.GREEN}; border-color:{C.GREEN}; }}'
            f'QPushButton:hover {{ border-color:{C.PRI}; }}'
        )
        try:
            cfg = _read_full_config()
            self._hide_system_chat = bool(cfg.get('hide_system_chat', False))
        except Exception:
            self._hide_system_chat = False
        self._hide_sys_btn.setChecked(self._hide_system_chat)
        self._hide_sys_btn.toggled.connect(self._toggle_hide_system)
        chat_hdr.addWidget(self._hide_sys_btn)

        self._hide_chat_lines_btn = QPushButton('LINES')
        self._hide_chat_lines_btn.setCheckable(True)
        self._hide_chat_lines_btn.setFixedHeight(22)
        self._hide_chat_lines_btn.setMinimumWidth(58)
        self._hide_chat_lines_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hide_chat_lines_btn.setToolTip('Hide or show the separator lines between chat messages')
        self._hide_chat_lines_btn.setAccessibleName('Hide chat separator lines toggle')
        self._hide_chat_lines_btn.setStyleSheet(
            f'QPushButton {{ background:rgba(1,15,25,125); color:{C.TEXT_DIM}; border:1px solid rgba(67,151,185,92); border-radius:6px; padding:2px 8px; font:800 7pt "Exo 2"; letter-spacing:1px; }}'
            f'QPushButton:hover {{ color:{C.WHITE}; border-color:{C.PRI}; }}'
            f'QPushButton:checked {{ color:{C.WHITE}; background:rgba(0,96,125,135); border-color:{C.PRI}; }}'
        )
        try:
            cfg = _read_full_config()
            self._hide_chat_lines = bool(cfg.get('hide_chat_lines', False))
        except Exception:
            self._hide_chat_lines = False
        self._hide_chat_lines_btn.setChecked(self._hide_chat_lines)
        self._hide_chat_lines_btn.toggled.connect(self._toggle_chat_lines)
        chat_hdr.addWidget(self._hide_chat_lines_btn)

        layout.insertLayout(layout.indexOf(self.chat), chat_hdr)

        layout.addWidget(self.chat, 1)

        # -- Bottom status: transparent rail, no border, date + time ----
        self.chips = QLabel('')
        self.chips.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chips.setStyleSheet(
            f'color:{C.TEXT_DIM}; background:transparent; border:none;'
            f' font:700 8pt "Share Tech Mono"; letter-spacing:1.5px; padding:2px;'
        )
        layout.addWidget(self.chips)

        # -- Input row: angular field + mic icon + send icon ------------
        row = QHBoxLayout(); row.setSpacing(6)
        self.input = _AngularLineEdit(self)
        self.input.setPlaceholderText('Ask JARVIS anything…')
        self.input.setFixedHeight(44)
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)

        self.mic_btn = _MicIconButton(self, size=22, color=C.PRI)
        self.mic_btn.setToolTip('Mute / unmute microphone (F4)')
        self.mic_btn.clicked.connect(self._toggle_mute)
        row.addWidget(self.mic_btn)

        self.send_btn = _SendIconButton(self, size=22, color=C.PRI)
        self.send_btn.setToolTip('Send command')
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        # -- Live date/time rail + frosted-glass refresh ---------------
        self._refresh_status_clock()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status_clock)
        self._status_timer.start(1000)

        # -- Live telemetry for the HUD cards --------------------------
        try:
            self._telemetry_user = str(_read_full_config().get('user_name') or '').strip() or '—'
        except Exception:
            self._telemetry_user = '—'
        self._active_apps = []
        self._apps_busy = False
        self._telemetry_ticks = 0
        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.timeout.connect(self._refresh_telemetry)
        self._telemetry_timer.start(1500)
        self._refresh_telemetry()
        self.chat.viewport().update()

    # ── HUD telemetry cards ─────────────────────────────────────────────
    def _hud_put(self, key, text, color=None) -> None:
        """Set a card value, only repainting when it actually changed."""
        val = getattr(self, '_hud_values', {}).get(key)
        if val is None:
            return
        try:
            if val.text() != text:
                val.setText(text)
            if color:
                val.setStyleSheet(
                    f'color:{color}; font:700 7pt "Orbitron"; letter-spacing:0.4px;'
                )
        except Exception:
            pass

    def _refresh_telemetry(self) -> None:
        """Feed the SYSTEM / NETWORK / QUICK INFO / ACTIVE APPS cards real data."""
        if not getattr(self, '_hud_values', None) or not self.isVisible():
            return
        self._telemetry_ticks = getattr(self, '_telemetry_ticks', 0) + 1
        try:
            s = _metrics.snapshot()
        except Exception:
            s = {}

        try:
            cpu = float(s.get('cpu', 0.0) or 0.0)
            ram = float(s.get('mem', 0.0) or 0.0)
            gpu = float(s.get('gpu', -1.0))
            tmp = float(s.get('tmp', -1.0))
            self._hud_put('cpu', f'{cpu:.0f}%', C.RED if cpu >= 85 else (C.ACC if cpu >= 65 else C.WHITE))
            self._hud_put('ram', f'{ram:.0f}%', C.RED if ram >= 85 else (C.ACC if ram >= 65 else C.WHITE))
            self._hud_put('gpu', f'{gpu:.0f}%' if gpu >= 0 else 'N/A',
                          C.WHITE if gpu >= 0 else C.TEXT_DIM)
            self._hud_put('temp', f'{tmp:.0f}°C' if tmp >= 0 else '--',
                          C.RED if tmp >= 85 else (C.WHITE if tmp >= 0 else C.TEXT_DIM))

            self._hud_put('net_down', _fmt_rate(s.get('net_down', 0.0)), C.WHITE)
            self._hud_put('net_up', _fmt_rate(s.get('net_up', 0.0)), C.WHITE)
            self._hud_put('os', platform.system() or '—', C.TEXT_MED)

            self._hud_put('uptime', _fmt_uptime(s.get('uptime', 0)), C.WHITE)
            user = str(getattr(self, '_telemetry_user', '—') or '—')
            self._hud_put('user', user if len(user) <= 10 else user[:9] + '…', C.WHITE)
            self._hud_put('ip', _local_ip(), C.WHITE)
            try:
                offset = datetime.now().astimezone().strftime('%z')
                zone = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else '—'
            except Exception:
                zone = '—'
            self._hud_put('zone', zone, C.TEXT_MED)
        except Exception:
            pass

        # Process sampling is heavier — every other tick (≈3 s) and off-thread.
        if self._telemetry_ticks % 2 == 1:
            self._poll_active_apps()
        else:
            self._render_active_apps()

    def _render_active_apps(self) -> None:
        rows = list(getattr(self, '_active_apps', []) or [])
        for i in range(3):
            key = f'app{i}'
            lab = getattr(self, '_hud_labels', {}).get(key)
            if lab is None:
                continue
            if i < len(rows):
                name, pct = rows[i]
                label = str(name)
                if len(label) > 13:
                    label = label[:12] + '…'
                try:
                    if lab.text() != label:
                        lab.setText(label)
                    lab.setToolTip(f'{name} — {pct:.0f}% CPU')
                except Exception:
                    pass
                self._hud_put(key, f'{pct:.0f}%', C.GREEN)
            else:
                try:
                    if lab.text() != '—':
                        lab.setText('—')
                    lab.setToolTip('No active application sampled yet')
                except Exception:
                    pass
                self._hud_put(key, '—', C.TEXT_DIM)

    def _poll_active_apps(self) -> None:
        """Sample the busiest processes on a worker thread (never on the UI one)."""
        if getattr(self, '_apps_busy', False):
            return
        self._apps_busy = True

        def _work():
            try:
                rows = _top_active_apps(3)
            except Exception:
                rows = []

            def _apply():
                self._apps_busy = False
                self._active_apps = rows
                self._render_active_apps()

            self.run_on_ui(_apply)

        threading.Thread(target=_work, daemon=True, name='jarvis-active-apps').start()

    def _toggle_chat_lines(self, on: bool) -> None:
        """Persist separator-line visibility and immediately rebuild the chat."""
        self._hide_chat_lines = bool(on)
        try:
            _ui_save(API_FILE, hide_chat_lines=bool(on))
        except Exception:
            pass
        try:
            entries = list(getattr(self, '_chat_entries', []))
            self._chat_entries = []
            self.chat.clear()
            for who, text, you in entries:
                self.append_msg(who, text, you=you)
        except Exception:
            pass

    def _toggle_hide_system(self, on: bool) -> None:
        """Persist the filter and immediately rebuild the visible chat."""
        self._hide_system_chat = bool(on)
        try:
            _ui_save(API_FILE, hide_system_chat=bool(on))
        except Exception:
            pass
        try:
            entries = list(getattr(self, '_chat_entries', []))
            self._chat_entries = []
            self.chat.clear()
            for who, text, you in entries:
                self.append_msg(who, text, you=you)
        except Exception:
            pass

    def _refresh_status_clock(self):
        """Bottom rail: date + live time, no background, no stroke."""
        try:
            self.chips.setText(datetime.now().strftime('%d %b %Y   ·   %I:%M %p'))
        except Exception:
            pass

    def set_metrics_text(self, text: str):
        # The refined command window's bottom rail is dedicated to date/time
        # (see _refresh_status_clock). The base _CommandWindow.set_metrics_text
        # also targets self.chips with CPU/RAM/GPU/TEMP text on its own timer;
        # left inherited, the two would keep overwriting each other every
        # second, making the bottom text flicker between the two. Live system
        # stats already live in the SYSTEM HUD card up top, so this is a no-op
        # here by design.
        pass

    def _on_vision_toggled(self, checked: bool) -> None:
        try:
            _sfx('click')
        except Exception:
            pass

        checked = bool(checked)
        try:
            # Keep both entry points synchronized without recursively firing them.
            for btn in (getattr(self, '_vision_btn', None), getattr(self, '_vision_status_toggle', None)):
                if btn is not None:
                    btn.blockSignals(True)
                    btn.setChecked(checked)
                    btn.blockSignals(False)

            visual = getattr(self, '_vision_visual', None)
            globe = getattr(self, 'globe', None)
            cam = getattr(self, '_camera_view', None)

            if checked:
                # Show the camera page FIRST so the loader is visible immediately.
                if visual is not None and cam is not None:
                    visual.setCurrentWidget(self._vision_camera_stage)
                    self._fade_widget(cam, 1.0, 180)
                    self._vision_camera_stage.show()

                if cam is not None and hasattr(cam, 'begin_loading'):
                    cam.begin_loading()

                # Coordinated HUD transition: panels slide out, logo fades out.
                self._animate_vision_layout(True)

                try:
                    host = self._host
                    host._chat_vision_inline = True
                    # Close both legacy floating camera surfaces BEFORE starting/reusing the stream.
                    for name in ('_cam_preview', '_cam_live_lbl'):
                        w = getattr(host, name, None)
                        if w is not None:
                            w.hide()
                    host.start_camera_stream()
                    host.write_log('VIS: Chat Vision — connecting camera…')
                except Exception as exc:
                    if cam is not None:
                        cam._loading = True
                        cam._status = 'VISION CAMERA · START ERROR'
                        cam._loader.hide()
                        cam.update()
                    try:
                        self._host.write_log(f'ERR: Vision camera start — {exc}')
                    except Exception:
                        pass
            else:
                # Stop the source first so the old camera surfaces cannot steal frames.
                try:
                    self._host._chat_vision_inline = False
                    self._host.stop_camera_stream()
                    for name in ('_cam_preview', '_cam_live_lbl'):
                        w = getattr(self._host, name, None)
                        if w is not None:
                            w.hide()
                except Exception:
                    pass

                # Reverse the coordinated HUD transition: panels slide back in, logo fades in.
                self._animate_vision_layout(False)

                if cam is not None:
                    try:
                        if hasattr(cam, '_loading_timer'):
                            cam._loading_timer.stop()
                        cam._loader.hide()
                    except Exception:
                        pass

                # Restore logo page and fade it back in.
                if visual is not None and globe is not None:
                    visual.setCurrentWidget(getattr(self, '_logo_page', globe))
                self._fade_widget(cam, 0.0, 150)

                try:
                    self._host.write_log('VIS: Chat Vision disabled.')
                except Exception:
                    pass
        except Exception as exc:
            try:
                self._vision_btn.blockSignals(True)
                self._vision_btn.setChecked(not checked)
                self._vision_btn.blockSignals(False)
            except Exception:
                pass
            try:
                self._host.write_log(f'ERR: Vision — {exc}')
            except Exception:
                pass

    def _sync_vision_options(self, *_):
        try:
            self._camera_view.set_options(
                face_track=self._vision_face_track.isChecked(),
                object_track=self._vision_object_track.isChecked(),
                visible_name=self._vision_visible_name.isChecked(),
                face_zoom=self._vision_face_zoom.isChecked(),
                expression=self._vision_expression.isChecked(),
                object_detection=self._vision_object_detection.isChecked(),
                invert=self._vision_invert.isChecked(),
                grid_overlay=self._vision_grid_overlay.isChecked(),
                fps_hud=self._vision_fps_hud.isChecked(),
                low_light=self._vision_low_light.isChecked(),
                center_reticle=self._vision_center_reticle.isChecked(),
                edge_outline=self._vision_edge_outline.isChecked(),
            )
        except Exception:
            pass

    def _fade_widget(self, widget, target: float, duration: int = 260):
        if widget is None:
            return
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', widget)
        anim.setDuration(duration)
        anim.setStartValue(float(effect.opacity()))
        anim.setEndValue(float(target))
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        setattr(widget, '_jarvis_fade_animation', anim)

    def _animate_vision_layout(self, enabled: bool):
        """Coordinated HUD slide transition when Vision is toggled.

        * System + Network panels (left_wrap) slide LEFT toward the edge.
        * Quick Info + Active Apps panels (right_wrap) slide RIGHT toward the edge.
        * The main reactor logo fades out during the transition and fades
          back in when returning to the default view.
        * All elements use the same duration and cubic easing so the whole
          transition reads as one coordinated HUD movement.
        """
        DURATION = 340
        left_wrap  = getattr(self, '_vision_left_wrap', None)
        right_wrap = getattr(self, '_vision_right_wrap', None)
        globe      = getattr(self, 'globe', None)

        full_w = getattr(self, '_vision_full_width', 168)

        if enabled:
            # 1) Fade out the logo first so it disappears as the panels slide.
            if globe is not None:
                self._fade_widget(globe, 0.0, DURATION)
            # 2) Slide left panels toward the left edge (width shrinks to 0).
            if left_wrap is not None:
                self._anim_width(left_wrap, 0, DURATION)
            # 3) Slide right panels toward the right edge (width shrinks to 0).
            if right_wrap is not None:
                self._anim_width(right_wrap, 0, DURATION)
        else:
            # Reverse: panels slide back in, logo fades back in.
            if left_wrap is not None:
                self._anim_width(left_wrap, full_w, DURATION)
            if right_wrap is not None:
                self._anim_width(right_wrap, full_w, DURATION)
            if globe is not None:
                self._fade_widget(globe, 1.0, DURATION)

    def _anim_width(self, widget, target_w: int, duration: int):
        """Animate a widget's maximumWidth so the layout slides it away."""
        if widget is None:
            return
        anim = QPropertyAnimation(widget, b'maximumWidth', widget)
        anim.setDuration(duration)
        anim.setStartValue(widget.maximumWidth())
        anim.setEndValue(target_w)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        setattr(widget, '_jarvis_width_animation', anim)

    def _on_local_camera_frame(self, data: bytes) -> None:
        if not getattr(self, '_vision_btn', None) or not self._vision_btn.isChecked():
            return
        try:
            if hasattr(self._camera_view, 'set_frame_bytes'):
                self._camera_view.set_frame_bytes(data)
            # Face detection is already available in this module via OpenCV/Haar.
            # Run it periodically off the UI thread and feed only the inline Vision HUD.
            n = getattr(self, '_inline_vision_n', 0) + 1
            self._inline_vision_n = n
            if n % 8 != 0 or getattr(self, '_inline_face_busy', False):
                return
            self._inline_face_busy = True
            jpg = bytes(data)
            def _detect():
                try:
                    faces = _detect_faces(jpg)
                except Exception:
                    faces = []
                def _apply():
                    self._inline_face_busy = False
                    try:
                        if hasattr(self._camera_view, 'set_faces'):
                            self._camera_view.set_faces(faces)
                    except Exception:
                        pass
                self.run_on_ui(_apply)
            threading.Thread(target=_detect, daemon=True).start()
        except Exception:
            pass

    def _on_tab(self, index: int) -> None:
        try:
            _sfx('click')
        except Exception:
            pass

        # QPushButton (checkable) already flipped its own state by the time
        # this slot runs. If the active tab was clicked again, it's now
        # unchecked — clear any other checked state and stop.
        btn = self._tab_btns[index]
        if not btn.isChecked():
            for b in self._tab_btns:
                if b is not btn:
                    b.setChecked(False)
            return

        # Otherwise enforce exclusivity: only this button stays checked.
        for i, b in enumerate(self._tab_btns):
            b.setChecked(i == index)

        # Dispatch the tab action.
        try:
            h = self._host
            if index == 0:
                self.input.setFocus()
            elif index == 1:
                h._open_activity_panel()
            elif index == 2:
                h._open_tools_panel()
            elif index == 3:
                h._open_webview_panel()
            elif index == 4:
                h._open_memory_panel()
            elif index == 5:
                h._open_world_monitor()
            elif index == 6:
                h._open_full_settings()
        except Exception:
            pass

    def paintEvent(self, event) -> None:
        """Solid translucent frame — no desktop capture and no stale pixels."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        cut = 16
        path = QPainterPath()
        path.moveTo(rect.left() + cut, rect.top())
        path.lineTo(rect.right() - cut, rect.top())
        path.lineTo(rect.right(), rect.top() + cut)
        path.lineTo(rect.right(), rect.bottom() - cut)
        path.lineTo(rect.right() - cut, rect.bottom())
        path.lineTo(rect.left() + cut, rect.bottom())
        path.lineTo(rect.left(), rect.bottom() - cut)
        path.lineTo(rect.left(), rect.top() + cut)
        path.closeSubpath()

        # Settings-style technological field: layered blue-black surfaces,
        # atmospheric cyan glow, fine grid, targeting arcs and diagnostic lines.
        tint = QLinearGradient(rect.topLeft(), rect.bottomRight())
        tint.setColorAt(0.0, QColor(5, 34, 50, 238))
        tint.setColorAt(0.48, QColor(2, 8, 16, 246))
        tint.setColorAt(1.0, QColor(4, 30, 44, 234))
        painter.fillPath(path, QBrush(tint))

        glow = QRadialGradient(QPointF(rect.width()*0.80, rect.height()*0.14), max(rect.width(), rect.height())*0.72)
        glow.setColorAt(0.0, QColor(86, 214, 245, 42))
        glow.setColorAt(0.45, QColor(24, 130, 170, 14))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillPath(path, QBrush(glow))

        warm = QRadialGradient(QPointF(rect.width()*0.10, rect.height()*0.92), max(rect.width(), rect.height())*0.50)
        warm.setColorAt(0.0, QColor(244, 178, 74, 12))
        warm.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillPath(path, QBrush(warm))

        painter.setClipPath(path)
        painter.setPen(QPen(QColor(60, 156, 190, 20), 1))
        for x in range(0, int(rect.width()) + 28, 28):
            painter.drawLine(x, 0, x, int(rect.height()))
        for y in range(0, int(rect.height()) + 28, 28):
            painter.drawLine(0, y, int(rect.width()), y)

        painter.setPen(QPen(QColor(88, 214, 245, 28), 1))
        for x in range(0, int(rect.width()) + 84, 84):
            painter.drawLine(x, 0, x, int(rect.height()))
        for y in range(0, int(rect.height()) + 84, 84):
            painter.drawLine(0, y, int(rect.width()), y)

        painter.setPen(QPen(QColor(88, 214, 245, 26), 1))
        painter.drawLine(int(rect.width()*0.08), int(rect.height()*0.78), int(rect.width()*0.92), int(rect.height()*0.12))
        painter.drawLine(int(rect.width()*0.02), int(rect.height()*0.26), int(rect.width()*0.72), int(rect.height()*0.98))

        ring = min(rect.width(), rect.height()) * 0.43
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(88, 214, 245, 18), 1))
        painter.drawEllipse(QPointF(rect.width()*0.78, rect.height()*0.76), ring, ring)
        painter.setPen(QPen(QColor(244, 178, 74, 18), 1))
        painter.drawArc(QRectF(rect.width()*0.56, rect.height()*0.50, ring*2, ring*2), 25*16, 90*16)

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(88, 214, 245, 160), 1.15))
        painter.drawPath(path)
        painter.end()


_ArcLogo = RefinedArcLogo
_ArcLogoButton = RefinedArcLogoButton
_CommandWindow = RefinedCommandWindow


class _HudPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self._resize_origin = None
        self._resize_geom = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setObjectName('HudPanel')

    def _in_resize_zone(self, pos):
        r = self.rect()
        return pos.x() >= r.width() - 18 and pos.y() >= r.height() - 18

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._in_resize_zone(e.position()):
            self._resize_origin = e.globalPosition().toPoint()
            self._resize_geom = self.geometry()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resize_origin is not None and e.buttons() & Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._resize_origin
            g = self._resize_geom
            self.resize(max(260, g.width() + delta.x()), max(180, g.height() + delta.y()))
            e.accept()
            return
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._in_resize_zone(e.position()) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._resize_origin = None
            self._resize_geom = None
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        # The settings-style backdrop supplies the visual field.
        # Keep the module shell itself nearly borderless so nested cards do not
        # create the stacked/messy "box inside box" appearance.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = 14.0

        # Soft outer cyan glow.
        for width, alpha in ((7.0, 18), (4.0, 30), (2.2, 55)):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(86, 214, 245, alpha), width))
            p.drawRoundedRect(rect, radius, radius)

        # Single crisp edge.
        p.setPen(QPen(QColor(88, 214, 245, 125), 1.0))
        p.drawRoundedRect(rect, radius, radius)
        p.end()
        super().paintEvent(e)


class _IconOnlyButton(QPushButton):
    def __init__(self, kind='send', parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setText('')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(34, 30)
        self.setFlat(True)
        self.setMouseTracking(True)
        self.setStyleSheet('background:transparent;border:none;padding:0;')

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QColor(C.WHITE if self._hover else C.TEXT_MED)
        p.setPen(QPen(c, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        r = self.rect()
        cx, cy = r.center().x(), r.center().y()
        if self.kind == 'send':
            path = QPainterPath()
            path.moveTo(cx - 8, cy - 5)
            path.lineTo(cx - 1, cy - 5)
            path.lineTo(cx - 1, cy - 9)
            path.lineTo(cx + 8, cy)
            path.lineTo(cx - 1, cy + 9)
            path.lineTo(cx - 1, cy + 5)
            path.lineTo(cx - 8, cy + 5)
            path.closeSubpath()
            p.drawPath(path)
        else:
            p.drawLine(cx - 10, cy - 10, cx + 10, cy + 10)
            p.drawRoundedRect(QRectF(cx - 4, cy - 9, 8, 12), 4, 4)
            p.drawArc(QRectF(cx - 8, cy - 1, 16, 12), 200 * 16, 140 * 16)
            p.drawLine(cx, cy + 11, cx, cy + 14)
        p.end()


class _GlowButton(QPushButton):
    def __init__(self, text='', icon_text='', compact=False, parent=None):
        label = f'{icon_text}  {text}' if icon_text else text
        super().__init__(label, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34 if compact else 40)
        self._hover = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)
        try:
            _sfx('hover')
        except Exception:
            pass

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = 7.0
        if self.isDown():
            fill = QBrush(QColor(0, 154, 199, 180))
        elif self._hover:
            fill = QBrush(QColor(0, 125, 165, 120))
        else:
            fill = QBrush(QColor(1, 17, 27, 150))
        p.setBrush(fill)
        p.setPen(QPen(QColor(66, 199, 239, 255) if (self._hover or self.hasFocus()) else QColor(67, 151, 185, 100), 1.2))
        p.drawRoundedRect(r, radius, radius)
        p.setPen(QPen(QColor(233, 252, 255, 46 if self._hover else 26), 1.0))
        p.drawLine(QPointF(r.left() + radius + 2, r.top() + 2.5), QPointF(r.right() - radius - 2, r.top() + 2.5))
        p.setPen(QPen(QColor(227, 251, 255, 255) if self._hover else QColor(168, 239, 255, 255), 1))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class _GlowSquareButton(QPushButton):
    def __init__(self, text='×', parent=None, size=26):
        super().__init__(text, parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = 7.0
        p.setBrush(QBrush(QColor(0, 125, 165, 120) if self._hover else QColor(1, 17, 27, 150)))
        p.setPen(QPen(QColor(66, 199, 239, 255) if self._hover else QColor(67, 151, 185, 100), 1.2))
        p.drawRoundedRect(r, radius, radius)
        p.setPen(QPen(QColor(255, 120, 146, 255) if (self._hover and self.text().strip() in ('×', '✕')) else (QColor(227, 251, 255, 255) if self._hover else QColor(168, 239, 255, 255)), 1))
        p.setFont(QFont('Rajdhani', 11, QFont.Weight.Bold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


def _hud_field_css():
    return _theme_field_css()


class RefinedMeter(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background:rgba(1,17,27,120);"
            " border:1px solid rgba(67,151,185,70); border-radius:8px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self._label = QLabel(label.upper())
        self._label.setStyleSheet("color:#5796ad;font:700 7pt 'Exo 2';background:transparent;")
        top.addWidget(self._label, 1)
        self._value = QLabel('--')
        self._value.setStyleSheet("color:#9ae8ff;font:700 8pt 'Orbitron';background:transparent;")
        top.addWidget(self._value)
        lay.addLayout(top)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(7)
        self._apply_bar_style('#42c7ef')
        lay.addWidget(self._bar)
        self._anim = None

    def _apply_bar_style(self, color: str) -> None:
        self._bar.setStyleSheet(
            "QProgressBar { background:rgba(0,20,30,180); border:none; border-radius:3px; }"
            f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}"
        )

    def set_value(self, pct: float, text: str = '') -> None:
        try:
            pct = max(0.0, min(100.0, float(pct)))
        except Exception:
            pct = 0.0
        self._apply_bar_style(_theme_meter_color(pct))
        if text:
            self._value.setText(str(text))
        try:
            if self._anim is not None:
                self._anim.stop()
        except Exception:
            pass
        try:
            self._anim = QPropertyAnimation(self._bar, b'value', self)
            self._anim.setDuration(600)
            self._anim.setStartValue(self._bar.value())
            self._anim.setEndValue(int(round(pct)))
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except Exception:
            try:
                self._bar.setValue(int(round(pct)))
            except Exception:
                pass


class WebViewPane(QFrame):
    def __init__(self, parent=None, home='https://www.google.com', use_engine=True):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        self._home = home or 'https://www.google.com'
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        bar = QHBoxLayout(); bar.setSpacing(6)
        self._url = QLineEdit(self._home)
        self._url.setPlaceholderText('Search or type a URL…')
        self._url.setStyleSheet(_hud_field_css())
        self._url.setMinimumHeight(36)
        self._url.returnPressed.connect(self.navigate)
        bar.addWidget(self._url, 1)
        go = _GlowButton('✓', '', compact=True)
        go.setMinimumHeight(36); go.setMinimumWidth(44)
        go.setToolTip('Go')
        go.clicked.connect(self.navigate)
        bar.addWidget(go)
        lay.addLayout(bar)
        self._engine_toggle = QCheckBox('INTERNAL ENGINE')
        self._engine_toggle.setChecked(bool(use_engine))
        self._engine_toggle.setVisible(False)
        self._engine_toggle.toggled.connect(self._on_engine_toggle)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(5)
        self._progress.setStyleSheet(
            "QProgressBar { background:rgba(0,20,30,180); border:none; border-radius:2px; }"
            "QProgressBar::chunk { background:#42c7ef; border-radius:2px; }"
        )
        self._progress.hide()
        lay.addWidget(self._progress)
        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#ff7892;font:700 7pt 'Exo 2';background:transparent;" + _theme_icon_font_css())
        self._status.hide()
        lay.addWidget(self._status)
        self._engine = None
        self._engine_ready = False
        self._fallback = QTextEdit(self)
        self._fallback.setReadOnly(True)
        self._fallback.setStyleSheet(_hud_field_css())
        self._fallback.setHtml(
            '<p style="color:#5ab8cc">Embedded WebView is unavailable or disabled. '
            'Use GO to open the URL in your system browser, or install PyQt6-WebEngine.</p>'
        )
        self._fallback.hide()
        if _WEB_OK and QWebEngineView is not None:
            QTimer.singleShot(0, lambda: self._set_engine_enabled(self._engine_toggle.isChecked()))
        else:
            self._set_engine_enabled(False)

    def _on_engine_toggle(self, enabled):
        self._set_engine_enabled(enabled)
        try:
            cfg = _ui_load(API_FILE)
            feats = dict(cfg.get('features', {})) if isinstance(cfg.get('features', {}), dict) else {}
            feats['webview_engine'] = bool(enabled)
            _ui_save(API_FILE, features=feats)
        except Exception:
            pass

    def _ensure_web_engine(self) -> bool:
        if self._engine is not None:
            return True
        if not (_WEB_OK and QWebEngineView is not None):
            self._set_engine_enabled(False)
            return False
        try:
            self._engine = QWebEngineView(self)
            # Settings → WEB → "Allow JavaScript in Webview". Defaults to on
            # when the key is absent, so existing installs behave as before.
            _js = True
            try:
                _cfg = _ui_load(API_FILE)
                _js = bool((_cfg.get('features', {}) or {}).get('webview_js', True))
            except Exception:
                _js = True
            self._engine.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled, _js)
            try:
                self._engine.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
                )
            except Exception:
                pass
            self._engine.urlChanged.connect(self._on_url_changed)
            self._engine.titleChanged.connect(lambda t: self._status.setText(f'● {t[:64]}'))
            self._engine.loadStarted.connect(self._on_load_started)
            self._engine.loadProgress.connect(self._on_load_progress)
            self._engine.loadFinished.connect(self._on_load_finished)
            self.layout().addWidget(self._engine, 1)
            self._engine_ready = True
            self._set_engine_enabled(self._engine_toggle.isChecked())
            return True
        except Exception as exc:
            self._engine = None
            self._status.setText(f'WEBVIEW ERROR · {exc}')
            return False

    def _set_engine_enabled(self, enabled):
        enabled = bool(enabled) and getattr(self, '_engine', None) is not None
        if getattr(self, '_engine', None) is not None:
            self._engine.setVisible(enabled)
        self._fallback.setVisible(not enabled)
        if self._engine_toggle.isChecked() and self._engine is None:
            self._engine_toggle.setToolTip('Install PyQt6-WebEngine to enable the embedded browser.')
            self._status.setText('WEBVIEW · ENGINE UNAVAILABLE')

    @property
    def engine_enabled(self):
        if getattr(self, '_engine', None) is None and self._engine_toggle.isChecked():
            self._ensure_web_engine()
        return bool(self._engine is not None and self._engine_toggle.isChecked())

    def _normalize(self, raw: str) -> str:
        raw = (raw or '').strip()
        if not raw:
            return self._home
        if ' ' in raw and '://' not in raw:
            from urllib.parse import quote_plus
            return 'https://www.google.com/search?q=' + quote_plus(raw)
        if not raw.startswith(('http://', 'https://')):
            raw = 'https://' + raw
        return raw

    def _on_url_changed(self, u) -> None:
        try:
            txt = u.toString()
            self._url.setText(txt)
            host = QUrl(txt).host() or '—'
            self._source.setText(f'SOURCE · {host[:40]}')
        except Exception:
            pass

    def _on_load_started(self) -> None:
        try:
            self._progress.setValue(0)
            self._progress.show()
            self._status.setText('◌ LOADING…')
        except Exception:
            pass

    def _on_load_progress(self, pct: int) -> None:
        try:
            self._progress.setValue(max(0, min(100, int(pct))))
            self._status.setText(f'◌ LOADING… {int(pct)}%')
        except Exception:
            pass

    def _on_load_finished(self, ok: bool) -> None:
        try:
            self._progress.hide()
        except Exception:
            pass
        self._status.setText('● READY' if ok else '✕ LOAD ERROR')
        try:
            self._status.setStyleSheet(
                "color:#7affc3;font:700 7pt 'Exo 2';background:transparent;"
                if ok else "color:#ff7892;font:700 7pt 'Exo 2';background:transparent;")
        except Exception:
            pass

    def navigate(self):
        url = self._normalize(self._url.text())
        self._url.setText(url)
        if self._engine_toggle.isChecked() and self._engine is None:
            self._ensure_web_engine()
        if self.engine_enabled:
            self._on_load_started()
            self._engine.setUrl(QUrl(url))
        else:
            # Settings → WEB → "Open unknown schemes in the system browser".
            # When it is off the request is reported, not silently handed to
            # the desktop browser.
            _external = True
            try:
                _cfg = _ui_load(API_FILE)
                _external = bool((_cfg.get('features', {}) or {}).get('webview_external', True))
            except Exception:
                _external = True
            if not _external:
                self._status.setText('WEBVIEW · EXTERNAL OPENING DISABLED IN SETTINGS')
                return
            try:
                import webbrowser; webbrowser.open(url)
                self._status.setText(f'OPENED SYSTEM BROWSER · {url[:48]}')
            except Exception as exc:
                self._status.setText(f'WEBVIEW ERROR · {exc}')

    def back(self):
        if self.engine_enabled:
            self._engine.back()

    def forward(self):
        if self.engine_enabled:
            try:
                self._engine.forward()
            except Exception:
                pass

    def reload(self):
        if self._engine_toggle.isChecked() and self._engine is None:
            self._ensure_web_engine()
        if self.engine_enabled:
            self._on_load_started()
            self._engine.reload()
        else:
            self.navigate()

    def go_home(self):
        self._url.setText(self._home); self.navigate()


class WebTaskPane(QFrame):
    task_run = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        self._tasks = []
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(7)
        intro = QLabel('WEB TASK QUEUE  //  SEARCH · NEWS · RESEARCH · BROWSE')
        intro.setStyleSheet(f'color:{C.PRI};font:800 8pt "Exo 2";background:transparent;')
        lay.addWidget(intro)
        form = QGridLayout(); form.setHorizontalSpacing(7); form.setVerticalSpacing(6)
        self._kind = QComboBox(); self._kind.addItems(['search', 'news', 'research', 'price', 'browse']); self._kind.setStyleSheet(_hud_field_css())
        self._query = QLineEdit(); self._query.setPlaceholderText('Query, URL, or research brief…'); self._query.setStyleSheet(_hud_field_css())
        self._query.returnPressed.connect(self.add_task)
        form.addWidget(QLabel('MODE'), 0, 0); form.addWidget(self._kind, 0, 1)
        form.addWidget(QLabel('INPUT'), 1, 0); form.addWidget(self._query, 1, 1)
        lay.addLayout(form)
        row = QHBoxLayout(); row.setSpacing(6)
        add = _GlowButton('ADD TASK', '＋', compact=True); add.clicked.connect(self.add_task); row.addWidget(add)
        run = _GlowButton('RUN SELECTED', '▸', compact=True); run.clicked.connect(self.run_selected); row.addWidget(run)
        run_all = _GlowButton('RUN ALL', '▶', compact=True); run_all.clicked.connect(self.run_all); row.addWidget(run_all)
        clear = _GlowButton('CLEAR', '×', compact=True); clear.clicked.connect(self.clear_done); row.addWidget(clear)
        lay.addLayout(row)
        self._list = QListWidget(); self._list.setStyleSheet(_hud_field_css()); self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self._list, 1)
        self._log = QLabel('0 queued · idle')
        self._log.setStyleSheet(f'color:{C.TEXT_MED};font:700 7pt "Exo 2";background:transparent;')
        lay.addWidget(self._log)

    def add_task(self):
        q = self._query.text().strip()
        if not q:
            return
        kind = self._kind.currentText()
        item = {'kind': kind, 'query': q, 'state': 'QUEUED'}
        self._tasks.append(item)
        self._query.clear()
        self._refresh()

    def _refresh(self):
        self._list.clear()
        for i, t in enumerate(self._tasks):
            self._list.addItem(f'{i+1:02d}  [{t["state"]}]  {t["kind"].upper()}  ·  {t["query"][:80]}')
        queued = sum(1 for t in self._tasks if t['state'] == 'QUEUED')
        self._log.setText(f'{len(self._tasks)} tasks · {queued} queued')

    def _command_for(self, task):
        k, q = task['kind'], task['query']
        if k == 'browse':
            return f'Open this URL and summarize the page: {q}'
        if k == 'news':
            return f'Search the latest news about: {q}'
        if k == 'research':
            return f'Research this topic in depth and report sources: {q}'
        if k == 'price':
            return f'Find current prices and compare options for: {q}'
        return f'Search the web for: {q}'

    def run_selected(self):
        row = self._list.currentRow()
        if row < 0 or row >= len(self._tasks):
            if self._tasks:
                row = 0
            else:
                self.add_task(); row = len(self._tasks) - 1
                if row < 0:
                    return
        self._run_index(row)

    def run_all(self):
        if self._query.text().strip():
            self.add_task()
        for i, t in enumerate(self._tasks):
            if t['state'] == 'QUEUED':
                self._run_index(i)

    def _run_index(self, i):
        task = self._tasks[i]
        task['state'] = 'RUNNING'
        self._refresh()
        cmd = self._command_for(task)
        self.task_run.emit(cmd)
        if task['kind'] == 'browse':
            self.task_run.emit('__webview__:' + task['query'])
        task['state'] = 'SENT'
        self._refresh()

    def clear_done(self):
        self._tasks = [t for t in self._tasks if t['state'] == 'QUEUED']
        self._refresh()


class WorldMonitorPane(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        self._headlines = ['Scanning world feeds…']
        self._city = 'Local'
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(7)
        hdr = QLabel('SYSTEM  //  LIVE TELEMETRY')
        hdr.setStyleSheet(_theme_section_css())
        lay.addWidget(hdr)
        clocks = QGridLayout(); clocks.setHorizontalSpacing(10); clocks.setVerticalSpacing(4)
        self._clock_labels = {}
        zones = [('LOCAL', None), ('UTC', 0), ('LONDON', 0), ('NEW YORK', -4), ('TOKYO', 9), ('MANILA', 8)]
        for i, (name, offset) in enumerate(zones):
            cap = QLabel(name); cap.setStyleSheet("color:#5796ad;font:700 7pt 'Exo 2';background:transparent;")
            val = QLabel('--:--:--'); val.setStyleSheet("color:#9ae8ff;font:800 11pt 'Orbitron';background:transparent;")
            clocks.addWidget(cap, 0, i); clocks.addWidget(val, 1, i); self._clock_labels[name] = (val, offset)
        lay.addLayout(clocks)
        meters = QGridLayout(); meters.setHorizontalSpacing(8); meters.setVerticalSpacing(8)
        self._meter_cpu = RefinedMeter('CPU'); self._meter_ram = RefinedMeter('RAM')
        self._meter_gpu = RefinedMeter('GPU'); self._meter_disk = RefinedMeter('DISK')
        meters.addWidget(self._meter_cpu, 0, 0); meters.addWidget(self._meter_ram, 0, 1)
        meters.addWidget(self._meter_gpu, 1, 0); meters.addWidget(self._meter_disk, 1, 1)
        lay.addLayout(meters)
        chips = QHBoxLayout(); chips.setSpacing(6)
        self._m_temp = QLabel('TEMP --'); self._m_net = QLabel('NET --'); self._m_batt = QLabel('BATT --')
        self._m_up = QLabel('UP --'); self._m_procs = QLabel('PROCS --')
        for w in (self._m_temp, self._m_net, self._m_batt, self._m_up, self._m_procs):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w.setStyleSheet(_theme_chip_css() + "font:700 7pt 'Exo 2';")
            chips.addWidget(w, 1)
        lay.addLayout(chips)
        feeds_lbl = QLabel('WORLD FEEDS  //  LIVE HEADLINES')
        feeds_lbl.setStyleSheet(_theme_section_css())
        lay.addWidget(feeds_lbl)
        self._globe = QLabel()
        self._globe.setMinimumHeight(118)
        self._globe.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._globe.setStyleSheet(_theme_card_css() + "color:#9ae8ff;")
        lay.addWidget(self._globe)
        self._news = QListWidget(); self._news.setStyleSheet(_hud_field_css()); self._news.setMinimumHeight(140)
        lay.addWidget(self._news, 1)
        row = QHBoxLayout(); row.setSpacing(6)
        refresh = _GlowButton('REFRESH FEEDS', '↻', compact=True); refresh.clicked.connect(self.refresh_feeds); row.addWidget(refresh)
        self._status = QLabel('● MONITOR ONLINE'); self._status.setStyleSheet("color:#7affc3;font:700 7pt 'Exo 2';background:transparent;" + _theme_icon_font_css())
        row.addWidget(self._status, 1); lay.addLayout(row)
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(1000)
        self._tick(); QTimer.singleShot(400, self.refresh_feeds)

    def _tick(self):
        now = datetime.now()
        utc = datetime.now(timezone.utc)
        for name, (lab, offset) in self._clock_labels.items():
            if offset is None:
                lab.setText(now.strftime('%H:%M:%S'))
            else:
                lab.setText((utc + timedelta(hours=offset)).strftime('%H:%M:%S'))
        try:
            s = _metrics.snapshot()
            gpu_v = float(s.get('gpu', -1))
            gpu_t = f'{gpu_v:.0f}%' if gpu_v >= 0 else 'N/A'
            self._meter_cpu.set_value(s.get('cpu', 0), f"{s.get('cpu', 0):.0f}%")
            self._meter_ram.set_value(s.get('mem', 0), f"{s.get('mem', 0):.0f}%")
            self._meter_gpu.set_value(gpu_v if gpu_v >= 0 else 0, gpu_t)
            self._meter_disk.set_value(s.get('disk', 0), f"{s.get('disk', 0):.0f}%")
            tmp_v = s.get('tmp', -1)
            self._m_temp.setText(f"TEMP {tmp_v:.0f}°C" if isinstance(tmp_v, (int, float)) and tmp_v >= 0 else 'TEMP --')
            net_v = s.get('net', 0)
            self._m_net.setText(f'NET {net_v:.0f} KB/s' if isinstance(net_v, (int, float)) else 'NET --')
            batt_v = s.get('batt', -1)
            if isinstance(batt_v, (int, float)) and batt_v >= 0:
                plug = '⚡' if s.get('plugged') else ''
                self._m_batt.setText(f'BATT {batt_v:.0f}%{plug}')
            else:
                self._m_batt.setText('BATT --')
            self._m_up.setText(f"UP {_fmt_uptime(s.get('uptime', 0))}")
            self._m_procs.setText(f"PROCS {int(s.get('procs', 0) or 0)}")
        except Exception:
            pass
        self._paint_globe()

    def _paint_globe(self):
        w, h = max(320, self._globe.width()), max(110, self._globe.height())
        px = QPixmap(w, h); px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy, rad = w * 0.22, h * 0.5, min(w, h) * 0.38
        p.setPen(QPen(qcol(C.PRI, 80), 1)); p.setBrush(QBrush(qcol(C.PRI_GHO, 90))); p.drawEllipse(QPointF(cx, cy), rad, rad)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in (0.45, 0.72, 1.0):
            p.drawEllipse(QPointF(cx, cy), rad * k, rad * k)
        p.drawLine(QPointF(cx - rad, cy), QPointF(cx + rad, cy))
        p.drawLine(QPointF(cx, cy - rad), QPointF(cx, cy + rad))
        pulse = (time.time() % 4.0) / 4.0
        p.setPen(QPen(qcol(C.ACC, 180), 2)); p.drawEllipse(QPointF(cx + rad * 0.35, cy - rad * 0.18), 3 + pulse * 4, 3 + pulse * 4)
        p.setPen(qcol(C.TEXT)); p.setFont(QFont('Exo 2', 8, QFont.Weight.Bold))
        p.drawText(QRectF(w * 0.46, 10, w * 0.52, h - 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'WORLD GRID ACTIVE\nLAT/LON SWEEP  {pulse*360:.0f}°\nFEEDS  {len(self._headlines)}\nNODE  {self._city}')
        p.end(); self._globe.setPixmap(px)

    def refresh_feeds(self):
        self._status.setText('SCANNING FEEDS…')
        threading.Thread(target=self._fetch_feeds, daemon=True).start()

    def _fetch_feeds(self):
        headlines = []
        try:
            req = urllib.request.Request(
                'https://feeds.bbci.co.uk/news/world/rss.xml',
                headers={'User-Agent': 'JARVIS-WorldMonitor/1.0'},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml = resp.read().decode('utf-8', errors='ignore')
            import re
            headlines = [re.sub(r'<[^>]+>', '', t).strip() for t in re.findall(r'<title>(.*?)</title>', xml, re.I | re.S)][1:12]
        except Exception as exc:
            headlines = [f'Feed offline — {exc}', 'Using local telemetry only.']
        if not headlines:
            headlines = ['No headlines returned.']
        self._headlines = headlines
        QTimer.singleShot(0, self._apply_feeds)

    def _apply_feeds(self):
        self._news.clear()
        for h in self._headlines:
            self._news.addItem('▸  ' + h)
        self._status.setText(f'{len(self._headlines)} HEADLINES · LIVE')


class MemoryMonitorPane(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        self._rows = []
        self._cat_filter = 'All'
        self._clear_armed = False
        self._orbits_visible = True
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(7)

        head = QHBoxLayout(); head.setSpacing(6)
        title = QLabel('MEMORY CORE  //  SYNAPSE ORB')
        title.setStyleSheet(_theme_section_css())
        head.addWidget(title, 1)
        self._orbits_btn = _GlowButton('MANAGE ▾', '◉', compact=True)
        self._orbits_btn.setFixedHeight(22)
        self._orbits_btn.clicked.connect(self._toggle_orbits)
        head.addWidget(self._orbits_btn)
        lay.addLayout(head)

        self._orbit_wrap = QWidget(); self._orbit_wrap.setStyleSheet('background:transparent;')
        olay = QVBoxLayout(self._orbit_wrap); olay.setContentsMargins(0, 0, 0, 0); olay.setSpacing(0)
        try:
            from dashboard.brain3d import BrainGraph3D
            self._orbit = BrainGraph3D(get_graph=self._brain_graph)
        except Exception as exc:
            self._orbit = None
            cap = QLabel(f'3D orb unavailable: {exc}')
            cap.setStyleSheet("color:#5796ad;font:7pt 'Exo 2';background:transparent;")
            olay.addWidget(cap)
        else:
            self._orbit.setMinimumHeight(320)
            olay.addWidget(self._orbit, 1)
            # Selecting a bead in the orb reports it here — the orb and the list
            # are two views of the same memory store, so they stay in step.
            try:
                self._orbit.nodeFocused.connect(self._on_node_focus)
            except Exception:
                pass
        lay.addWidget(self._orbit_wrap, 1)

        self._cap = QLabel('Attaching to JARVIS core…')
        self._cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cap.setStyleSheet("color:#5796ad;font:7pt 'Exo 2';background:transparent;")
        lay.addWidget(self._cap)

        self._manage = QWidget(); self._manage.setStyleSheet('background:transparent;')
        mlay = QVBoxLayout(self._manage); mlay.setContentsMargins(0, 2, 0, 0); mlay.setSpacing(6)
        stats = QHBoxLayout(); stats.setSpacing(6)
        self._stat_total = QLabel('FACTS --'); self._stat_cats = QLabel('AREAS --'); self._stat_new = QLabel('NEWEST --')
        for w in (self._stat_total, self._stat_cats, self._stat_new):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w.setStyleSheet(_theme_chip_css() + "font:700 7pt 'Exo 2';")
            stats.addWidget(w, 1)
        mlay.addLayout(stats)
        search_row = QHBoxLayout(); search_row.setSpacing(6)
        self._search = QLineEdit(); self._search.setPlaceholderText('Search facts…'); self._search.setStyleSheet(_hud_field_css())
        self._search.setMinimumHeight(30)
        self._search.textChanged.connect(self._render)
        search_row.addWidget(self._search, 1)
        self._catbox = QComboBox(); self._catbox.addItem('All'); self._catbox.setStyleSheet(_hud_field_css())
        self._catbox.setMinimumHeight(30)
        self._catbox.currentTextChanged.connect(self._on_cat_filter)
        search_row.addWidget(self._catbox)
        reload_b = _GlowButton('RELOAD', '↻', compact=True); reload_b.clicked.connect(self.reload); search_row.addWidget(reload_b)
        mlay.addLayout(search_row)
        self._list = QListWidget(); self._list.setStyleSheet(_hud_field_css()); self._list.setMinimumHeight(150); mlay.addWidget(self._list, 1)
        add = QGridLayout(); add.setHorizontalSpacing(6); add.setVerticalSpacing(5)
        self._cat = QComboBox(); self._cat.addItems(['notes', 'identity', 'preferences', 'projects', 'relationships', 'wishes']); self._cat.setStyleSheet(_hud_field_css())
        self._key = QLineEdit(); self._key.setPlaceholderText('key'); self._key.setStyleSheet(_hud_field_css())
        self._val = QLineEdit(); self._val.setPlaceholderText('value to remember'); self._val.setStyleSheet(_hud_field_css())
        self._val.returnPressed.connect(self.add_fact)
        add.addWidget(self._cat, 0, 0); add.addWidget(self._key, 0, 1); add.addWidget(self._val, 0, 2)
        mlay.addLayout(add)
        row = QHBoxLayout(); row.setSpacing(6)
        save = _GlowButton('REMEMBER', '◆', compact=True); save.clicked.connect(self.add_fact); row.addWidget(save)
        forget = _GlowButton('FORGET SELECTED', '✕', compact=True); forget.clicked.connect(self.forget_selected); row.addWidget(forget)
        self._clear_btn = _GlowButton('CLEAR ALL', '⚠', compact=True); self._clear_btn.clicked.connect(self.clear_all); row.addWidget(self._clear_btn)
        mlay.addLayout(row)
        self._manage.setVisible(False)
        lay.addWidget(self._manage, 0, Qt.AlignmentFlag.AlignBottom)
        self.reload()

    def _on_cat_filter(self, text: str) -> None:
        self._cat_filter = text
        self._render()

    def _on_node_focus(self, node: dict) -> None:
        """The orb reports its selected memory back into the pane caption."""
        if not node:
            self._cap.setText(f'{len(self._rows)} memories attached to the core — live from the brain store.')
            return
        cat = str(node.get("category", "?"))
        label = str(node.get("label", ""))
        imp = node.get("importance")
        extra = f"  ·  strength {float(imp):.2f}" if isinstance(imp, (int, float)) else ""
        self._cap.setText(f'FOCUS // {cat} — {label[:64]}{extra}')
        # Select the matching row when the bead is one of the legacy key/value
        # facts shown in the list, so both halves point at the same memory.
        try:
            needle = label.lower()
            for i in range(self._list.count()):
                it = self._list.item(i)
                txt = it.text().lower()
                key = str(node.get("label", "")).split(": ", 1)[-1].split(" — ")[0].lower()
                if key and key in txt and (not needle or needle[:12] in txt or key in txt):
                    self._list.setCurrentItem(it)
                    break
        except Exception:
            pass

    def _brain_graph(self):
        try:
            from memory.manager import get_brain_memory
            g = get_brain_memory().graph()
        except Exception:
            g = {"nodes": [{"id": 0, "label": "JARVIS CORE", "category": "CORE", "activity": 0.0}], "links": []}
        nodes = {n.get("id"): n for n in (g.get("nodes") or [])}
        links = list(g.get("links") or [])
        seen_ids = set(nodes)
        try:
            from memory.memory_manager import all_entries_for_ui
            for i, r in enumerate(all_entries_for_ui()):
                nid = f"legacy_{i}"
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                nodes[nid] = {
                    "id": nid, "label": f"{r.get('category', '?')}: "
                    f"{str(r.get('key', ''))[:22]} — {str(r.get('value', ''))[:22]}",
                    "category": str(r.get('category', 'DEFAULT')).upper(),
                    "activity": 1.0,
                }
                links.append({"s": 0, "t": nid, "type": "has_memory", "weight": 0.6})
        except Exception:
            pass
        return {"nodes": list(nodes.values()), "links": links}

    def _toggle_orbits(self) -> None:
        showing = not self._manage.isVisible()
        self._manage.setVisible(showing)
        self._orbits_visible = showing
        self._orbits_btn.setText('MANAGE ▴' if showing else 'MANAGE ▾')
        if showing:
            try:
                win = self.window()
                need = self.sizeHint().height() + 60
                if win.height() < need:
                    win.resize(win.width(), need)
            except Exception:
                pass

    def reload(self):
        try:
            from memory.memory_manager import all_entries_for_ui
            self._rows = all_entries_for_ui()
        except Exception as exc:
            self._rows = []
            self._cap.setText(f'Memory load failed: {exc}')
            return
        try:
            if getattr(self, '_orbit', None) is not None:
                self._orbit.refresh()
        except Exception:
            pass
        cats = sorted({str(r.get('category', '')) for r in self._rows if r.get('category')})
        try:
            keep = self._catbox.currentText()
            self._catbox.blockSignals(True)
            self._catbox.clear()
            self._catbox.addItem('All')
            self._catbox.addItems(cats)
            if keep in (['All'] + cats):
                self._catbox.setCurrentText(keep)
                self._cat_filter = keep
            self._catbox.blockSignals(False)
        except Exception:
            pass
        try:
            newest = self._rows[0]['updated'] if self._rows else '—'
            self._stat_total.setText(f'FACTS {len(self._rows)}')
            self._stat_cats.setText(f'AREAS {len(cats)}')
            self._stat_new.setText(f'NEWEST {str(newest)[:10]}')
        except Exception:
            pass
        self._cap.setText(f'{len(self._rows)} memories attached to the core — live from the brain store.')
        self._disarm_clear()
        self._render()
        # Keep the orb's counters in step with the chips above it.
        try:
            if getattr(self, '_orbit', None) is not None:
                self._orbit.set_stats({
                    'facts': len(self._rows), 'areas': len(cats),
                    'newest': str(self._rows[0]['updated'])[:10] if self._rows else '—',
                })
        except Exception:
            pass

    def _render(self):
        q = self._search.text().strip().lower()
        self._list.clear()
        shown = 0
        for r in self._rows:
            line = f"{r['category']}/{r['key']} — {r['value']}   [{r['updated'] or '—'}]"
            if self._cat_filter != 'All' and str(r.get('category', '')) != self._cat_filter:
                continue
            if q and q not in line.lower():
                continue
            shown += 1
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, (r['category'], r['key']))
            self._list.addItem(item)
        if self._list.count() == 0:
            self._list.addItem('Nothing stored yet. Add a fact below.')
        # Push the same filter into the 3D orb so the sphere highlights exactly
        # the memories listed here instead of drifting out of sync.
        try:
            if getattr(self, '_orbit', None) is not None:
                self._orbit.set_query(q)
                self._orbit.set_category(self._cat_filter)
                self._orbit.set_stats({
                    'facts': len(self._rows), 'areas': self._catbox.count() - 1,
                    'shown': shown, 'query': q, 'area': self._cat_filter,
                })
        except Exception:
            pass

    def _disarm_clear(self) -> None:
        self._clear_armed = False
        try:
            self._clear_btn.setText('CLEAR ALL')
        except Exception:
            pass

    def clear_all(self):
        if not self._clear_armed:
            self._clear_armed = True
            try:
                self._clear_btn.setText('SURE?')
            except Exception:
                pass
            self._cap.setText('Press CLEAR ALL again within 4 seconds to forget everything.')
            QTimer.singleShot(4000, self._disarm_clear)
            return
        self._disarm_clear()
        try:
            from memory.memory_manager import forget as _forget
            n = 0
            for r in list(self._rows):
                try:
                    _forget(r['key'], r['category'])
                    n += 1
                except Exception:
                    pass
            self.reload()
            self._cap.setText(f'Forgot {n} facts. Memory is clear.')
        except Exception as exc:
            self._cap.setText(f'Clear failed: {exc}')

    def add_fact(self):
        key = self._key.text().strip().replace(' ', '_')
        val = self._val.text().strip()
        if not key or not val:
            self._cap.setText('Enter both a key and a value to remember.')
            return
        try:
            from memory.memory_manager import remember
            remember(key, val, self._cat.currentText())
            self._key.clear(); self._val.clear()
            self.reload()
            self._cap.setText(f'Remembered {self._cat.currentText()}/{key}.')
        except Exception as exc:
            self._cap.setText(f'Remember failed: {exc}')

    def forget_selected(self):
        item = self._list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        try:
            from memory.memory_manager import forget
            forget(data[1], data[0])
            self.reload()
        except Exception as exc:
            self._cap.setText(f'Forget failed: {exc}')


class _ActivityTimeline(QFrame):
    MAX_ENTRIES = 60

    _CATS = (
        (('you:', 'voice:', 'mic:'),            'VOICE', '#e9fcff'),
        (('jarvis:', 'j.a.r.v.i.s', 'ai:'),     'AI',    '#9ae8ff'),
        (('err', 'fail', 'exception', 'trace'), 'ERROR', '#ff7892'),
        (('ok', 'done', 'complete', 'sent'),    'OK',    '#7affc3'),
        (('file:',),                            'FILE',  '#7affc3'),
        (('tool', 'exec', 'run:'),              'TOOL',  '#ffcc66'),
        (('web', 'search', 'fetch'),            'WEB',   '#42c7ef'),
        (('warn',),                             'WARN',  '#ffcc66'),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        self._entries: list[dict] = []
        self._filter = 'ALL'
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet('QScrollArea{background:transparent;border:none;}' + _theme_scrollbar_css(5))
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._cl = QVBoxLayout(self._container)
        self._cl.setContentsMargins(2, 2, 2, 2)
        self._cl.setSpacing(6)
        self._cl.addStretch(1)
        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll, 1)

    @classmethod
    def _categorize(cls, text: str):
        tl = text.strip().lower()
        for prefixes, tag, color in cls._CATS:
            if tl.startswith(prefixes) or f' {prefixes[0]}' in tl[:14]:
                return tag, color
        if tl.startswith('sys:'):
            return 'SYS', '#659fb1'
        return 'SYS', '#659fb1'

    def set_filter(self, tag: str) -> None:
        self._filter = str(tag or 'ALL').upper()
        self._render()

    def count(self) -> int:
        return len(self._entries)

    def lines(self, n: int = 80) -> list[str]:
        try:
            return [f"[{e['time']}] [{e['tag']}] {e['text']}" for e in self._entries[:max(1, int(n))]]
        except Exception:
            return []

    def add_entry(self, text: str) -> None:
        text = str(text or '').strip()
        if not text:
            return
        try:
            stamp = time.strftime('%H:%M:%S')
        except Exception:
            stamp = '--:--:--'
        tag, color = self._categorize(text)
        self._entries.insert(0, {'time': stamp, 'tag': tag, 'color': color, 'text': text[:280]})
        del self._entries[self.MAX_ENTRIES:]
        self._render(new_first=(not self._filter or self._filter == 'ALL' or self._filter == tag))

    def _render(self, new_first: bool = False) -> None:
        while self._cl.count() > 1:
            item = self._cl.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        shown = 0
        for i, e in enumerate(self._entries):
            if self._filter != 'ALL' and e['tag'] != self._filter:
                continue
            card = self._make_card(e)
            self._cl.insertWidget(shown, card)
            shown += 1
            if new_first and i == 0:
                self._fade_in(card)
        try:
            self._scroll.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def _make_card(self, e: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:rgba(1,15,25,120); border:none; border-radius:8px; }"
        )
        hl = QHBoxLayout(card)
        hl.setContentsMargins(9, 6, 9, 6)
        hl.setSpacing(8)
        dot = QLabel('●')
        dot.setStyleSheet(f"color:{e['color']};font-size:9px;background:transparent;{_theme_icon_font_css()}")
        dot.setFixedWidth(12)
        hl.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        vb = QVBoxLayout()
        vb.setSpacing(2)
        vb.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.setSpacing(8)
        tag = QLabel(e['tag'])
        tag.setStyleSheet(f"color:{e['color']};font:700 7pt 'Exo 2';background:transparent;")
        head.addWidget(tag)
        ts = QLabel(e['time'])
        ts.setStyleSheet("color:#3a6a7a;font:700 7pt 'Exo 2';background:transparent;")
        head.addWidget(ts)
        head.addStretch(1)
        vb.addLayout(head)
        body = QLabel(e['text'])
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("color:#b9efff;font:8pt 'Share Tech Mono';background:transparent;")
        vb.addWidget(body)
        hl.addLayout(vb, 1)
        return card

    def _fade_in(self, card: QFrame) -> None:
        try:
            eff = QGraphicsOpacityEffect(card)
            card.setGraphicsEffect(eff)
            eff.setOpacity(0.0)
            anim = QPropertyAnimation(eff, b'opacity', card)
            anim.setDuration(200)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: card.setGraphicsEffect(None))
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except Exception:
            pass


class ToolsPane(QFrame):
    CATEGORIES = ('All', 'Computer Control', 'Browser', 'Files', 'Screen',
                  'Media', 'System', 'Automation', 'Coding', 'Memory', 'Plugins')

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        self._host = host
        self._entries: list[dict] = []
        self._filter = 'All'
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        top = QHBoxLayout()
        top.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText('Search tools…')
        self._search.setStyleSheet(_hud_field_css())
        self._search.setMinimumHeight(30)
        self._search.textChanged.connect(self._render)
        top.addWidget(self._search, 1)
        self._cat = QComboBox()
        self._cat.addItems(list(self.CATEGORIES))
        self._cat.setStyleSheet(_hud_field_css())
        self._cat.setMinimumHeight(30)
        self._cat.currentTextChanged.connect(self._on_cat)
        top.addWidget(self._cat)
        lay.addLayout(top)
        self._count = QLabel('')
        self._count.setStyleSheet("color:#5796ad;font:700 7pt 'Exo 2';background:transparent;")
        lay.addWidget(self._count)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet('QScrollArea{background:transparent;border:none;}' + _theme_scrollbar_css(5))
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._cl = QVBoxLayout(self._container)
        self._cl.setContentsMargins(2, 2, 2, 2)
        self._cl.setSpacing(7)
        self._cl.addStretch(1)
        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll, 1)

    def _on_cat(self, text: str) -> None:
        self._filter = text
        self._render()

    def reload(self) -> None:
        try:
            host = self._host() if callable(self._host) else self._host
            if host is not None and hasattr(host, 'get_tool_entries'):
                self._entries = list(host.get_tool_entries() or [])
        except Exception:
            pass
        self._render()

    def _render(self) -> None:
        q = self._search.text().strip().lower() if hasattr(self, '_search') else ''
        while self._cl.count() > 1:
            item = self._cl.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        shown = 0
        for e in self._entries:
            if self._filter != 'All' and e.get('category') != self._filter:
                continue
            hay = f"{e.get('name','')} {e.get('desc','')}".lower()
            if q and q not in hay:
                continue
            self._cl.insertWidget(shown, self._make_card(e))
            shown += 1
        try:
            self._count.setText(f'{shown} of {len(self._entries)} tools ready')
        except Exception:
            pass

    def _make_card(self, e: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:rgba(1,15,25,120); border:none; border-radius:8px; }"
        )
        hl = QHBoxLayout(card)
        hl.setContentsMargins(10, 8, 10, 8)
        hl.setSpacing(9)
        icon = QLabel(e.get('icon', '▣'))
        icon.setStyleSheet("color:#42c7ef;font-size:15px;background:transparent;" + _theme_icon_font_css())
        icon.setFixedWidth(22)
        hl.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        vb = QVBoxLayout()
        vb.setSpacing(3)
        vb.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name = QLabel(str(e.get('name', 'tool')).upper())
        name.setStyleSheet("color:#dff8ff;font:700 8pt 'Orbitron';letter-spacing:1px;background:transparent;border:none;")
        title_row.addWidget(name, 1)
        avail = bool(e.get('available', True))
        dot = QLabel('● READY' if avail else '○ OFF')
        dot.setStyleSheet(f"color:{'#7affc3' if avail else '#5796ad'};font:700 7pt 'Exo 2';background:transparent;")
        title_row.addWidget(dot)
        vb.addLayout(title_row)
        cat = QLabel(str(e.get('category', '')))
        cat.setStyleSheet("color:#5796ad;font:600 7pt 'Exo 2';background:transparent;")
        vb.addWidget(cat)
        desc = QLabel(str(e.get('desc', '')))
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#9ae8ff;font:8pt 'Rajdhani';background:transparent;")
        vb.addWidget(desc)
        hl.addLayout(vb, 1)
        run = QPushButton('RUN')
        run.setFixedSize(56, 32)
        run.setCursor(Qt.CursorShape.PointingHandCursor)
        run.setStyleSheet(
            "QPushButton { color:#edfcff; background:rgba(0,111,151,170);"
            " border:1px solid #75e2fa; border-radius:7px; font:700 7pt 'Exo 2'; }"
            "QPushButton:hover { background:rgba(0,154,199,210); }"
            "QPushButton:disabled { color:#3a6a7a; border-color:rgba(67,151,185,40);"
            " background:rgba(1,17,27,80); }"
        )
        run.setEnabled(avail)
        run.setToolTip(str(e.get('desc', 'Run this tool')))
        run.clicked.connect(lambda _=False, entry=dict(e): self._run_entry(entry))
        hl.addWidget(run, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _run_entry(self, entry: dict) -> None:
        try:
            _sfx('click')
        except Exception:
            pass
        try:
            host = self._host() if callable(self._host) else self._host
            if host is None:
                return
            action = entry.get('action')
            if action == 'open_web_task' and hasattr(host, '_open_web_task_panel'):
                host._open_web_task_panel()
                return
            if action == 'open_window' and hasattr(host, 'control_jarvis_window'):
                host.control_jarvis_window(entry.get('window', 'webview'), 'open')
                return
            run_text = (entry.get('run') or '').strip()
            if run_text and hasattr(host, '_send_backend_command'):
                try:
                    host._activity_add(f"YOU: {run_text}")
                except Exception:
                    pass
                host._send_backend_command(run_text)
        except Exception:
            pass


class WorkspaceWindowPane(QFrame):
    def __init__(self, parent=None, title='WORKSPACE'):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;border:none;')
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self._title = QLineEdit(title); self._title.setStyleSheet(_hud_field_css())
        lay.addWidget(self._title)
        self._notes = QTextEdit(); self._notes.setPlaceholderText('Drop Markdown notes, links, or live status for this window…')
        self._notes.setStyleSheet(_hud_field_css()); lay.addWidget(self._notes, 1)
        row = QHBoxLayout(); row.setSpacing(6)
        pin = QLabel('MARKDOWN NOTES  ·  **BOLD**  ·  [LINKS](https://…)  ·  ```CODE```')
        pin.setStyleSheet(f'color:{C.TEXT_DIM};font:700 7pt "Exo 2";background:transparent;'); row.addWidget(pin, 1)
        preview = QPushButton('PREVIEW')
        preview.clicked.connect(lambda: self._show_markdown_preview())
        preview.setStyleSheet(_theme_button_css())
        row.addWidget(preview)
        lay.addLayout(row)

    def _show_markdown_preview(self):
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle('JARVIS · MARKDOWN PREVIEW')
            dlg.resize(760, 560)
            box=QVBoxLayout(dlg); box.setContentsMargins(10,10,10,10)
            view=MarkdownTextBrowser(dlg); view.setStyleSheet(_hud_field_css() + 'QTextBrowser{padding:10px;}'); view.set_markdown(self._notes.toPlainText()); box.addWidget(view,1)
            r=QHBoxLayout(); copy=QPushButton('COPY ALL'); copy.clicked.connect(view.copy_all); close=QPushButton('CLOSE'); close.clicked.connect(dlg.close); r.addWidget(copy); r.addStretch(1); r.addWidget(close); box.addLayout(r)
            dlg.setStyleSheet(_theme_button_css()); dlg.exec()
        except Exception:
            pass


class _LayoutElementWidget(QWidget):
    def __init__(self, kind: str, text: str, color: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._text = text
        self._color = color
        self._drag_start = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._label = None
        if kind == 'Text':
            self._label = QLabel(text, self)
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._label.setStyleSheet(f'background:transparent;color:{color};font:700 13px "Rajdhani";')
        elif kind == 'Button':
            self._label = QPushButton(text, self)
            self._label.setStyleSheet(f'QPushButton{{background:rgba(0,24,36,190);color:{color};border:1px solid {color};border-radius:8px;padding:6px 10px;}}')
            self._label.setGeometry(self.rect())
            self._label.setEnabled(False)
        elif kind in ('Panel', 'Window', 'Monitor', 'WebView'):
            self.setStyleSheet(f'background:rgba(0,14,24,110);border:1px solid {color};border-radius:10px;')
        elif kind == 'Separator':
            self.setStyleSheet(f'background:{color};border:none;')

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._label is not None:
            self._label.setGeometry(self.rect())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start is not None and e.buttons() & Qt.MouseButton.LeftButton:
            d = e.globalPosition().toPoint() - self._drag_start
            self.move(self.pos() + d)
            self._drag_start = e.globalPosition().toPoint()
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if hasattr(self.window(), '_save_layout_elements'):
                self.window()._save_layout_elements()
            e.accept(); return
        super().mouseReleaseEvent(e)


class MainWindow(QMainWindow):
    _state_sig = pyqtSignal(str)
    _log_sig = pyqtSignal(str)
    _content_sig = pyqtSignal(str, str)
    _reconfig_sig = pyqtSignal()
    _camera_sig = pyqtSignal(bytes)
    _cam_stream_sig = pyqtSignal(bool)
    _cam_frame_sig = pyqtSignal(bytes)
    _confirm_sig = pyqtSignal(str, str)
    _confirm_hide_sig = pyqtSignal()
    _image_bytes_sig = pyqtSignal(bytes, str)
    _gui_exec_sig = pyqtSignal(object)
    # Mic level arrives from the sounddevice audio thread — a direct widget
    # call from that thread is undefined behaviour in Qt. The signal marshals
    # it onto the GUI thread where the waveform painters actually live.
    _audio_level_sig = pyqtSignal(float)

    EXPANDED_SIZE = QSize(1380, 840)
    COLLAPSED_SIZE = QSize(136, 136)

    # How much smaller than the compact window the reactor logo should be.
    # Bigger number = smaller logo. Adjust this one value for future tweaks.
    _LOGO_PADDING = 28
    _LOGO_MIN = 96

    def __init__(self, face_path: str):
        super().__init__()
        cfg = _ui_load(API_FILE)
        global DISPLAY_FONT
        DISPLAY_FONT = _register_redesign_font()
        _register_app_fonts()
        self._face_path = face_path
        self._assistant_name = (cfg.get('assistant_name') or 'JARVIS').strip() or 'JARVIS'
        self._muted = False
        self._voice_input_enabled = True
        self._current_file = None
        self._ready = self._check_config()
        self.get_plugins = None
        self.request_say = None
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.on_voice_change = None
        self.on_audio_device_change = None
        self._confirm_overlay = None
        self._customize_overlay = None
        self._overlay = None
        self._collapsed = True
        self._panel_alpha = int(cfg.get('ui_opacity', 82) or 82)
        self._font_family = cfg.get('ui_font') or 'Rajdhani'
        self._drag_pos = None
        self._cam_stop = threading.Event()
        self._cam_thread = None
        self._panel_animations = {}
        self._quick_popup = None
        self._command_window = None
        self._chat_history = []
        self._custom_quick_actions = list(cfg.get("quick_actions", [])) if isinstance(cfg.get("quick_actions", []), list) else []
        self._layout_editor = None
        self._layout_elements = {}
        self._layout_widgets = {}
        self._reactor_opacity = int(cfg.get('reactor_opacity', 60) or 60)
        self._reactor_stroke_opacity = int(cfg.get('reactor_stroke_opacity', 100) or 100)
        self._features = dict(cfg.get('features', {})) if isinstance(cfg.get('features', {}), dict) else {}
        self._profile_avatar_path = self._profile_avatar_path_get()
        self._active_voice_config = {k: cfg.get(k) for k in (
            'tts_engine', 'tts_voice', 'voice_name', 'voice_output_mode',
            'onnx_voice_model', 'onnx_voice_config', 'onnx_voice_speaker',
            'onnx_execution_provider', 'sapi_voice',
            'elevenlabs_voice_id', 'elevenlabs_model_id',
            'fish_audio_voice_id', 'fish_audio_model_id',
            'voice_speed', 'voice_pitch', 'voice_volume'
        )}
        self._chat_filter_categories = dict(cfg.get('chat_filter_categories', {}) or {})
        self._pc_control_active = False
        self._pc_control_restore_reactor = False
        self._pc_control_restore_command = False
        self._drag_reactor_enabled = bool(self._features.get('drag_reactor', True))
        self._music_webview_enabled = bool(self._features.get('webview_music', True))
        self._reactor_speed = float(cfg.get('reactor_animation_speed', 1.0) or 1.0)
        self._eq_sensitivity = float(cfg.get('equalizer_sensitivity', 1.0) or 1.0)
        self._startup_phase = _StartupPhase.SETTLED
        self._startup_voice_seen = False
        self._startup_target_pos = None
        self._startup_pending_layout = False
        self._startup_fallback_timer = None
        self._startup_timeline = None
        self._startup_end_logo = 0
        self._compact_alpha = 0
        self._compact_size = max(170, min(500, int(cfg.get("compact_size", 170) or 170)))

        _ui_color = (cfg.get('ui_color') or '').strip()
        if _ui_color:
            apply_ui_accent(_ui_color)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self.setMinimumSize(QSize(self._compact_size, self._compact_size))
        self.setMaximumSize(QSize(self._compact_size, self._compact_size))
        self.resize(QSize(self._compact_size, self._compact_size))
        self.setFont(QFont(self._font_family, 10))
        self.setWindowTitle('J.A.R.V.I.S')

        self._build_ui()
        self._wire_signals()
        self._apply_initial_visibility()
        self._load_layout_elements()
        self._header_logo.set_graphic_opacity(self._reactor_opacity)
        self._header_logo.set_stroke_opacity(self._reactor_stroke_opacity)
        self._header_logo._animation_speed = self._reactor_speed
        self._hide_taskbar_button = bool(self._features.get('hide_taskbar_button', self._features.get('hide_taskbar', False)))
        self._set_taskbar_button_hidden(self._hide_taskbar_button)
        try:
            from core import sfx as _sfx_mod
            _sfx_mod.configure(
                enabled=bool(self._features.get('enable_sfx', True)),
                volume=float(cfg.get('voice_volume', 1.0) or 1.0) * 0.35,
            )
        except Exception:
            pass
        if not self._ready:
            QTimer.singleShot(120, self._show_setup)
        else:
            self._restore_window_position(animate=True)

    def _surface_style(self):
        opaque = max(40, min(245, int(255*self._panel_alpha/100)))
        return f"""QFrame#Surface {{
            background: rgba(1, 8, 16, {opaque});
            border: none;
            border-radius: 3px;
        }}"""

    def _logo_target_size(self) -> int:
        return max(self._LOGO_MIN, int(self._compact_size) - self._LOGO_PADDING)

    def _logo_settled_size(self) -> int:
        """Logo diameter when the puck is settled.
        Default compact_size is 150 → settled logo is now 132 px (was 122)."""
        return max(110, int(self._compact_size) - 20)

    def _settled_logo_size(self) -> int:
        """Settled logo diameter: STARTUP_LOGO_END_SIZE clamped to fit the
        compact window; the clamp is logged once per process.

        The painted artwork keeps a transparent margin (extent ~= widget-4px),
        so the widget may exceed the window by STARTUP_END_FIT_SLACK without
        clipping — the default 176 px compact window still gets the full
        180 px settle size.
        """
        global _STARTUP_END_CLAMP_LOGGED
        small = max(170, int(getattr(self, '_compact_size', 170)))
        end = min(STARTUP_LOGO_END_SIZE, small + STARTUP_END_FIT_SLACK)
        if end < STARTUP_LOGO_END_SIZE and not _STARTUP_END_CLAMP_LOGGED:
            _STARTUP_END_CLAMP_LOGGED = True
            try:
                self.write_log(
                    f'SYS: compact window {small}px cannot fit the '
                    f'{STARTUP_LOGO_END_SIZE}px settled logo — clamped to {end}px')
            except Exception:
                pass
        return max(120, end)

    def _defer_layout_until_settled(self) -> bool:
        """True while a startup settle owns the geometry (phase != SETTLED).

        Compact-layout/settings writers call this before their min/max/resize/
        logo-size writes; the change is deferred and re-applied at settle end.
        """
        if getattr(self, '_startup_phase', None) is not _StartupPhase.SETTLED:
            self._startup_pending_layout = True
            return True
        return False

    def _apply_pending_startup_layout(self) -> None:
        """Re-apply layout changes deferred while the settle owned the geometry."""
        if not getattr(self, '_startup_pending_layout', False):
            return
        self._startup_pending_layout = False
        try:
            self._collapse()
        except Exception:
            pass

    def _stop_startup_fallback(self) -> None:
        timer = getattr(self, '_startup_fallback_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _shutdown_startup_sequence(self) -> None:
        """Window close during startup: stop every startup timer/animation."""
        self._stop_startup_fallback()
        for a in getattr(self, '_startup_anims', ()) or ():
            try:
                a.stop()
            except Exception:
                pass
        slide = getattr(self, '_startup_timeline', None)
        if slide is not None:
            try:
                slide.stop()
            except Exception:
                pass

    def _build_ui(self):
        root = QWidget(); root.setObjectName('Root'); root.setStyleSheet('background: transparent;')
        self.setCentralWidget(root)
        root_l = QVBoxLayout(root); root_l.setContentsMargins(0,0,0,0); root_l.setSpacing(0); self._root_layout = root_l

        self.surface = QFrame(); self.surface.setObjectName('Surface'); self.surface.setStyleSheet(self._surface_style())
        root_l.addWidget(self.surface)
        sl = QVBoxLayout(self.surface); sl.setContentsMargins(18,14,18,16); sl.setSpacing(10)

        self._topbar = QFrame(); self._topbar.setStyleSheet('background: transparent;')
        hb = QHBoxLayout(self._topbar); hb.setContentsMargins(2,0,2,0); hb.setSpacing(7); self._header_layout = hb
        self._header_logo = _ArcLogoButton(size=self._logo_settled_size()); self._header_logo.clicked.connect(self._toggle_command_window)
        self._startup_logo_hitbox = _StartupLogoHitArea(self)
        self._startup_logo_hitbox.setObjectName('StartupLogoHitArea')
        self._startup_logo_hitbox.setToolTip('Open JARVIS command panel')
        self._startup_logo_hitbox.hide()
        self._startup_logo_hitbox.clicked.connect(self._toggle_command_window)
        # Keep the invisible hit surface synchronized with the animated startup
        # logo continuously, including the period before JARVIS speaks / before
        # the wake-word transition begins.
        self._startup_logo_hitbox_timer = QTimer(self)
        self._startup_logo_hitbox_timer.setInterval(16)
        self._startup_logo_hitbox_timer.timeout.connect(self._sync_startup_logo_hitbox)
        # 60 FPS sync keeps the invisible hit target locked to startup motion
        # even before the first voice/wake-word event occurs.
        # Circular profile button — click to set the user's avatar.
        self._profile_btn = QPushButton()
        self._profile_btn.setFixedSize(34, 34)
        self._profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._profile_btn.setToolTip('Set your profile picture')
        self._profile_btn.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._profile_btn.setStyleSheet('QPushButton{background:transparent;border:none;padding:0;margin:0;}')
        self._profile_btn.paintEvent = self._paint_profile_btn
        self._profile_btn.clicked.connect(self._pick_profile_image)
        hb.addWidget(self._profile_btn)
        hb.addWidget(self._header_logo)
        self._cam_toggle_btn = QPushButton('◉')
        self._cam_toggle_btn.setCheckable(True)
        self._cam_toggle_btn.setFixedSize(26, 26)
        self._cam_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cam_toggle_btn.setToolTip('Webcam in reactor frame — click to show/hide the camera inside the logo circle')
        self._cam_toggle_btn.setStyleSheet(
            f"QPushButton {{ color:{C.PRI}; background:rgba(0,40,60,140);"
            f" border:1px solid {C.PRI}; border-radius:13px; font:700 10pt 'Exo 2'; }}"
            f"QPushButton:hover {{ color:{C.WHITE}; background:{C.PRI_GHO}; }}"
            f"QPushButton:checked {{ color:{C.WHITE}; background:rgba(0,150,190,200); }}"
        )
        self._cam_toggle_btn.clicked.connect(self._toggle_inline_cam)
        hb.addWidget(self._cam_toggle_btn)
        self._cam_inline = False
        self._brand = QLabel('J.A.R.V.I.S'); self._brand.setStyleSheet(f'color:{C.PRI};font-size:17px;font-weight:800;letter-spacing:3px;background:transparent;')
        hb.addWidget(self._brand)
        self._status = QLabel('VOICE LINK · ONLINE'); self._status.setStyleSheet(f'color:{C.TEXT_MED};font-size:9px;font-family:"Archivo Black";background:transparent;')
        hb.addWidget(self._status)
        hb.addStretch(1)
        self._top_metrics = QLabel('CPU --%   RAM --%   GPU --%'); self._top_metrics.setStyleSheet(f'color:{C.TEXT_DIM};font-size:8px;background:transparent;')
        hb.addWidget(self._top_metrics)
        self._settings_btn = self._mini_button('⚙', 'Settings')
        self._min_btn = self._mini_button('—', 'Minimize')
        self._close_btn = self._mini_button('×', 'Close')
        sl.addWidget(self._topbar)

        self._main_row = QWidget(); mr = QHBoxLayout(self._main_row); mr.setContentsMargins(0,0,0,0); mr.setSpacing(12)
        self.hud = HudCanvas(self._face_path, self._assistant_name.upper())
        mr.addWidget(self.hud, 1)
        self.quick_panel = self._build_quick_panel()
        self.quick_panel.hide()
        sl.addWidget(self._main_row, 1)

        self._statusbar = QLabel('LISTENING · F4 MUTE · F11 FULLSCREEN · ESC INTERRUPT')
        self._statusbar.setStyleSheet(f'color:{C.TEXT_DIM};font-size:8px;font-family:"Archivo Black";background:transparent;padding:2px 4px;')
        sl.addWidget(self._statusbar)

        self._panel_launcher = None
        self._video_preview_widget = VideoPreview()
        self._video_panel = self._build_floating_panel(self._video_preview_widget, 'video')
        self._hide_video_title(self._video_panel)
        self._activity_panel = self._build_activity_panel()
        self._image_panel = self._build_image_panel()
        self._content_panel = self._build_content_panel()
        self._model_panel = self._build_model_panel()
        home = str((_ui_load(API_FILE) or {}).get('web_homepage') or 'https://www.google.com')
        web_features = self._features
        self._webview_pane = WebViewPane(
            home=home,
            use_engine=bool(web_features.get('webview_engine', True)),
        )
        self._webview_panel = self._build_floating_panel(self._webview_pane, 'web')
        self._web_task_pane = WebTaskPane()
        self._web_task_pane.task_run.connect(self._on_web_task)
        self._web_task_panel = self._build_floating_panel(self._web_task_pane, 'task')
        self._world_monitor_pane = WorldMonitorPane()
        self._world_monitor_panel = self._build_floating_panel(self._world_monitor_pane, 'world')
        self._memory_pane = MemoryMonitorPane()
        self._memory_panel = self._build_floating_panel(self._memory_pane, 'memory')
        try:
            from dashboard.brain3d import BrainPanel
            self._brain_pane  = BrainPanel()
            self._brain_panel = self._build_floating_panel(self._brain_pane, 'brain')
        except Exception as _be:
            print(f"[Brain] ⚠️ brain panel unavailable: {_be}")
            self._brain_pane  = None
            self._brain_panel = None
        self._tools_pane = ToolsPane(self)
        self._tools_panel = self._build_floating_panel(self._tools_pane, 'tools')
        self._workspace_windows = []
        self._system_layout_widgets = {
            'Video Preview': self._video_panel,
            'Image Preview': self._image_panel,
            'Activity Log': self._activity_panel,
            'Content Surface': self._content_panel,
            '3D Display': self._model_panel,
            'Webview': self._webview_panel,
            'Web Task': self._web_task_panel,
            'World Monitor': self._world_monitor_panel,
            'Memory Core': self._memory_panel,
            'Brain Graph': self._brain_panel,
            'Tools Deck': self._tools_panel,
        }

        for panel in (self._video_panel, self._activity_panel, self._image_panel, self._content_panel, self._model_panel,
                      self._webview_panel, self._web_task_panel, self._world_monitor_panel, self._memory_panel,
                      self._tools_panel):
            panel.hide(); panel.raise_()
        if self._brain_panel is not None:
            self._brain_panel.hide(); self._brain_panel.raise_()

        self._clock_timer = QTimer(self); self._clock_timer.timeout.connect(self._update_clock); self._clock_timer.start(1000)
        self._metrics_timer = QTimer(self); self._metrics_timer.timeout.connect(self._update_metrics); self._metrics_timer.start(1500)
        self._update_clock(); self._update_metrics()

        self._cam_preview = _CameraPreview(None); self._cam_preview.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint); self._cam_preview.hide()
        self._cam_live_lbl = _FeatheredCamView(None); self._cam_live_lbl.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint); self._cam_live_lbl.hide()
        self._cam_live_lbl.setProperty('_jarvis_popup_drag', True)
        self._cam_live_lbl.installEventFilter(self)
        self._cam_live_lbl.setCursor(Qt.CursorShape.SizeAllCursor)

    def _wire_signals(self):
        self._state_sig.connect(self._apply_state)
        self._log_sig.connect(self._log_from_backend)
        self._content_sig.connect(self.show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._image_bytes_sig.connect(self._show_image_bytes)
        self._gui_exec_sig.connect(self._run_gui_task)
        self._audio_level_sig.connect(self._apply_audio_level)
        QShortcut(QKeySequence('F4'), self).activated.connect(self._toggle_mute)
        QShortcut(QKeySequence('F11'), self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence('Escape'), self).activated.connect(self._do_interrupt)
        QShortcut(QKeySequence('Ctrl+Shift+Space'), self).activated.connect(self.toggle_panels)
        QShortcut(QKeySequence('F6'), self).activated.connect(self._toggle_push_to_talk)
        close_shortcut = QShortcut(QKeySequence('Shift+Home'), self)
        close_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        close_shortcut.activated.connect(self._close_all_ui)
        self._settings_btn.clicked.connect(self._open_full_settings)
        self._topbar.installEventFilter(self)

    def _apply_audio_level(self, lv: float):
        """GUI-thread fan-out of the mic level to every reactive visual."""
        try:
            if bool(self._features.get('audio_reactive', True)):
                self.hud.set_audio_level(lv)
                self._header_logo.set_audio_level(lv)
                win = getattr(self, '_command_window', None)
                if win is not None and win.isVisible():
                    win.set_audio_level(lv)
        except Exception:
            pass

    def _mini_button(self, text, tip):
        b = QPushButton(text); b.setToolTip(tip); b.setFixedSize(30,30); b.setCursor(Qt.CursorShape.PointingHandCursor)
        radius = 0 if text == '×' else 7
        b.setStyleSheet(f"""QPushButton{{background:rgba(0,18,28,180);color:{C.TEXT_MED};border:1px solid {C.BORDER};border-radius:{radius}px;font-size:13px;}}
                           QPushButton:hover{{color:{C.PRI};border:1px solid {C.PRI};background:{C.PRI_GHO};}}""")
        return b

    def _panel_base(self):
        f = _HudPanel(None)
        f.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        f.setMinimumSize(260, 180)
        f.setStyleSheet(
            "QFrame { background: transparent; border: none; border-radius: 14px; }"
            "QLabel { background: transparent; }"
        )
        f.setProperty('_jarvis_floating_panel', True)
        f.setMouseTracking(True)
        return f

    def _panel_header(self, parent, icon='✦', close_cb=None, title='', subtitle=''):
        bar = QFrame(parent)
        bar.setFixedHeight(40)
        bar.setStyleSheet(_theme_header_css())
        row = QHBoxLayout(bar); row.setContentsMargins(12, 4, 6, 4); row.setSpacing(8)
        icon_l = QLabel(icon, bar); icon_l.setStyleSheet(f'color:{C.PRI};font-size:14px;background:transparent;{_theme_icon_font_css()}'); row.addWidget(icon_l)
        title_box = QVBoxLayout(); title_box.setSpacing(0); title_box.setContentsMargins(0, 0, 0, 0)
        title_l = QLabel(title or 'HUD WINDOW', bar)
        title_l.setStyleSheet("color:#dff8ff;font:700 9pt 'Orbitron';letter-spacing:2px;background:transparent;border:none;")
        title_box.addWidget(title_l)
        if subtitle:
            sub_l = QLabel(subtitle, bar)
            sub_l.setStyleSheet("color:#5796ad;font:600 6.5pt 'Exo 2';letter-spacing:1px;background:transparent;border:none;")
            title_box.addWidget(sub_l)
        row.addLayout(title_box, 1)
        x = _GlowSquareButton('×', bar, 26)
        x.setToolTip('Close window')
        if close_cb: x.clicked.connect(close_cb)
        row.addWidget(x)
        parent.layout().insertWidget(0, bar)
        bar.setProperty('_jarvis_panel_drag', True)
        icon_l.setProperty('_jarvis_panel_drag', True)
        title_l.setProperty('_jarvis_panel_drag', True)
        bar.installEventFilter(self); icon_l.installEventFilter(self); title_l.installEventFilter(self)
        bar.setCursor(Qt.CursorShape.SizeAllCursor); icon_l.setCursor(Qt.CursorShape.SizeAllCursor); title_l.setCursor(Qt.CursorShape.SizeAllCursor)
        return x

    def _build_quick_panel(self):
        f = self._panel_base(); f.setFixedWidth(318)
        l = QVBoxLayout(f); l.setContentsMargins(10,10,10,10); l.setSpacing(8)
        self._panel_header(f, '✦', lambda: self._set_panel_visible(f, False), 'QUICK LINK')
        self._panel_command_input = QLineEdit(); self._panel_command_input.setPlaceholderText('Type a command…'); self._panel_command_input.returnPressed.connect(lambda:self._send_backend_command(self._panel_command_input.text())); self._panel_command_input.setStyleSheet(f'QLineEdit{{background:rgba(0,0,0,80);color:{C.WHITE};border:1px solid {C.BORDER};border-radius:6px;padding:5px;}}'); l.addWidget(self._panel_command_input)
        hint = QLabel('QUICK LINK'); hint.setStyleSheet(f'color:{C.TEXT_DIM};font-size:8px;letter-spacing:2px;background:transparent;padding:0 4px;'); l.addWidget(hint)
        grid = QGridLayout(); grid.setSpacing(8)
        actions = [
            ('SETTINGS', self._open_full_settings),
            ('VIDEO PREVIEW', self._open_video_panel),
            ('IMAGE PREVIEW', self._open_image_panel),
            ('3D DISPLAY', self._open_3d_display),
            ('WEBVIEW', self._open_webview_panel),
            ('WEB TASK', self._open_web_task_panel),
            ('VISION', self._open_vision_preview),
            ('WORLD MONITOR', self._open_world_monitor),
            ('MEMORY', self._open_memory_panel),
        ]
        for i,(txt,cb) in enumerate(actions):
            b = _GlowButton(txt, '', compact=True); b.setMinimumHeight(46); b.clicked.connect(cb); grid.addWidget(b,i//2,i%2)
        l.addLayout(grid); l.addStretch(1)
        mic = QLabel('VOICE ONLY  ·  SAY COMMANDS TO J.A.R.V.I.S'); mic.setAlignment(Qt.AlignmentFlag.AlignCenter); mic.setWordWrap(True)
        mic.setStyleSheet(f'color:{C.TEXT_MED};font-size:8px;background:rgba(0,25,35,120);border:1px solid {C.BORDER};border-radius:9px;padding:8px;'); l.addWidget(mic)
        return f

    def _build_floating_panel(self, widget, kind):
        f=self._panel_base(); f.setProperty('_jarvis_panel_kind', str(kind))
        l=QVBoxLayout(f); l.setContentsMargins(8,8,8,8); l.setSpacing(6)
        titles={'video':'VIDEO PREVIEW','model':'3D DISPLAY','web':'WEBVIEW','task':'WEB TASK','world':'SYSTEM MONITOR','memory':'MEMORY CORE','window':'WINDOW','tools':'TOOLS DECK'}
        icons={'video':'▶','model':'◇','web':'🌐','task':'⌁','world':'◎','memory':'🧠','window':'▣','tools':'▣'}
        subtitles={'video':'LOCAL MEDIA','model':'HOLOGRAM','web':'WEB CONSOLE','task':'RESEARCH QUEUE','world':'LIVE TELEMETRY','memory':'LONG-TERM STORE','window':'WORKSPACE','tools':'COMMAND DECK'}
        self._panel_header(f, icons.get(kind,'✦'), lambda:self._set_panel_visible(f,False), titles.get(kind,'HUD WINDOW'), subtitles.get(kind,''))
        l.addWidget(widget,1)
        self._install_panel_drag_surface(f)
        return f

    def _install_panel_drag_surface(self, panel):
        if panel is None:
            return
        panel.setProperty('_jarvis_floating_panel', True)
        panel.setCursor(Qt.CursorShape.ArrowCursor)
        panel.installEventFilter(self)
        panel_kind = str(panel.property('_jarvis_panel_kind') or 'panel')
        panel.setObjectName(f'JarvisPanel_{panel_kind}')
        # The old 4px transparent drag strip has been removed: it intercepted
        # clicks in a band of the header and, when the main window resized,
        # MainWindow.resizeEvent moved *one arbitrary panel's* strip using the
        # main window's width.  Drag now lives on the header widgets themselves
        # (_jarvis_panel_drag tags on the bar, icon, and title labels).

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Intentionally empty — the old drag-strip repositioning was removed
        # (see _install_panel_drag_surface).  Each panel's drag zone is now
        # self-contained and cannot be corrupted by another window's resize.

    def _remember_panel_position(self, panel):
        try:
            if panel is None or not panel.isWindow():
                return
            key = str(panel.property('_jarvis_panel_key') or panel.property('_jarvis_panel_kind') or panel.objectName() or '')
            if not key:
                return
            positions = self._features.get('panel_positions') if isinstance(self._features, dict) else None
            if not isinstance(positions, dict):
                positions = {}
            positions[key] = {
                'x': int(panel.x()),
                'y': int(panel.y()),
                'w': int(panel.width()),
                'h': int(panel.height()),
            }
            self._features['panel_positions'] = positions
            _ui_save(API_FILE, features=self._features)
        except Exception:
            pass

    def _schedule_panel_autosave(self, panel):
        try:
            if panel is None or not panel.isWindow():
                return
            if not hasattr(self, '_panel_save_timers'):
                self._panel_save_timers = {}
            old = self._panel_save_timers.pop(panel, None)
            if old is not None:
                try: old.stop()
                except Exception: pass
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=panel, t=timer: (self._remember_panel_position(p), self._panel_save_timers.pop(p, None)))
            self._panel_save_timers[panel] = timer
            timer.start(220)
        except Exception:
            pass

    def _restore_panel_position(self, panel):
        try:
            positions = self._features.get('panel_positions', {}) if isinstance(self._features, dict) else {}
            key = str(panel.property('_jarvis_panel_key') or panel.property('_jarvis_panel_kind') or panel.objectName() or '')
            pos = positions.get(key) if isinstance(positions, dict) else None
            if isinstance(pos, dict):
                x = int(pos.get('x', panel.x()))
                y = int(pos.get('y', panel.y()))
                w = int(pos.get('w', 0) or 0)
                h = int(pos.get('h', 0) or 0)
                if w > 0 and h > 0:
                    panel.resize(max(panel.minimumWidth(), w), max(panel.minimumHeight(), h))
                panel.move(x, y)
                return True
        except Exception:
            pass
        return False

    def _build_model_panel(self):
        f = self._panel_base(); f.setMinimumSize(460, 380)
        l = QVBoxLayout(f); l.setContentsMargins(7,7,7,7); l.setSpacing(6)
        self._panel_header(f, '◇', lambda: self._set_panel_visible(f, False), '3D DISPLAY', 'HOLOGRAM')
        self._model_view = Model3DView()
        l.addWidget(self._model_view, 1)
        self._model_status = QLabel('No 3D model loaded')
        self._model_status.setStyleSheet(f'color:{C.TEXT_DIM};font:8pt "Exo 2";background:transparent;')
        l.addWidget(self._model_status)
        row = QHBoxLayout()
        open_btn = _GlowButton('OPEN MODEL', '＋', compact=True); open_btn.clicked.connect(self._open_model); row.addWidget(open_btn)
        reset = _GlowButton('RESET', '↻', compact=True); reset.clicked.connect(self._model_view.reset_view); row.addWidget(reset)
        wire = _GlowButton('WIREFRAME', '◇', compact=True); wire.clicked.connect(self._model_view.toggle_wireframe); row.addWidget(wire)
        row.addStretch(1); l.addLayout(row)
        return f

    def _open_3d_panel(self):
        self._open_3d_display()

    def _open_3d_display(self):
        path,_=QFileDialog.getOpenFileName(self,'Open 3D object',str(Path.home()),'3D Models (*.obj *.stl *.ply *.off *.glb *.gltf *.dae *.3ds *.fbx);;All Files (*.*)')
        if not path: return
        try:
            if getattr(self,'_three_d_display',None) is None:
                self._three_d_display=_3DDisplayWindow(self)
                self._three_d_display.setProperty('_jarvis_floating_panel', True)
                self._three_d_display.setProperty('_jarvis_panel_kind', '3d_hologram')
                self._three_d_display.setProperty('_jarvis_panel_key', '3d_hologram')
            self._three_d_display.load_model(path)
            sg=QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
            if sg:
                w,h=self._three_d_display.width(),self._three_d_display.height()
                self._three_d_display.move(sg.left()+(sg.width()-w)//2,sg.top()+(sg.height()-h)//2)
            self._three_d_display.show(); self._three_d_display.raise_(); self._three_d_display.activateWindow()
        except Exception as exc:
            self.write_log(f'ERR: 3D display — {exc}')

    def control_jarvis_window(self, window: str, action: str = "open", query: str = ""):
        w = str(window or "webview").strip().lower().replace(" ", "_").replace("-", "_")
        a = str(action or "open").strip().lower()
        aliases = {
            "web": "webview", "browser": "webview", "search": "webview",
            "images": "image", "image_preview": "image", "imageviewer": "image",
            "world": "world_monitor", "worldmonitor": "world_monitor",
            "video_preview": "video", "player": "video",
            "task": "web_task", "webtask": "web_task",
            "tools": "tools", "toolbox": "tools", "tool": "tools",
            "system": "world_monitor", "telemetry": "world_monitor",
            "sysmon": "world_monitor", "performance": "world_monitor",
            "memory_core": "memory", "memorycore": "memory",
            "activity_log": "activity", "log": "activity",
            "content_surface": "content", "research": "content",
            "model": "3d", "3d_display": "3d", "hologram": "3d",
            "camera": "live_camera", "webcam": "live_camera",
            "my_camera": "live_camera", "camera_view": "live_camera",
            "live_camera": "live_camera", "vision": "live_camera",
        }
        w = aliases.get(w, w)
        panels = {
            "video": self._video_panel, "image": self._image_panel,
            "activity": self._activity_panel, "content": self._content_panel,
            "3d": self._model_panel, "webview": self._webview_panel,
            "web_task": self._web_task_panel, "world_monitor": self._world_monitor_panel,
            "memory": self._memory_panel, "tools": self._tools_panel,
        }
        if w not in panels:
            return "Unknown JARVIS window. Available: Tools, WebView, Image, System Monitor, Video, Web Task, Memory, Activity, Content, and 3D."
        panel = panels[w]

        if a == "close":
            self._set_panel_visible(panel, False)
            return f"Closed the {w.replace('_', ' ')} window."

        if w == "webview":
            if a in ("search", "find"):
                q = query.strip()
                if not q:
                    return "Please provide something to search for."
                self._open_webview_panel()
                self._webview_pane._url.setText(q)
                self._webview_pane.navigate()
                return f"Searching for {q} inside the JARVIS WebView."
            if a in ("refresh", "reload"):
                self._open_webview_panel(); self._webview_pane.reload()
                return "Refreshed the JARVIS WebView."
            self._open_webview_panel(query if query else None)
            return "Opened the JARVIS WebView."

        if w == "world_monitor":
            self._open_world_monitor()
            if a == "refresh":
                try: self._world_monitor_pane.refresh_feeds()
                except Exception: pass
                return "Refreshed the JARVIS World Monitor."
            return "Opened the JARVIS World Monitor."

        if w == "image":
            self._open_image_panel()
            if a == "search" and query:
                try:
                    from urllib.parse import quote_plus
                    self._open_webview_panel('https://www.google.com/search?tbm=isch&q=' + quote_plus(query))
                    return f"Opened image search for {query} inside the JARVIS WebView."
                except Exception as exc:
                    return f"Could not open image search: {exc}"
            return "Opened the JARVIS Image Preview."

        if w == "video":
            self._open_video_panel()
            return "Opened the JARVIS Video Preview."
        if w == "live_camera":
            enabled = (a != "close")
            self.toggle_camera_overlay(enabled)
            return ("Camera closed." if not enabled
                    else "Live camera view opened.")
        if w == "web_task":
            self._open_web_task_panel()
            return "Opened the JARVIS Web Task window."
        if w == "tools":
            self._open_tools_panel()
            return "Opened the JARVIS Tools Deck."
        if w == "memory":
            self._open_memory_panel()
            return "Opened the JARVIS Memory Core."
        if w == "activity":
            self._open_activity_panel()
            return "Opened the JARVIS Activity Log."
        if w == "content":
            self._set_panel_visible(self._content_panel, True)
            return "Opened the JARVIS Content Surface."
        if w == "3d":
            self._set_panel_visible(self._model_panel, True)
            return "Opened the JARVIS 3D Display. Use OPEN MODEL inside it to load a file."
        return "Done."

    def _open_video_panel(self):
        self._set_panel_visible(self._video_panel, True)

    def _open_image_panel(self):
        self._set_panel_visible(self._image_panel, True)

    def play_web_music(self, query: str):
        from urllib.parse import quote_plus
        q = str(query or '').strip()
        if not q:
            q = 'music'
        if not getattr(self, '_music_webview_enabled', True):
            return 'WebView music is disabled. Enable "Play music inside the internal WebView" in Control Center → Web.'
        pane = getattr(self, '_webview_pane', None)
        panel = getattr(self, '_webview_panel', None)
        if pane is None or panel is None:
            return 'The internal WebView panel is unavailable.'
        if not getattr(pane, 'engine_enabled', False):
            return 'Internal WebView is disabled. Enable it in Control Center → Web to play music inside JARVIS.'
        url = 'https://www.youtube.com/results?search_query=' + quote_plus(q)
        try:
            self._set_panel_visible(panel, True)
            pane._url.setText(url)
            engine = getattr(pane, '_engine', None)
            if engine is None:
                return 'The embedded WebView engine is unavailable.'
            def _after_load(ok):
                if not ok:
                    return
                js = r"""
                    (() => {
                      const pick = () => {
                        const selectors = [
                          'a#video-title',
                          'a#video-title-link',
                          'ytd-video-renderer a#video-title',
                          'a[href*="watch?v="]'
                        ];
                        for (const sel of selectors) {
                          const el = [...document.querySelectorAll(sel)].find(x => x && x.offsetParent !== null);
                          if (el) { el.click(); return true; }
                        }
                        return false;
                      };
                      if (pick()) return;
                      let n = 0;
                      const timer = setInterval(() => {
                        if (pick() || ++n > 12) clearInterval(timer);
                      }, 700);
                    })();
                """
                try: engine.page().runJavaScript(js)
                except Exception: pass
            old = getattr(pane, '_music_load_hook', None)
            if old is not None:
                try: engine.loadFinished.disconnect(old)
                except Exception: pass
            pane._music_load_hook = _after_load
            engine.loadFinished.connect(_after_load)
            engine.setUrl(QUrl(url))
            pane._status.setText(f'MUSIC · SEARCHING {q[:52]}')
            return f"Opened music for {q} inside the JARVIS WebView."
        except Exception as exc:
            return f"Music WebView error: {exc}"

    def _open_webview_panel(self, url=None):
        if url:
            try:
                self._webview_pane._url.setText(str(url))
                self._webview_pane.navigate()
            except Exception:
                pass
        self._set_panel_visible(self._webview_panel, True)

    def _open_web_task_panel(self):
        self._set_panel_visible(self._web_task_panel, True)

    def _open_tools_panel(self):
        try:
            self._tools_pane.reload()
        except Exception:
            pass
        self._set_panel_visible(self._tools_panel, True)

    def get_tool_entries(self) -> list:
        feats = self._features if isinstance(getattr(self, '_features', None), dict) else {}
        def _on(*keys, default=True):
            try:
                return bool(feats.get(keys[0], default)) if len(keys) == 1 else any(bool(feats.get(k, default)) for k in keys)
            except Exception:
                return True
        web_ok  = _on('webview', 'webview_engine')
        comp_ok = _on('computer_control')
        core = [
            ('open_app', 'Computer Control', '▣', 'Open any application by name.', 'Open the calculator app.', comp_ok),
            ('computer_settings', 'Computer Control', '◐', 'Volume, brightness, windows, power and shortcuts.', 'Turn the volume up slightly.', comp_ok),
            ('computer_control', 'Computer Control', '⌖', 'See and control the screen, mouse and keyboard.', 'Take a screenshot of my screen.', comp_ok),
            ('desktop_control', 'Computer Control', '⊞', 'Wallpaper, icons, desktop organization and stats.', 'Organize my desktop.', comp_ok),
            ('browser_control', 'Browser', '🌐', 'Drive the web browser: open, click, fill, scroll.', 'Open example.com in the browser.', web_ok),
            ('web_search', 'Browser', '⌕', 'Search, news, research, prices and comparisons.', 'Search the web for quantum computing news.', web_ok),
            ('youtube_video', 'Browser', '▶', 'Play, summarize and inspect YouTube videos.', 'Play a relaxing jazz video.', web_ok),
            ('play_music', 'Browser', '♫', 'Play music inside the JARVIS WebView.', 'Play some lofi music.', web_ok and _on('webview_music')),
            ('flight_finder', 'Browser', '✈', 'Find flights with live options and prices.', 'Find flights from London to Tokyo.', web_ok),
            ('file_controller', 'Files', '🗀', 'List, create, move, copy, rename and inspect files.', 'List the files on my desktop.', _on('file_control')),
            ('file_processor', 'Files', '⬣', 'Summarize, convert and analyze documents and media.', 'Summarize the selected file.', _on('file_control')),
            ('desktop_control', 'Files', '🗄', 'Desktop-level file organization helpers.', 'Show my disk usage.', _on('file_control')),
            ('screen_process', 'Screen', '◉', 'Capture and understand screen or webcam.', 'Look at my screen and tell me what you see.', _on('screen_understanding')),
            ('close_camera', 'Screen', '◌', 'Close the live camera view.', 'Close the camera.', True),
            ('play_local_video', 'Media', '🎬', 'Play a local video/audio file in the media panel.', 'Play my video file.', True),
            ('generate_image', 'Media', '◈', 'Generate AI imagery from a description.', 'Generate an image of a futuristic city.', True),
            ('system_status', 'System', '⬢', 'Live CPU, RAM, GPU, temperature and uptime.', 'What is the status of my system?', _on('desktop_awareness')),
            ('reminder', 'System', '⏰', 'Set timed reminders via the task scheduler.', 'Remind me in 10 minutes to stretch.', True),
            ('manage_monitor', 'System', '◎', 'Track topics and get daily briefings.', 'What topics are being monitored?', True),
            ('game_updater', 'System', '🎮', 'Update and manage Steam / Epic games.', 'Check for game updates.', True),
            ('shutdown_jarvis', 'System', '⏻', 'End the session and shut JARVIS down.', 'Goodbye.', True),
            ('jarvis_window', 'Automation', '▤', 'Open JARVIS windows: web, memory, activity, 3D.', 'Open the webview window.', True),
            ('send_message', 'Automation', '✉', 'Send WhatsApp / Telegram messages.', 'Send a message.', True),
            ('file_processor', 'Automation', '⚙', 'Batch-process the selected file.', 'Process the selected file.', _on('file_control')),
            ('code_helper', 'Coding', '⌨', 'Write, explain, run and fix code.', 'Write a Python hello world script.', True),
            ('dev_agent', 'Coding', '⛭', 'Build complete multi-file projects.', 'Build me a Pomodoro timer app.', True),
            ('save_memory', 'Memory', '◆', 'Remember a fact about the user.', 'Remember that I like espresso.', True),
            ('recall_memory', 'Memory', '◇', 'Recall stored facts on demand.', 'What do you remember about me?', True),
            ('undo', 'Memory', '↩', 'Undo the last change JARVIS made.', 'Undo that.', True),
        ]
        entries = []
        for name, cat, icon, desc, run, avail in core:
            entries.append({'name': name, 'category': cat, 'icon': icon,
                            'desc': desc, 'run': run, 'available': bool(avail)})
        entries.append({'name': 'web_task_queue', 'category': 'Automation', 'icon': '☰',
                        'desc': 'Queue search, news and research jobs.', 'run': '',
                        'available': _on('web_task'), 'action': 'open_web_task'})
        try:
            plugins = self.get_plugins() if getattr(self, 'get_plugins', None) else []
        except Exception:
            plugins = []
        for p in plugins or []:
            try:
                pname = str(p.get('name', 'plugin'))
                entries.append({'name': pname, 'category': 'Plugins', 'icon': '⬢',
                                'desc': str(p.get('description', 'Community plugin.'))[:140],
                                'run': f'Use the {pname} plugin.',
                                'available': bool(p.get('enabled', True) and p.get('valid', True))})
            except Exception:
                pass
        return entries

    def _open_vision_preview(self):
        try:
            self.start_camera_stream()
        except Exception as exc:
            self.write_log(f'ERR: Vision preview — {exc}')

    def _open_world_monitor(self):
        self._set_panel_visible(self._world_monitor_panel, True)
        try:
            self._world_monitor_pane.refresh_feeds()
        except Exception:
            pass

    def _open_memory_panel(self):
        try:
            self._memory_pane.reload()
        except Exception:
            pass
        self._set_panel_visible(self._memory_panel, True)

    def _open_brain_panel(self):
        if self._brain_panel is None:
            try:
                from dashboard.brain3d import BrainPanel
                self._brain_pane  = BrainPanel()
                self._brain_panel = self._build_floating_panel(self._brain_pane, 'brain')
            except Exception as exc:
                self.write_log(f'ERR: Brain panel — {exc}')
                return
        try:
            self._brain_pane.graph.refresh()
        except Exception:
            pass
        self._set_panel_visible(self._brain_panel, True)

    def set_brain_activity(self, activity: str = ""):
        activity = activity or "idle"
        try:
            if hasattr(self, "_brain_pane") and self._brain_pane is not None:
                self._brain_pane.set_activity(activity)
        except Exception:
            pass
        try:
            if hasattr(self, "_memory_pane") and getattr(self._memory_pane, "_orbit", None) is not None:
                self._memory_pane._orbit.set_activity(activity)
        except Exception:
            pass

    def _open_activity_panel(self):
        self._set_panel_visible(self._activity_panel, True)

    def _spawn_workspace_window(self):
        pane = WorkspaceWindowPane(title=f'WINDOW {len(self._workspace_windows)+1:02d}')
        win = self._build_floating_panel(pane, 'window')
        win.setProperty('_jarvis_panel_key', f'new_window_{len(self._workspace_windows)+1:02d}')
        self._workspace_windows.append(win)
        self._set_panel_visible(win, True)

    def _on_web_task(self, payload: str):
        text = str(payload or '')
        if text.startswith('__webview__:'):
            self._open_webview_panel(text.split(':', 1)[-1].strip())
            return
        self._send_backend_command(text)

    def _build_activity_panel(self):
        f=self._panel_base(); f.setMinimumSize(400,320); l=QVBoxLayout(f); l.setContentsMargins(12,10,12,10); l.setSpacing(8)
        self._panel_header(f,'◉',lambda:self._set_panel_visible(f,False),'ACTIVITY','LIVE TIMELINE')
        filt_row=QHBoxLayout(); filt_row.setSpacing(6)
        filt_lbl=QLabel('SHOW'); filt_lbl.setStyleSheet("color:#5796ad;font:700 7pt 'Exo 2';background:transparent;")
        filt_row.addWidget(filt_lbl)
        self._activity_filter=QComboBox(); self._activity_filter.addItems(['ALL','VOICE','AI','TOOL','WEB','FILE','OK','WARN','ERROR','SYS'])
        self._activity_filter.setStyleSheet(_hud_field_css()); self._activity_filter.setMinimumHeight(28)
        self._activity_filter.currentTextChanged.connect(lambda t: self._activity.set_filter(t))
        filt_row.addWidget(self._activity_filter,1)
        l.addLayout(filt_row)
        self._activity=_ActivityTimeline(); l.addWidget(self._activity,1); self._activity_add('SYS: UI online'); return f

    def _build_image_panel(self):
        f=self._panel_base(); f.setMinimumSize(500,420)
        l=QVBoxLayout(f); l.setContentsMargins(8,8,8,8); l.setSpacing(7)
        self._panel_header(f,'▧',lambda:self._set_panel_visible(f,False),'IMAGE PREVIEW', 'STILLS & ANALYSIS')
        top=QWidget(f); top.setStyleSheet("background:rgba(3,20,30,140);border:1px solid rgba(74,196,239,120);border-radius:9px;")
        tr=QHBoxLayout(top); tr.setContentsMargins(8,6,8,6); tr.setSpacing(6)
        self._image_status=QLabel('IMAGE PREVIEW · NO FILE'); self._image_status.setWordWrap(False); self._image_status.setStyleSheet("color:#9ae8ff;font:700 8pt 'Exo 2';background:transparent;"); tr.addWidget(self._image_status,1)
        open_btn=_GlowButton('OPEN','＋',compact=True); open_btn.clicked.connect(self._open_image_file); tr.addWidget(open_btn)
        clear_btn=_GlowButton('CLEAR','×',compact=True); clear_btn.clicked.connect(lambda:self._image_label.clear()); tr.addWidget(clear_btn)
        top.setCursor(Qt.CursorShape.SizeAllCursor); top.installEventFilter(self); top.setProperty('_jarvis_panel_drag',True); l.addWidget(top)
        self._image_label=QLabel(); self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self._image_label.setMinimumHeight(300); self._image_label.setStyleSheet(_theme_card_css() + "color:#5796ad;"); self._image_label.setScaledContents(False); l.addWidget(self._image_label,1)
        return f

    def _open_image_file(self):
        p,_=QFileDialog.getOpenFileName(self,'Open image',str(Path.home()),'Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff)')
        if p: self.show_image_path(p,'Loaded image')

    def _build_content_panel(self):
        f=self._panel_base(); f.setMinimumSize(560,360); l=QVBoxLayout(f); l.setContentsMargins(7,7,7,7); l.setSpacing(5)
        self._panel_header(f,'⌕',lambda:self._set_panel_visible(f,False),'CONTENT SURFACE', 'RESEARCH & DOCS')
        self._content_title=QLabel(''); self._content_title.setStyleSheet("color:#dff8ff;font:700 10pt 'Orbitron';letter-spacing:2px;background:transparent;border:none;padding:2px 7px;"); l.addWidget(self._content_title)
        self._content_text=QTextEdit(); self._content_text.setReadOnly(True); self._content_text.setStyleSheet(_hud_field_css() + "QTextEdit{padding:10px;}"); l.addWidget(self._content_text,1); return f

    def _profile_avatar_path_get(self) -> str | None:
        """Return the cached circular avatar path, creating it from the configured image."""
        try:
            cfg = _read_full_config()
            src = cfg.get('profile_image', '') or ''
            if not src or not os.path.exists(src):
                return None
            out = CONFIG_DIR / '_avatar_circle.png'
            try:
                from PIL import Image, ImageDraw, ImageOps
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                img = Image.open(src).convert('RGBA')
                side = min(img.size)
                img = ImageOps.fit(img, (side, side))
                img = img.resize((128, 128), Image.LANCZOS)
                mask = Image.new('L', (128, 128), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 128, 128), fill=255)
                img.putalpha(mask)
                img.save(out, 'PNG')
                return str(out)
            except Exception:
                return None
        except Exception:
            return None

    def _pick_profile_image(self) -> None:
        """Choose and persist a profile picture."""
        try:
            p, _ = QFileDialog.getOpenFileName(
                self, 'Choose your profile picture', str(Path.home()),
                'Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)'
            )
            if not p:
                return
            # Copy the chosen image into JARVIS config so the default profile
            # picture remains available even if the original file is moved.
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            ext = Path(p).suffix.lower() or '.png'
            cached = CONFIG_DIR / f'_profile_default{ext}'
            shutil.copy2(p, cached)
            _ui_save(API_FILE, profile_image=str(cached))
            self._profile_avatar_path = self._profile_avatar_path_get()
            if getattr(self, '_profile_btn', None) is not None:
                self._profile_btn.update()
            if _sfx_enabled(self):
                _sfx('open')
        except Exception as exc:
            try:
                self.write_log(f'ERR: Profile image — {exc}')
            except Exception:
                pass

    def _paint_profile_btn(self, _e) -> None:
        """Paint the profile button as a circular photo or Y placeholder."""
        btn = self._profile_btn
        p = QPainter(btn)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0, 0, btn.width(), btn.height())
        src_now = getattr(self, '_profile_avatar_path', None)
        if src_now and os.path.exists(src_now):
            # Only draw the ring when a photo is present, so an empty
            # profile slot is 100% invisible against the puck.
            p.setPen(QPen(QColor(88, 214, 245, 220), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))

        path = QPainterPath()
        path.addEllipse(r.adjusted(3, 3, -3, -3))
        p.setClipPath(path)
        src = getattr(self, '_profile_avatar_path', None)
        if src and os.path.exists(src):
            pix = QPixmap(src)
            if not pix.isNull():
                p.drawPixmap(3, 3, btn.width() - 6, btn.height() - 6, pix)
        else:
            # No fill at all — the ring outline is already drawn above, and
            # the interior stays 100% transparent so the puck's surface
            # (and its panel gradient behind it) shows through untouched.
            p.setPen(QColor(C.PRI))
            p.setFont(QFont('Rajdhani', 12, QFont.Weight.Bold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, '●')
        p.end()

    def _open_full_settings(self):
        try:
            win = getattr(self, "_full_settings_window", None)
            try:
                if win is not None:
                    win.windowTitle()
            except RuntimeError:
                win = None
            if win is None:
                win = SettingsWindow(config_path=API_FILE, parent=None)
                try:
                    win.setProperty('_jarvis_floating_panel', True)
                    win.setProperty('_jarvis_panel_kind', 'control_center')
                    win.setProperty('_jarvis_panel_key', 'control_center')
                except Exception:
                    pass
                self._full_settings_window = win
                win.settings_saved.connect(self._apply_full_settings)
                win.voice_test_requested.connect(self._send_backend_command)
                try:
                    win.get_plugins = self.get_plugins
                    from memory.config_manager import save_plugin_enabled as _save_plug
                    win.set_plugin_enabled = _save_plug
                    win.get_activity_log = self.get_activity_lines
                except Exception:
                    pass
            win.show()
            win.raise_()
            win.activateWindow()
            if not getattr(win, "_jarvis_positioned", False):
                sg = QApplication.primaryScreen().availableGeometry()
                win.move(sg.left() + (sg.width() - win.width()) // 2,
                         sg.top() + (sg.height() - win.height()) // 2)
                win._jarvis_positioned = True
        except Exception as exc:
            self.write_log(f"ERR: Settings window — {exc}")

    def _apply_full_settings(self, data):
        try:
            try:
                _scale = int(float(data.get('chat_font_scale', 100) or 100))
                _mode = str(data.get('chat_filter', 'all') or 'all')
                LogWidget._FILTER_cfg['scale'] = _scale
                LogWidget._FILTER_cfg['mode'] = _mode
                _cats = data.get('chat_filter_categories', {})
                if isinstance(_cats, dict):
                    self._chat_filter_categories = {k: bool(_cats.get(k, True)) for k in ('sys','vis','jarvis','you','file','err','warn')}
                    LogWidget._FILTER_cfg['categories'] = dict(self._chat_filter_categories)
                for lw in self.findChildren(LogWidget):
                    lw.set_font_scale(_scale); lw.set_filter(_mode)
                    if hasattr(lw, 'set_filter_categories'): lw.set_filter_categories(self._chat_filter_categories)
            except Exception:
                pass
            self._font_family = str(data.get('ui_font', self._font_family))
            self._panel_alpha = max(0, min(100, int(data.get('ui_opacity', self._panel_alpha))))
            self._compact_size = max(170, min(500, int(data.get('compact_size', self._compact_size))))
            self._reactor_opacity = max(0, min(100, int(data.get('reactor_opacity', self._reactor_opacity))))
            self._reactor_stroke_opacity = max(0, min(100, int(data.get('reactor_stroke_opacity', self._reactor_stroke_opacity))))
            self._reactor_speed = max(0.25, min(2.5, float(data.get('reactor_animation_speed', self._reactor_speed))))
            self._eq_sensitivity = max(0.1, min(3.0, float(data.get('equalizer_sensitivity', self._eq_sensitivity))))
            feats = data.get('features')
            if isinstance(feats, dict):
                self._features.update(feats)
            try:
                new_name = str(data.get('assistant_name', '') or '').strip()
                new_user = str(data.get('user_name', '') or '').strip()
                if new_name and (new_name != getattr(self, '_assistant_name', '') or
                                 new_user != _read_full_config().get('user_name', '')):
                    self._apply_name_update(new_name, new_user)
            except Exception as exc:
                self.write_log(f'ERR: Identity update — {exc}')
            try:
                if not bool(self._features.get('mic_access', True)) and not self._muted:
                    self._toggle_mute()
            except Exception:
                pass
            _voice_keys = tuple(getattr(self, '_active_voice_config', {}).keys())
            voice_changed = bool(_voice_keys) and any(str(data.get(k)) != str(getattr(self, '_active_voice_config', {}).get(k)) for k in _voice_keys)
            # Gemini's Live voice is persisted ONLY when it actually changed —
            # a save for any other provider (Fish Audio, ElevenLabs, …) must
            # not rewrite the Gemini voice. This was the root of the "I saved
            # Fish Audio and Charon got saved" bug.
            voice_name = str(data.get('voice_name', '') or '').strip()
            if voice_name and voice_changed:
                try:
                    from memory.config_manager import save_voice
                    save_voice(voice_name)
                except Exception:
                    pass
            if voice_changed:
                self._active_voice_config = {k: data.get(k) for k in _voice_keys}
            try:
                from core.tts import clear_tts_cache
                clear_tts_cache()
            except Exception:
                pass
            try:
                ww = data.get('wake_words')
                if isinstance(ww, list) and ww:
                    from core.brain_bridge import reconfigure_wake
                    reconfigure_wake(words=[str(w) for w in ww])
            except Exception:
                pass
            self._drag_reactor_enabled = bool(self._features.get('drag_reactor', True))
            self._music_webview_enabled = bool(self._features.get('webview_music', True))
            app = QApplication.instance()
            if app:
                app.setFont(QFont(self._font_family, 10))
            color = str(data.get('ui_color', '') or '')
            if color:
                old = current_palette()
                if apply_ui_accent(color):
                    retheme_all_widgets(old, current_palette())
            if getattr(self, '_collapsed', True):
                if self._defer_layout_until_settled():
                    pass
                else:
                    self.setMinimumSize(QSize(self._compact_size, self._compact_size))
                    self.setMaximumSize(QSize(self._compact_size, self._compact_size))
                    self.resize(self._compact_size, self._compact_size)
                    self._apply_compact_style()
                    self._header_logo.set_logo_size(self._settled_logo_size())
                    self._header_logo.setGeometry(
                        max(0, (self._compact_size - self._header_logo.width()) // 2),
                        max(0, (self._compact_size - self._header_logo.height()) // 2),
                        self._header_logo.width(), self._header_logo.height())
                    self._header_logo.raise_()
            else:
                self.surface.setStyleSheet(self._surface_style())
            self._header_logo.set_graphic_opacity(self._reactor_opacity)
            self._header_logo.set_stroke_opacity(self._reactor_stroke_opacity)
            self._header_logo._animation_speed = self._reactor_speed
            home = str(data.get('web_homepage') or '')
            if home and getattr(self, '_webview_pane', None) is not None:
                self._webview_pane._home = home
            if getattr(self, '_webview_pane', None) is not None and isinstance(feats, dict):
                self._webview_pane._engine_toggle.setChecked(bool(feats.get('webview_engine', True)))
                # Apply the WebView script toggle to the LIVE engine — a saved
                # setting that only takes effect after a restart looks broken.
                try:
                    _eng = getattr(self._webview_pane, '_engine', None)
                    if _eng is not None and _WEB_OK and QWebEngineSettings is not None:
                        _eng.settings().setAttribute(
                            QWebEngineSettings.WebAttribute.JavascriptEnabled,
                            bool(feats.get('webview_js', True)))
                except Exception:
                    pass
            self._hide_taskbar_button = bool(self._features.get('hide_taskbar_button', self._features.get('hide_taskbar', False)))
            self._set_taskbar_button_hidden(self._hide_taskbar_button)
            try:
                from core import sfx as _sfx_mod
                _sfx_mod.configure(
                    enabled=bool(self._features.get('enable_sfx', True)),
                    volume=float(data.get('voice_volume', 1.0) or 1.0) * 0.35,
                )
            except Exception:
                pass
            # Never persist the mid-animation position as the remembered home;
            # the settle's own finalize saves the target position.
            if getattr(self, '_startup_phase', None) is _StartupPhase.SETTLED:
                self._save_window_position()
            if voice_changed:
                # Say what will actually speak: a raw 'gemini' value can resolve
                # to the Piper pipeline (one-time migration), and the execution
                # provider decides whether Piper runs on the CPU or a GPU.
                _mode = str(data.get('voice_output_mode', '') or '').lower()
                try:
                    from core.hybrid_voice import resolve_voice_mode
                    _mode = resolve_voice_mode(data)[0]
                except Exception:
                    _mode = _mode or 'gemini'
                if _mode == 'gemini_piper':
                    _eng = f"PIPER · {str(data.get('onnx_execution_provider', 'cpu') or 'cpu').upper()}"
                else:
                    _eng = str(data.get('tts_engine', '')).upper()
                _out = _mode.upper()
                _voice_display = str(data.get('onnx_voice_model', '') or data.get('tts_voice', '') or data.get('voice_name', ''))
                self.write_log(f'SYS: Voice configuration applied — {_eng} / {_out} / {_voice_display}')
                if self.on_voice_change:
                    self.on_voice_change()
        except Exception as e:
            self.write_log(f'ERR: Applying settings — {e}')

    def _open_layout_editor(self):
        try:
            if self._layout_editor is None:
                self._layout_editor=LayoutEditorDialog(self)
            self._layout_editor.show()
        except Exception as e: self.write_log(f'ERR: Layout editor — {e}')

    def _hide_video_title(self, panel):
        for label in panel.findChildren(QLabel):
            if label.text().strip().upper() == 'VIDEO PREVIEW': label.hide()

    def _load_layout_elements(self):
        try:
            path=CONFIG_DIR/'ui_layout.json'; data=_load_layout_file(path); elems=data.get('elements',[]) if isinstance(data,dict) else []
            self._layout_elements={}
            for e in elems:
                if isinstance(e,dict) and e.get('name'):
                    self._layout_elements[str(e['name'])]=dict(e)
            self._apply_layout_elements()
        except Exception as e:
            self.write_log(f'ERR: Layout load — {e}')

    def _save_layout_elements(self):
        try:
            for name,w in self._layout_widgets.items():
                e=self._layout_elements.get(name)
                if e is not None:
                    g=w.geometry(); e.update({'x':g.x(),'y':g.y(),'w':g.width(),'h':g.height(),'visible':w.isVisible()})
            _save_layout_file(CONFIG_DIR/'ui_layout.json', {'elements':list(self._layout_elements.values())})
        except Exception as e:
            self.write_log(f'ERR: Layout save — {e}')

    def _apply_layout_elements(self):
        if not hasattr(self,'_layout_widgets'): return
        for name,w in list(self._layout_widgets.items()):
            if name not in self._layout_elements:
                w.deleteLater(); self._layout_widgets.pop(name,None)
        parent=self.centralWidget()
        for name,e in self._layout_elements.items():
            kind=str(e.get('kind','Text')); text=str(e.get('text',name)); color=str(e.get('color',C.PRI))
            w=self._layout_widgets.get(name)
            if w is None:
                w=_LayoutElementWidget(kind,text,color,parent); self._layout_widgets[name]=w
            w.kind=kind; w._text=text; w._color=color; w.setGeometry(int(e.get('x',60)),int(e.get('y',60)),int(e.get('w',220)),int(e.get('h',50))); w.setVisible(bool(e.get('visible',True))); w.raise_()
            if w._label is not None and kind in ('Text','Button'): w._label.setStyleSheet((f'background:transparent;color:{color};font:700 13px "Rajdhani";' if kind=='Text' else f'QPushButton{{background:rgba(0,24,36,190);color:{color};border:1px solid {color};border-radius:8px;padding:6px 10px;font:700 9pt "Rajdhani";}}'))
            elif kind in ('Panel','Window','Monitor','WebView'): w.setStyleSheet(f'background:rgba(0,14,24,110);border:1px solid {color};border-radius:10px;')
            elif kind=='Separator': w.setStyleSheet(f'background:{color};border:none;')

    def _apply_initial_visibility(self):
        if self._defer_layout_until_settled():
            return
        self._hide_startup_logo_hitbox()
        self._topbar.hide(); self._main_row.hide(); self._statusbar.hide()
        self._apply_compact_style()
        if hasattr(self, '_root_layout'):
            self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._header_logo.setParent(self.surface)
        self._header_logo.set_logo_size(self._settled_logo_size())
        self._header_logo.setGeometry(max(0, (self._compact_size-self._header_logo.width())//2),
                                      max(0, (self._compact_size-self._header_logo.height())//2),
                                      self._header_logo.width(), self._header_logo.height())
        self._header_logo.raise_()
        self._center_logo()

    def _sync_startup_logo_hitbox(self) -> None:
        """Keep a top-level invisible click target over the whole startup reactor."""
        try:
            # The hitbox is only visible during STARTUP: not while the settle
            # timeline runs (it is hidden when the animation begins — this
            # check also avoids per-frame map/raise work that can stutter a
            # translucent Windows window), and never once settled.
            if getattr(self, '_startup_phase', None) is not _StartupPhase.STARTUP:
                return
            hit = getattr(self, '_startup_logo_hitbox', None)
            logo = getattr(self, '_header_logo', None)
            if hit is None or logo is None:
                return

            # The hit target intentionally lives directly under MainWindow.
            # This guarantees it is stacked above the logo, topbar, status and
            # other child widgets, so the entire reactor area is clickable.
            if hit.parentWidget() is not self:
                hit.setParent(self)

            center = logo.mapTo(self, QPoint(logo.width() // 2, logo.height() // 2))
            visual = float(max(120, int(getattr(logo, '_visual_logo_size',
                                               min(logo.width(), logo.height())))))

            # Include the painted cyan bloom and a generous interaction margin.
            paint_box = float(max(2, min(logo.width(), logo.height()) - 6))
            paint_scale = paint_box / 300.0
            glow_diameter = 2.0 * float(LOGO_RADIUS) * float(STARTUP_GLOW_SCALE) * paint_scale
            extent = max(visual, glow_diameter)
            pad = max(STARTUP_HITBOX_PAD, int(round(extent * 0.10)))
            half = int(round(extent * 0.5)) + pad
            size = max(2, half * 2)

            # Clamp only after calculating the full target so clicking near the
            # visible outer ring remains valid even when the logo nears an edge.
            x = max(0, min(self.width() - size, center.x() - half))
            y = max(0, min(self.height() - size, center.y() - half))
            r = QRect(int(x), int(y), int(min(size, self.width())), int(min(size, self.height())))
            if r.width() < 2 or r.height() < 2:
                return
            if hit.geometry() != r:
                hit.setGeometry(r)
            hit.raise_()
        except Exception:
            pass

    def _show_startup_logo_hitbox(self) -> None:
        try:
            hit = getattr(self, '_startup_logo_hitbox', None)
            if hit is None:
                return
            if hit.parentWidget() is not self:
                hit.setParent(self)
            self._sync_startup_logo_hitbox()
            hit.show()
            hit.raise_()
            hit.activateWindow() if hasattr(hit, 'activateWindow') else None
            timer = getattr(self, '_startup_logo_hitbox_timer', None)
            if timer is not None and not timer.isActive():
                timer.start()
        except Exception:
            pass

    def _hide_startup_logo_hitbox(self) -> None:
        try:
            hit = getattr(self, '_startup_logo_hitbox', None)
            if hit is not None:
                hit.hide()
            timer = getattr(self, '_startup_logo_hitbox_timer', None)
            if timer is not None:
                timer.stop()
        except Exception:
            pass

    def _center_logo(self):
        try:
            logo = self._header_logo
            logo.setGeometry(
                max(0, (self.width() - logo.width()) // 2),
                max(0, (self.height() - logo.height()) // 2),
                logo.width(), logo.height(),
            )
            self._sync_startup_logo_hitbox()
            if getattr(self, '_startup_phase', None) is _StartupPhase.STARTUP:
                hit = getattr(self, '_startup_logo_hitbox', None)
                if hit is not None:
                    hit.raise_()
        except Exception:
            pass

    def _apply_compact_style(self):
        self._compact_alpha = 0
        radius = max(20, self._compact_size // 2)
        self.surface.setStyleSheet(
            f'QFrame#Surface{{background:transparent;border:none;border-radius:{radius}px;}}'
        )

    def _set_taskbar_button_hidden(self, hidden: bool):
        if _OS != 'Windows':
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            if not hwnd:
                return
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            if hasattr(user32, 'GetWindowLongPtrW'):
                get_style = user32.GetWindowLongPtrW
                set_style = user32.SetWindowLongPtrW
            else:
                get_style = user32.GetWindowLongW
                set_style = user32.SetWindowLongW
            get_style.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
            set_style.restype = get_style.restype
            exstyle = int(get_style(hwnd, GWL_EXSTYLE))
            if hidden:
                exstyle = (exstyle & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
            else:
                exstyle = (exstyle & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            set_style(hwnd, GWL_EXSTYLE, exstyle)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        except Exception:
            pass

    def _clamp_position_to_screen(self, pos: QPoint, size: int | None = None) -> QPoint:
        try:
            w = max(1, int(size or self.width()))
            h = max(1, int(size or self.height()))
            center = pos + QPoint(w // 2, h // 2)
            screen = QApplication.screenAt(center)
            if screen is None:
                screen = QApplication.primaryScreen()
            if screen is None:
                return pos
            g = screen.availableGeometry()
            max_x = g.right() - w + 1
            max_y = g.bottom() - h + 1
            x = max(g.left(), min(int(pos.x()), max_x))
            y = max(g.top(), min(int(pos.y()), max_y))
            return QPoint(x, y)
        except Exception:
            return pos

    def _move_clamped(self, pos: QPoint, size: int | None = None):
        target = self._clamp_position_to_screen(pos, size)
        self.move(target)
        return target

    def _save_window_position(self):
        try:
            feats = self._features if isinstance(getattr(self, '_features', None), dict) else {}
            if feats.get('remember_position', True):
                safe = self._clamp_position_to_screen(self.pos(), min(self.width(), self.height()))
                if safe != self.pos():
                    self.move(safe)
                _ui_save(API_FILE, ui_x=self.x(), ui_y=self.y())
        except Exception:
            pass

    def _save_compact_settings(self):
        try:
            _ui_save(API_FILE, compact_opacity=self._compact_alpha, compact_size=self._compact_size)
        except Exception: pass

    def toggle_panels(self):
        self._toggle_command_window()

    def _toggle_command_window(self):
        try:
            win = getattr(self, '_command_window', None)
            try:
                if win is not None:
                    win.windowTitle()
            except RuntimeError:
                win = None
                self._command_window = None
            if win is not None and win.isVisible():
                if self._smooth():
                    self._fade_panel(win, False)
                else:
                    win.hide()
                if _sfx_enabled(self):
                    _sfx('close')
                return
            if win is None:
                win = _CommandWindow(self)
                self._command_window = win
                for entry in getattr(self, '_chat_history', [])[-20:]:
                    try:
                        plain = entry.replace('<b style="color:', '').replace('</b>', '')
                        win.chat.append(entry)
                    except Exception:
                        pass
            screen = QApplication.primaryScreen()
            sg = screen.availableGeometry() if screen is not None else None
            g = self.frameGeometry()
            if sg is not None:
                gap = 14
                w, h = win.width(), win.height()
                right_x = g.right() + gap
                left_x = g.left() - w - gap
                centered_y = g.top() + (g.height() - h) // 2
                if right_x + w <= sg.right():
                    x = right_x
                    y = centered_y
                elif left_x >= sg.left():
                    x = left_x
                    y = centered_y
                else:
                    x = sg.left() + (sg.width() - w) // 2
                    y = g.bottom() + gap
                    if y + h > sg.bottom():
                        y = g.top() - h - gap
                x = max(sg.left() + 8, min(x, sg.right() - w - 8))
                y = max(sg.top() + 8, min(y, sg.bottom() - h - 8))
                win.move(x, y)
            else:
                win.move(self.pos() + QPoint(self.width() + 14, 0))
            # The circle logo always brings the chat log forward.
            ensure_chat = getattr(win, "_show_chat_tab", None)
            if callable(ensure_chat):
                ensure_chat()
            win.show()
            win.raise_()
            win.activateWindow()
            if self._smooth():
                self._fade_panel(win, True)
            if _sfx_enabled(self):
                _sfx('open')
        except Exception as exc:
            self.write_log(f'ERR: Command window — {exc}')

    def _toggle_quick_popup(self):
        if self._quick_popup is not None and self._quick_popup.isVisible():
            self._animate_quick_popup(False)
            return
        if self._quick_popup is None:
            self._quick_popup = self._build_quick_popup()
        self._quick_popup.adjustSize()
        g = self.frameGeometry()
        screen = QApplication.primaryScreen().availableGeometry()
        x = g.right() + 12
        if x + self._quick_popup.width() > screen.right():
            x = g.left() - self._quick_popup.width() - 12
        y = g.top() + max(0, (g.height() - self._quick_popup.sizeHint().height()) // 2)
        y = min(max(screen.top()+8, y), screen.bottom() - self._quick_popup.height() - 8)
        target = QRectF(x, y, self._quick_popup.width(), self._quick_popup.height()).toRect()
        self._animate_quick_popup(True, target)

    def _animate_quick_popup(self, show: bool, target=None):
        if not self._quick_popup:
            return
        panel = self._quick_popup
        panel.adjustSize()
        target = target or panel.geometry()
        if show:
            sw, sh = max(200, int(target.width()*0.84)), max(120, int(target.height()*0.84))
            start = target.__class__(0, 0, sw, sh); start.moveCenter(target.center())
            panel.setGeometry(start); panel.show(); panel.raise_(); panel.activateWindow()
            eff = QGraphicsOpacityEffect(panel); panel.setGraphicsEffect(eff); eff.setOpacity(0.0)
            opacity = QPropertyAnimation(eff, b'opacity', panel); opacity.setDuration(220); opacity.setStartValue(0.0); opacity.setEndValue(1.0); opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
            geom = QPropertyAnimation(panel, b'geometry', panel); geom.setDuration(220); geom.setStartValue(start); geom.setEndValue(target); geom.setEasingCurve(QEasingCurve.Type.OutCubic)
            opacity.start(); geom.start(); self._quick_anim = (opacity, geom)
        else:
            start = panel.geometry(); ew, eh = max(180, int(start.width()*0.84)), max(110, int(start.height()*0.84))
            end = start.__class__(0,0,ew,eh); end.moveCenter(start.center())
            eff = panel.graphicsEffect() if isinstance(panel.graphicsEffect(), QGraphicsOpacityEffect) else QGraphicsOpacityEffect(panel); panel.setGraphicsEffect(eff); eff.setOpacity(1.0)
            opacity = QPropertyAnimation(eff,b'opacity',panel); opacity.setDuration(170); opacity.setStartValue(1.0); opacity.setEndValue(0.0); opacity.setEasingCurve(QEasingCurve.Type.InCubic)
            geom = QPropertyAnimation(panel,b'geometry',panel); geom.setDuration(170); geom.setStartValue(start); geom.setEndValue(end); geom.setEasingCurve(QEasingCurve.Type.InCubic)
            opacity.finished.connect(panel.hide); opacity.start(); geom.start(); self._quick_anim=(opacity,geom)

    def _build_quick_popup(self):
        w = QFrame(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        w.setObjectName('QuickPopup')
        w.setProperty('_jarvis_floating_panel', True)
        w.setProperty('_jarvis_panel_kind', 'command_deck')
        w.setProperty('_jarvis_panel_key', 'command_deck')
        w.setWindowTitle('JARVIS Quick Actions')
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(f"""
            QFrame#QuickPopup {{ background: transparent; border: none; }}
            QFrame#DashboardHeader {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(0,102,150,72), stop:0.48 rgba(0,30,48,72), stop:1 rgba(0,78,120,58));
                border: 1px solid rgba(0,212,255,55); border-radius: 8px; }}
            QLabel#DashboardTitle {{ color: {C.WHITE}; font: 800 8.5pt \"Orbitron\"; background: transparent; padding: 2px 4px; letter-spacing: 1.4px; }}
            QLabel#DashboardSubtitle {{ color: {C.TEXT_DIM}; font: 700 6pt \"Exo 2\"; background: transparent; padding-left: 4px; }}
            QFrame#DashboardPanel {{ background: rgba(0,18,30,62); border: 1px solid rgba(0,164,220,46); border-radius: 7px; }}
            QLabel#DashboardEyebrow {{ color: {C.TEXT_DIM}; font: 700 6pt \"Exo 2\"; letter-spacing: .8px; background: transparent; }}
            QLabel#DashboardMetric {{ color: {C.PRI}; font: 700 8pt \"Orbitron\"; background: transparent; }}
            QPushButton#DashboardNav {{ color: {C.TEXT_MED}; background: rgba(0,20,31,70); border: 1px solid rgba(26,92,122,80); border-radius: 5px; padding: 3px 6px; font: 700 6.5pt \"Exo 2\"; }}
            QPushButton#DashboardNav:hover, QPushButton#DashboardNav:checked {{ color: {C.WHITE}; border-color: rgba(0,212,255,140); background: rgba(0,132,177,58); }}
            QLineEdit#QuickInput {{ background: rgba(0,15,24,74); color: {C.WHITE}; border: 1px solid rgba(0,164,220,72); border-radius: 6px; padding: 4px 8px; }}
            QLineEdit#QuickInput:focus {{ border-color: rgba(0,212,255,145); background: rgba(0,27,42,110); }}
            QTextEdit#QuickChat {{ background: rgba(0,10,18,48); color: {C.TEXT}; border: none; padding: 3px 2px; }}
            QScrollBar:vertical {{ background: transparent; width: 5px; }}
            QScrollBar::handle:vertical {{ background: rgba(55,166,199,85); border-radius: 2px; min-height: 18px; }}
        """)
        backdrop = FuturisticBackdrop(w)
        backdrop.lower()
        w._futuristic_backdrop = backdrop
        w.resize(376, 398)
        backdrop.setGeometry(w.rect())
        lay = QVBoxLayout(w); lay.setContentsMargins(7,7,7,7); lay.setSpacing(5)

        header_frame = QFrame(); header_frame.setObjectName('DashboardHeader')
        header_frame.setProperty('_jarvis_popup_drag', True); header_frame.installEventFilter(self)
        header = QHBoxLayout(header_frame); header.setContentsMargins(5,3,5,3); header.setSpacing(5)

        avatar = QLabel(); avatar.setFixedSize(34,34); avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f'color:{C.PRI}; background:rgba(0,31,45,80); border:1px solid rgba(0,212,255,80); border-radius:17px; font:800 10pt \"Orbitron\";')
        try:
            face = QPixmap(str(self._face_path))
            if not face.isNull():
                side = min(face.width(), face.height())
                src = face.copy((face.width()-side)//2, (face.height()-side)//2, side, side)
                out = QPixmap(30,30); out.fill(Qt.GlobalColor.transparent)
                qp = QPainter(out); qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                clip = QPainterPath(); clip.addEllipse(QRectF(0,0,30,30)); qp.setClipPath(clip)
                qp.drawPixmap(QRectF(0,0,30,30), src)
                qp.end(); avatar.setPixmap(out)
            else:
                avatar.setText((self._assistant_name[:1] or 'J').upper())
        except Exception:
            avatar.setText((self._assistant_name[:1] or 'J').upper())
        header.addWidget(avatar)

        title_box = QVBoxLayout(); title_box.setSpacing(0)
        hdr = QLabel('J.A.R.V.I.S  //  COMMAND DECK'); hdr.setObjectName('DashboardTitle')
        hdr.setProperty('_jarvis_popup_drag', True); hdr.installEventFilter(self)
        title_box.addWidget(hdr)
        subtitle = QLabel('TACTICAL INTERFACE  //  SYSTEM LINK ACTIVE'); subtitle.setObjectName('DashboardSubtitle')
        subtitle.setProperty('_jarvis_popup_drag', True); subtitle.installEventFilter(self)
        title_box.addWidget(subtitle); header.addLayout(title_box, 1)
        self._dashboard_link = QLabel('● ONLINE'); self._dashboard_link.setStyleSheet(f'color:{C.GREEN};font:700 6.5pt \"Exo 2\";background:transparent;'); header.addWidget(self._dashboard_link)
        close = _GlowSquareButton('×'); close.setToolTip('Close command deck and all JARVIS windows'); close.clicked.connect(self._close_all_ui); header.addWidget(close)
        lay.addWidget(header_frame)

        telemetry = QFrame(); telemetry.setObjectName('DashboardPanel')
        tl = QHBoxLayout(telemetry); tl.setContentsMargins(7,4,7,4); tl.setSpacing(10)
        for label, attr in (('CPU', 'cpu'), ('RAM', 'mem'), ('GPU', 'gpu')):
            col = QVBoxLayout(); col.setSpacing(0)
            eyebrow = QLabel(label); eyebrow.setObjectName('DashboardEyebrow'); col.addWidget(eyebrow)
            value = QLabel('--%'); value.setObjectName('DashboardMetric'); setattr(self, '_dashboard_' + attr, value); col.addWidget(value); tl.addLayout(col)
        tl.addStretch(1)
        state = QLabel('READY'); state.setObjectName('DashboardEyebrow'); state.setStyleSheet(f'color:{C.GREEN};font:700 6pt \"Exo 2\";background:transparent;'); tl.addWidget(state)
        lay.addWidget(telemetry)

        nav = QHBoxLayout(); nav.setSpacing(3)
        self._dashboard_pages = QStackedWidget(); self._dashboard_pages.setStyleSheet('background:transparent;')
        for i, label in enumerate(('CORE', 'MEDIA', 'WEB', 'PC', 'SYSTEM')):
            b = QPushButton(label); b.setObjectName('DashboardNav'); b.setCheckable(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, page=i: self._switch_dashboard_page(page)); nav.addWidget(b)
            if i == 0: self._dashboard_nav_first = b
        lay.addLayout(nav); self._dashboard_nav = nav

        pages = [
            [('CHAT LINK', 'Send commands through the secure voice/text bridge.', self._focus_quick_input),
             ('MEMORY CORE', 'Inspect, add, and forget stored facts.', self._open_memory_panel),
             ('BRAIN GRAPH', 'Open the live 3D neural memory graph.', self._open_brain_panel)],
            [('VIDEO FEED', 'Open live media preview and camera telemetry.', self._open_video_panel),
             ('IMAGE ANALYSIS', 'Inspect generated or uploaded imagery.', self._open_image_panel),
             ('3D HOLOGRAM', 'Launch the interactive model display surface.', self._open_3d_display)],
            [('WEBVIEW', 'Open a dedicated in-HUD browser window.', self._open_webview_panel),
             ('WEB TASK', 'Queue search, news, research, and browse jobs.', self._open_web_task_panel),
             ('WORLD MONITOR', 'Global clocks, telemetry, and world headlines.', self._open_world_monitor)],
            [('SCREEN CAPTURE', 'Capture and understand the current screen.', lambda: self._send_backend_command('Capture and understand my screen.')),
             ('DESKTOP CONTROL', 'Control applications, windows, mouse, keyboard, files, and terminal.', lambda: self._send_backend_command('Help me control my computer desktop.')),
             ('NEW WINDOW', 'Spawn another editable HUD workspace window.', self._spawn_workspace_window)],
            [('CONTROL CENTER', 'Configure identity, audio, theme, and runtime.', self._open_full_settings),
             ('LAYOUT EDITOR', 'Move, resize, and create HUD elements.', self._open_layout_editor),
             ('ACTIVITY LOG', 'Open the live system activity window.', self._open_activity_panel)],
        ]
        for entries in pages:
            page = QFrame(); page.setObjectName('DashboardPanel')
            pl = QVBoxLayout(page); pl.setContentsMargins(6,6,6,6); pl.setSpacing(4)
            for title, detail, callback in entries:
                btn = _GlowButton(title, '▸', compact=True); btn.setMinimumHeight(27); btn.setToolTip(detail); btn.clicked.connect(callback); pl.addWidget(btn)
            pl.addStretch(1); self._dashboard_pages.addWidget(page)
        lay.addWidget(self._dashboard_pages, 1)

        chat_header = QHBoxLayout(); chat_header.setSpacing(5)
        chat_title = QLabel('CHAT LOG'); chat_title.setStyleSheet(f'color:{C.PRI};font:800 6.5pt \"Orbitron\";letter-spacing:1.2px;background:transparent;'); chat_header.addWidget(chat_title)
        chat_header.addStretch(1)
        self._chat_meta = QLabel('LIVE'); self._chat_meta.setStyleSheet(f'color:{C.TEXT_DIM};font:700 6pt \"Exo 2\";background:transparent;'); chat_header.addWidget(self._chat_meta)
        lay.addLayout(chat_header)

        self._chat_log = MarkdownTextBrowser(); self._chat_log.setObjectName('QuickChat'); self._chat_log.setReadOnly(True)
        self._chat_log.setMinimumHeight(64); self._chat_log.setMaximumHeight(86)
        self._chat_log.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded); self._chat_log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chat_log.setPlaceholderText('Awaiting secure messages…'); lay.addWidget(self._chat_log)
        for entry in getattr(self, '_chat_history', []):
            self._append_quick_chat_html(entry, store=False)

        row = QHBoxLayout(); row.setSpacing(4)
        self._mic_btn = _IconOnlyButton('mic', w); self._mic_btn.setToolTip('Mute / unmute microphone (F4)'); self._mic_btn.clicked.connect(self._toggle_mute); row.addWidget(self._mic_btn)
        self._quick_input = QLineEdit(); self._quick_input.setObjectName('QuickInput'); self._quick_input.setPlaceholderText('Type a command…'); self._quick_input.setFixedHeight(28); self._quick_input.returnPressed.connect(self._send_quick_input); row.addWidget(self._quick_input,1)
        send = _IconOnlyButton('send', w); send.setToolTip('Send command'); send.clicked.connect(self._send_quick_input); row.addWidget(send); lay.addLayout(row)

        self._custom_quick_buttons_layout = QGridLayout(); self._custom_quick_buttons_layout.setSpacing(4); lay.addLayout(self._custom_quick_buttons_layout); self._rebuild_custom_quick_buttons()

        self._quick_status = QLabel(); self._quick_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._quick_status.setStyleSheet(f'color:{C.TEXT_DIM};font:700 6.5pt \"Archivo Black\";background:transparent;border:none;padding:2px 0;')
        lay.addWidget(self._quick_status)
        self._quick_clock_timer = QTimer(w); self._quick_clock_timer.timeout.connect(self._update_quick_clock); self._quick_clock_timer.start(1000); self._update_quick_clock()

        self._dashboard_nav_first.setChecked(True)
        self._dashboard_pages.setCurrentIndex(0)
        return w

    def _update_quick_clock(self):
        try:
            self._quick_status.setText(datetime.now().strftime('%d %b %Y  •  %H:%M:%S'))
        except Exception:
            pass

    def _append_quick_chat_html(self, text, store=True):
        import re, html
        raw = str(text or '')
        plain = re.sub(r'<[^>]+>', '', raw).strip()
        speaker, body = 'J.A.R.V.I.S', plain
        low = plain.lower()
        for prefix, name in (('you:', 'YOU'), ('jarvis:', 'J.A.R.V.I.S'), ('sys:', 'SYS'), ('vis:', 'VIS'), ('file:', 'FILE'), ('err:', 'ERROR'), ('warn:', 'WARN')):
            if low.startswith(prefix):
                speaker, body = name, plain[len(prefix):].strip(); break
        now = datetime.now().strftime('%H:%M:%S')
        initial = (speaker[:1] or 'J').upper()
        color = C.WHITE if speaker == 'YOU' else (C.PRI if speaker == 'J.A.R.V.I.S' else C.TEXT_MED)
        safe_speaker = html.escape(speaker)
        body_html = self._chat_log.markdown_to_html(body)
        block = (
            f'<table width="100%" cellspacing="0" cellpadding="0">'
            f'<tr><td width="24" valign="top"><div style="color:{C.PRI};background:rgba(0,72,96,70);border:1px solid rgba(0,212,255,70);border-radius:12px;width:20px;height:20px;text-align:center;">{initial}</div></td>'
            f'<td valign="top"><span style="color:{color};font-weight:700;">{safe_speaker}</span>'
            f' <span style="color:{C.TEXT_DIM};font-size:small;">{now}</span><br/>'
            f'{body_html}</td></tr></table>'
            f'<div style="border-bottom:1px solid rgba(63,153,184,55); height:4px;"></div>'
        )
        self._chat_log.insertHtml(block)
        self._chat_log.moveCursor(QTextCursor.MoveOperation.End)
        self._chat_log.ensureCursorVisible()

    def _resize_quick_backdrop(self):
        if self._quick_popup is not None:
            backdrop = getattr(self._quick_popup, '_futuristic_backdrop', None)
            if backdrop is not None:
                backdrop.setGeometry(self._quick_popup.rect())

    def _switch_dashboard_page(self, index):
        if not hasattr(self, '_dashboard_pages'):
            return
        self._dashboard_pages.setCurrentIndex(index)
        for i in range(self._dashboard_nav.count()):
            item = self._dashboard_nav.itemAt(i)
            if item and item.widget():
                item.widget().setChecked(i == index)

    def _focus_quick_input(self):
        if hasattr(self, '_quick_input'):
            self._quick_input.setFocus()

    def _close_all_ui(self):
        windows = (
            '_quick_popup', '_command_window', '_full_settings_window', '_three_d_display',
            '_video_panel', '_image_panel', '_content_panel', '_activity_panel',
            '_model_panel', '_webview_panel', '_web_task_panel', '_world_monitor_panel',
            '_memory_panel', '_tools_panel', '_cam_preview', '_cam_live_lbl', '_overlay',
            '_confirm_overlay', '_customize_overlay', '_audio_overlay',
            '_memory_overlay', '_plugin_manager_overlay',
        )
        for name in windows:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.close()
        for win in getattr(self, '_workspace_windows', []):
            try:
                win.close()
            except Exception:
                pass
        self.close()
        QApplication.quit()

    def _on_gui_thread(self) -> bool:
        try:
            app = QApplication.instance()
            return app is not None and QThread.currentThread() == app.thread()
        except Exception:
            return False

    def _run_gui_task(self, fn) -> None:
        try:
            fn()
        except Exception as exc:
            try:
                self.write_log(f'ERR: UI task — {exc}')
            except Exception:
                pass

    def run_on_ui(self, fn) -> None:
        try:
            if self._on_gui_thread():
                self._run_gui_task(fn)
                return
            self._gui_exec_sig.emit(fn)
        except Exception:
            try:
                self._run_gui_task(fn)
            except Exception:
                pass

    def call_on_ui(self, fn, timeout: float = 15.0):
        if self._on_gui_thread():
            return fn()
        box: dict = {}
        done = threading.Event()

        def _run():
            try:
                box['value'] = fn()
            except Exception as exc:
                box['error'] = exc
            finally:
                done.set()

        try:
            self._gui_exec_sig.emit(_run)
        except Exception as exc:
            raise RuntimeError(f'UI call failed: {exc}')
        if not done.wait(timeout):
            raise RuntimeError('UI call timed out — the interface may be busy.')
        if 'error' in box:
            raise box['error']
        return box.get('value')

    def show_video(self, url):
        self.run_on_ui(lambda: self._open_webview_panel(url))

    def _rebuild_custom_quick_buttons(self):
        layout = getattr(self, '_custom_quick_buttons_layout', None)
        if layout is None: return
        while layout.count():
            item=layout.takeAt(0)
            if item and item.widget(): item.widget().deleteLater()
        for i,action in enumerate(self._custom_quick_actions):
            label=str(action.get('label','ACTION')).strip() or 'ACTION'; cmd=str(action.get('command','')).strip(); b=_GlowButton(label,'✦',compact=True); b.setMinimumHeight(36); b.clicked.connect(lambda _=False, command=cmd: self._send_backend_command(command)); layout.addWidget(b,i//2,i%2)
        add=_GlowButton('ADD QUICK BUTTON','＋',compact=True); add.clicked.connect(self._add_quick_action)
        layout.addWidget(add, (len(self._custom_quick_actions))//2 + 1, 0, 1, 2)

    def _send_quick_input(self):
        if not hasattr(self,'_quick_input'): return
        txt=self._quick_input.text().strip()
        if not txt: return
        self._quick_input.clear()
        self._append_chat(f'YOU: {txt}')
        self._send_backend_command(txt, log=False)
        self._quick_input.setFocus()

    def _add_quick_action(self):
        label,ok=QInputDialog.getText(self,'Add Quick Action','Button name:')
        if not ok or not label.strip(): return
        command,ok=QInputDialog.getText(self,'Add Quick Action','Command to send to JARVIS:')
        if not ok or not command.strip(): return
        self._custom_quick_actions.append({'label':label.strip(),'command':command.strip()})
        cfg=_read_full_config(); cfg['quick_actions']=self._custom_quick_actions; API_FILE.parent.mkdir(parents=True,exist_ok=True); API_FILE.write_text(json.dumps(cfg,indent=4),encoding='utf-8')
        self._rebuild_custom_quick_buttons()
        if self._quick_popup: self._quick_popup.adjustSize()

    def _expand(self, animated=True):
        self._collapsed = True
        if self._defer_layout_until_settled():
            return
        self.setMinimumSize(QSize(self._compact_size, self._compact_size))
        self.setMaximumSize(QSize(self._compact_size, self._compact_size))
        self.resize(self._compact_size, self._compact_size)
        self._topbar.hide()
        self._main_row.hide()
        self._statusbar.hide()
        self._apply_compact_style()
        self._header_logo.setParent(self.surface)
        self._header_logo.set_logo_size(self._settled_logo_size())
        self._header_logo.setGeometry(max(0, (self._compact_size-self._header_logo.width())//2),
                                      max(0, (self._compact_size-self._header_logo.height())//2),
                                      self._header_logo.width(), self._header_logo.height())
        self._header_logo._startup_bg_fill_active = False
        self._header_logo.update()
        self._header_logo.raise_()

    def _collapse(self, animated=True):
        self._collapsed = True
        for panel in (self.quick_panel, self._video_panel, self._activity_panel, self._image_panel, self._content_panel,
                      getattr(self, '_webview_panel', None), getattr(self, '_web_task_panel', None),
                      getattr(self, '_world_monitor_panel', None), getattr(self, '_memory_panel', None)):
            if panel is not None:
                panel.hide()
        self._topbar.hide(); self._main_row.hide(); self._statusbar.hide(); self._apply_compact_style()
        if self._defer_layout_until_settled():
            if hasattr(self, '_root_layout'): self._root_layout.setContentsMargins(0, 0, 0, 0)
            return
        self.setMinimumSize(QSize(self._compact_size, self._compact_size)); self.setMaximumSize(QSize(self._compact_size, self._compact_size)); self.resize(self._compact_size, self._compact_size)
        if hasattr(self, '_root_layout'): self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._header_logo.setParent(self.surface); self._header_logo.set_logo_size(self._settled_logo_size()); self._header_logo.setGeometry(max(0,(self._compact_size-self._header_logo.width())//2),max(0,(self._compact_size-self._header_logo.height())//2),self._header_logo.width(),self._header_logo.height()); self._header_logo._startup_bg_fill_active = False; self._header_logo.update(); self._header_logo.raise_()

    def _startup_begin(self):
        """STARTUP: big presentation on screen, waiting for the settle trigger."""
        self._prepare_startup_presentation()

    def _startup_animate(self, duration_ms=None):
        """STARTUP → ANIMATING: start the single geometry timeline."""
        self._begin_startup_position_animation(duration_ms)

    def _startup_finish(self):
        """ANIMATING → SETTLED: pin the final geometry exactly once."""
        finalize = getattr(self, '_finalize_startup_transition', None)
        self._finalize_startup_transition = None
        if finalize is not None:
            finalize()

    def _startup_click_settle(self) -> None:
        """Click on the startup reactor (logo or hitbox): settle smoothly.

        ANIMATING — fast-forward the timeline clock to its end so the normal
        finished→finalize path lands the exact final geometry (no snap, no
        duplicate finalize). STARTUP — short 200 ms eased settle from the
        current frame. SETTLED — nothing to do.
        """
        try:
            phase = getattr(self, '_startup_phase', None)
            if phase is _StartupPhase.ANIMATING:
                slide = getattr(self, '_startup_timeline', None)
                if slide is not None:
                    try:
                        slide.setCurrentTime(slide.duration())
                    except Exception:
                        slide.stop()
                return
            if phase is _StartupPhase.STARTUP:
                self._startup_animate(duration_ms=200)
        except Exception:
            pass

    def _prepare_startup_presentation(self):
        try:
            # The states are the single authority: a running settle is never
            # interrupted by a re-present (e.g. a late setup-completion path),
            # and a settled sequence is never re-presented within one process.
            phase = getattr(self, '_startup_phase', None)
            if phase is _StartupPhase.ANIMATING:
                return
            if phase is _StartupPhase.SETTLED and getattr(self, '_startup_presented_once', False):
                return
            self._startup_presented_once = True
            # A previous presentation's fallback timer must never survive.
            self._stop_startup_fallback()
            # STARTUP owns the geometry from here on.
            self._startup_phase = _StartupPhase.STARTUP
            screen = QApplication.primaryScreen()
            sg = screen.availableGeometry() if screen is not None else None
            small = max(170, int(getattr(self, '_compact_size', 170)))
            # The startup logo is deliberately larger than the final compact logo.
            # Give the startup window enough room for the complete ring/dot geometry
            # so the outer white dots are never clipped by the window edges.
            big = max(250, small + 100)
            self._startup_big_size = big
            self._startup_small_size = small

            if sg is not None:
                cx = sg.left() + (sg.width() - big) // 2
                cy = sg.top() + (sg.height() - big) // 2
                self.setMinimumSize(QSize(big, big))
                self.setMaximumSize(QSize(big, big))
                self.setGeometry(cx, cy, big, big)
            else:
                self.setMinimumSize(QSize(big, big))
                self.setMaximumSize(QSize(big, big))
                self.resize(big, big)

            logo = getattr(self, '_header_logo', None)
            if logo is not None:
                # The configured LOGO_BG_FILL is active only during this startup
                # presentation. It is disabled automatically after settling.
                logo._startup_bg_fill_active = True
                logo._startup_core_fade = 0.0
                logo.update()

                # Fade the transparent black core in softly. The radial gradient
                # still fades to zero at the edge, so it never becomes a hard disc.
                try:
                    core_fade = QVariantAnimation(self)
                    core_fade.setDuration(STARTUP_CORE_FADE_IN_MS)
                    core_fade.setStartValue(0.0)
                    core_fade.setEndValue(1.0)
                    core_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
                    core_fade.valueChanged.connect(lambda v, lg=logo: (setattr(lg, '_startup_core_fade', float(v)), lg.update()))
                    core_fade.start()
                    self._startup_core_fade_anim = core_fade
                except Exception:
                    logo._startup_core_fade = 1.0

                # Subtle startup scale: begin at the configured startup diameter
                # and keep the entire visual motion smooth. The transparent hit
                # area follows the glow and logo before JARVIS speaks.
                startup_logo = min(big - 24, max(self._logo_settled_size() + 8, STARTUP_LOGO_SIZE))
                logo.set_logo_size(startup_logo)
                self._center_logo()

                target_op = int(getattr(self, '_reactor_opacity', 100))
                logo.set_graphic_opacity(0)
                fade = QVariantAnimation(self)
                fade.setDuration(620)
                fade.setStartValue(0)
                fade.setEndValue(target_op)
                fade.setEasingCurve(QEasingCurve.Type.OutCubic)
                fade.valueChanged.connect(lambda v, lg=logo: lg.set_graphic_opacity(int(v)))
                fade.start()
                self._startup_fade = fade
                self._startup_anims = (fade,)

                # Startup bloom — bright cyan halo that ramps 1.0 → 0.0
                # over ~1.6 s as the puck settles into its compact home.
                try:
                    logo._startup_glow = 1.0
                    bloom = QVariantAnimation(self)
                    bloom.setDuration(STARTUP_GLOW_FADE_MS)
                    bloom.setStartValue(1.0)
                    bloom.setEndValue(0.0)
                    bloom.setEasingCurve(QEasingCurve.Type.OutCubic)

                    def _on_bloom(v, lg=logo):
                        lg._startup_glow = float(v)
                        lg.update()

                    bloom.valueChanged.connect(_on_bloom)
                    bloom.start()
                    self._startup_bloom = bloom
                    self._startup_anims = (fade, bloom)
                except Exception:
                    pass

            # The transparent hit surface stays active while the startup logo
            # is waiting for the first JARVIS voice / wake-word transition.
            self._show_startup_logo_hitbox()

            # Safety fallback: even when TTS never emits SPEAKING, the startup
            # puck still settles instead of remaining large forever.
            try:
                self._startup_fallback_timer = QTimer(self)
                self._startup_fallback_timer.setSingleShot(True)
                self._startup_fallback_timer.timeout.connect(self._startup_animate)
                self._startup_fallback_timer.start(6000)
            except Exception:
                try:
                    QTimer.singleShot(6000, self._startup_animate)
                except Exception:
                    pass
        except Exception:
            self._startup_phase = _StartupPhase.STARTUP
            self._show_startup_logo_hitbox()
            try:
                self._startup_fallback_timer = QTimer(self)
                self._startup_fallback_timer.setSingleShot(True)
                self._startup_fallback_timer.timeout.connect(self._startup_animate)
                self._startup_fallback_timer.start(6000)
            except Exception:
                QTimer.singleShot(6000, self._startup_animate)

    def _begin_startup_position_animation(self, duration_ms=None):
        """Smoothly shrink the startup reactor and glide it to its compact home.

        This follows the proven startup animation pattern from the older JARVIS
        build: Qt animates the window's QRect directly, and the logo size is
        derived from that same animated QRect. There is only one animation
        clock, so position, window size and logo size stay locked together.
        """
        if getattr(self, '_startup_phase', None) is not _StartupPhase.STARTUP:
            return

        self._startup_phase = _StartupPhase.ANIMATING
        self._stop_startup_fallback()

        # The invisible startup hitbox is not needed while the reactor itself
        # is shrinking. Stop its timer so it cannot compete with the animation.
        try:
            timer = getattr(self, '_startup_logo_hitbox_timer', None)
            if timer is not None:
                timer.stop()
            hit = getattr(self, '_startup_logo_hitbox', None)
            if hit is not None:
                hit.hide()
        except Exception:
            pass

        logo = getattr(self, '_header_logo', None)

        try:
            small = max(170, int(getattr(self, '_compact_size', 170)))
            target_pos = self._clamp_position_to_screen(
                getattr(self, '_startup_target_pos', self.pos()), small
            )

            # Use the real visible startup frame as the animation start.
            # Never reconstruct/replace it here, which is what causes a snap.
            start = QRect(
                int(self.x()), int(self.y()),
                max(1, int(self.width())), max(1, int(self.height()))
            )
            target = QRect(
                int(target_pos.x()), int(target_pos.y()),
                int(small), int(small)
            )

            if logo is not None:
                # Keep the startup artwork exactly as-is, only changing its
                # diameter from the configured 190 px to the 180 px settle
                # size (clamped to fit the compact window).
                start_logo = int(min(
                    max(120, start.width() - 24),
                    max(120, start.height() - 24),
                    STARTUP_LOGO_SIZE,
                ))
                start_logo = max(120, start_logo)
                end_logo = self._settled_logo_size()
                self._startup_end_logo = end_logo
            else:
                start_logo = 0
                end_logo = 0
                self._startup_end_logo = 0

            # Release the big startup lock so Qt can actually interpolate the
            # top-level window geometry.
            self.setMinimumSize(QSize(120, 120))
            self.setMaximumSize(QSize(16777215, 16777215))

            # Pin the exact current frame before starting the property animation.
            self.setGeometry(start)

            if logo is not None:
                logo.set_logo_size(start_logo)
                logo._startup_bg_fill_active = True
                logo.setGeometry(
                    max(0, (start.width() - logo.width()) // 2),
                    max(0, (start.height() - logo.height()) // 2),
                    logo.width(), logo.height(),
                )
                logo.update()

            finalized = {'done': False}

            def _finalize():
                if finalized['done']:
                    return
                finalized['done'] = True
                try:
                    # One exact finalization after the animated motion has ended.
                    self.setMinimumSize(QSize(small, small))
                    self.setMaximumSize(QSize(small, small))
                    self.setGeometry(target)

                    if logo is not None:
                        logo.set_logo_size(end_logo)
                        logo.setGeometry(
                            max(0, (small - logo.width()) // 2),
                            max(0, (small - logo.height()) // 2),
                            logo.width(), logo.height(),
                        )
                        logo._startup_bg_fill_active = True
                        logo.raise_()
                        logo.update()

                    self._hide_startup_logo_hitbox()
                    self._save_window_position()
                except Exception:
                    pass
                finally:
                    # No startup animation or timer survives SETTLED (repeated
                    # launches must be identical). Stopping a mid-fade bloom
                    # would freeze its halo, so the glow is zeroed with it.
                    for a in getattr(self, '_startup_anims', ()) or ():
                        try:
                            a.stop()
                        except Exception:
                            pass
                    self._startup_anims = ()
                    lg = getattr(self, '_header_logo', None)
                    if lg is not None and getattr(lg, '_startup_glow', 0.0):
                        lg._startup_glow = 0.0
                        lg.update()
                    self._startup_timeline = None
                    self._finalize_startup_transition = None
                    # SETTLED is reached exactly once, through this one path.
                    self._startup_phase = _StartupPhase.SETTLED
                    self._apply_pending_startup_layout()

            self._finalize_startup_transition = _finalize

            # This is the key change from the newer implementation:
            # let Qt animate the QWidget geometry property itself instead of
            # manually calling setGeometry() every frame.
            slide = QPropertyAnimation(self, b'geometry', self)
            slide.setDuration(int(duration_ms) if duration_ms else 1250)
            slide.setStartValue(start)
            slide.setEndValue(target)
            slide.setEasingCurve(QEasingCurve.Type.InOutCubic)

            def _sync_logo(value):
                try:
                    rect = value
                    if not isinstance(rect, QRect):
                        rect = rect.toRect()

                    # The logo follows the SAME animated window rectangle.
                    # 0.0 = startup size, 1.0 = compact size.
                    width_span = float(max(1, start.width() - target.width()))
                    height_span = float(max(1, start.height() - target.height()))
                    px = (start.width() - rect.width()) / width_span
                    py = (start.height() - rect.height()) / height_span
                    progress = max(0.0, min(1.0, (px + py) * 0.5))

                    if logo is not None:
                        logo_size = int(round(
                            start_logo + (end_logo - start_logo) * progress
                        ))
                        logo_size = max(120, min(start_logo, logo_size))
                        logo.set_logo_size(logo_size)

                        # Cheap centering only. _center_logo() is deliberately
                        # not called here because it also maintains the hitbox.
                        logo.move(
                            max(0, (rect.width() - logo.width()) // 2),
                            max(0, (rect.height() - logo.height()) // 2),
                        )
                except Exception:
                    pass

            slide.valueChanged.connect(_sync_logo)
            slide.finished.connect(_finalize)

            self._startup_slide = slide
            self._startup_timeline = slide
            self._startup_anims = tuple(
                a for a in (
                    getattr(self, '_startup_fade', None),
                    getattr(self, '_startup_bloom', None),
                    getattr(self, '_startup_core_fade_anim', None),
                    slide,
                ) if a is not None
            )

            slide.start()

        except Exception:
            # Any construction failure still settles deterministically: pin the
            # final geometry synchronously and land in SETTLED.
            self._startup_finish_fallback()

    def _startup_finish_fallback(self):
        """Synchronous settle used when the timeline cannot be constructed."""
        self._startup_phase = _StartupPhase.SETTLED
        self._stop_startup_fallback()
        try:
            small = max(170, int(getattr(self, '_compact_size', 170)))
            target = self._clamp_position_to_screen(
                getattr(self, '_startup_target_pos', self.pos()), small
            )
            self.setMinimumSize(QSize(small, small))
            self.setMaximumSize(QSize(small, small))
            self.setGeometry(target.x(), target.y(), small, small)
            logo = getattr(self, '_header_logo', None)
            if logo is not None:
                logo.set_logo_size(self._settled_logo_size())
                logo._startup_bg_fill_active = True
                logo.setGeometry(
                    max(0, (small - logo.width()) // 2),
                    max(0, (small - logo.height()) // 2),
                    logo.width(), logo.height(),
                )
                logo.update()
            self._hide_startup_logo_hitbox()
            self._save_window_position()
        except Exception:
            pass
        finally:
            for a in getattr(self, '_startup_anims', ()) or ():
                try:
                    a.stop()
                except Exception:
                    pass
            self._startup_anims = ()
            lg = getattr(self, '_header_logo', None)
            if lg is not None and getattr(lg, '_startup_glow', 0.0):
                lg._startup_glow = 0.0
                lg.update()
            self._startup_timeline = None
            self._finalize_startup_transition = None
            self._apply_pending_startup_layout()

    def _animate_window_show(self):
        self._startup_begin()

    def _multi_window_enabled(self) -> bool:
        """Settings → SYSTEM → "Allow multiple independent HUD windows"."""
        try:
            return bool(self._features.get('floating_windows', True))
        except Exception:
            return True

    def _open_panels(self) -> list:
        out = []
        for name in ('_video_panel', '_model_panel', '_image_panel', '_content_panel',
                     '_activity_panel', '_webview_panel', '_web_task_panel',
                     '_world_monitor_panel', '_memory_panel', '_tools_panel',
                     '_quick_panel', '_window_panel'):
            p = getattr(self, name, None)
            if p is not None:
                out.append(p)
        return out

    def _set_panel_visible(self, panel, visible=True):
        if panel is None:
            return
        if visible and not self._multi_window_enabled():
            # "Allow multiple independent HUD windows" is off: one HUD window at
            # a time, so opening a panel closes whichever was already open
            # instead of the desktop filling up with them.
            for other in self._open_panels():
                if other is panel or not other.isVisible():
                    continue
                try:
                    self._fade_panel(other, False)
                except Exception:
                    pass
        if visible:
            if panel.property('_jarvis_panel_key') is None:
                key = str(panel.property('_jarvis_panel_kind') or panel.objectName() or 'panel')
                panel.setProperty('_jarvis_panel_key', key)
            if not self._restore_panel_position(panel):
                self._position_one_panel(panel)
            panel.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            panel.show()
            panel.raise_()
            panel.activateWindow()
            self._fade_panel(panel, True)
            if _sfx_enabled(self):
                _sfx('open')
        else:
            self._fade_panel(panel, False)

    def _smooth(self) -> bool:
        try:
            return bool(self._features.get('smooth_animations', True))
        except Exception:
            return True

    def _fade_panel(self, panel, show):
        old=self._panel_animations.pop(panel, None)
        if old:
            try:
                for _a in (old if isinstance(old, (list, tuple)) else (old,)):
                    _a.stop()
            except Exception: pass
        if not self._smooth():
            panel.setVisible(show)
            return
        old_eff = panel.graphicsEffect()
        eff = old_eff if isinstance(old_eff, QGraphicsOpacityEffect) else QGraphicsOpacityEffect(panel)
        panel.setGraphicsEffect(eff)
        anims = []
        anim=QPropertyAnimation(eff,b'opacity',panel); anim.setDuration(220 if show else 170)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic)
        anims.append(anim)
        try:
            slide = QPropertyAnimation(panel, b'pos', panel)
            slide.setDuration(240 if show else 170)
            slide.setEasingCurve(QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic)
            end_pos = panel.pos()
            if show:
                slide.setStartValue(end_pos + QPoint(0, 14))
                slide.setEndValue(end_pos)
            else:
                slide.setStartValue(end_pos)
                slide.setEndValue(end_pos + QPoint(0, 10))
            anims.append(slide)
        except Exception:
            pass
        if show:
            eff.setOpacity(0.0); anim.setStartValue(0.0); anim.setEndValue(1.0)
        else:
            try:
                anim.finished.disconnect()
            except Exception:
                pass
            anim.setStartValue(eff.opacity() if eff.opacity()>0 else 1.0); anim.setEndValue(0.0); anim.finished.connect(panel.hide)
        for _a in anims:
            _a.start()
        self._panel_animations[panel]=anims

    def _position_one_panel(self, panel):
        screen = QApplication.primaryScreen()
        if screen is None or panel is None:
            return
        sg = screen.availableGeometry()
        sizes = {
            getattr(self, '_video_panel', None): (640, 460),
            getattr(self, '_image_panel', None): (560, 500),
            getattr(self, '_content_panel', None): (620, 520),
            getattr(self, '_activity_panel', None): (420, 380),
            getattr(self, '_model_panel', None): (620, 520),
            getattr(self, '_webview_panel', None): (960, 600),
            getattr(self, '_web_task_panel', None): (560, 480),
            getattr(self, '_world_monitor_panel', None): (860, 560),
            getattr(self, '_memory_panel', None): (560, 520),
            getattr(self, '_tools_panel', None): (600, 540),
        }
        pw, ph = sizes.get(panel, (480, 360))
        pw = min(pw, sg.width() - 24); ph = min(ph, sg.height() - 24)
        if not panel.isVisible() or panel.x() < sg.left() or panel.x() > sg.right() or panel.y() < sg.top() or panel.y() > sg.bottom():
            panel.resize(pw, ph)
            offset = (abs(id(panel)) // 7) % 6
            panel.move(sg.left() + (sg.width()-pw)//2 + offset * 26, sg.top() + (sg.height()-ph)//2 + offset * 20)
        panel.raise_()

    def _position_overlays(self):
        for name in ('_video_panel', '_image_panel', '_content_panel', '_activity_panel', '_model_panel',
                     '_webview_panel', '_web_task_panel', '_world_monitor_panel', '_memory_panel',
                     '_tools_panel'):
            panel = getattr(self, name, None)
            if panel is not None and panel.isVisible():
                self._position_one_panel(panel)
        screen = QApplication.primaryScreen()
        if screen is not None:
            sg = screen.availableGeometry()
            if hasattr(self, '_cam_preview') and self._cam_preview.isVisible():
                self._cam_preview.raise_()
            if hasattr(self, '_cam_live_lbl') and self._cam_live_lbl.isVisible():
                self._cam_live_lbl.raise_()

    def _restore_window_position(self, animate: bool = False):
        try:
            cfg = _read_full_config()
            feats = cfg.get('features', {}) if isinstance(cfg.get('features', {}), dict) else {}
            x, y = cfg.get('ui_x'), cfg.get('ui_y')
            screen = QApplication.primaryScreen()
            sg = screen.availableGeometry() if screen is not None else None
            target = None
            small = max(170, int(getattr(self, '_compact_size', 170)))

            if feats.get('remember_position', True) and isinstance(x, int) and isinstance(y, int):
                if sg is None or (sg.left()-small < x < sg.right() and sg.top()-small < y < sg.bottom()):
                    target = self._clamp_position_to_screen(QPoint(x, y), small)
            if target is None:
                if sg is not None:
                    target = self._clamp_position_to_screen(sg.center() - QPoint(small // 2, small // 2), small)
                else:
                    target = self.pos()

            if animate:
                self._startup_target_pos = QPoint(target)
                QTimer.singleShot(0, self._prepare_startup_presentation)
            else:
                self.setMinimumSize(QSize(small, small))
                self.setMaximumSize(QSize(small, small))
                self.resize(small, small)
                self._move_clamped(target, small)
                self._center_logo()
        except Exception:
            if animate:
                self._startup_target_pos = self.pos()
                QTimer.singleShot(0, self._prepare_startup_presentation)
            else:
                self._center_on_screen()
                self._center_logo()

    def _center_on_screen(self):
        screen=QApplication.primaryScreen()
        if screen:
            g=screen.availableGeometry(); self.move(g.center()-self.rect().center())

    def resizeEvent(self,e):
        super().resizeEvent(e); self._position_overlays()
        # While the settle timeline runs it is the sole logo-position writer;
        # recentering here would fight the animation frame by frame.
        if getattr(self, '_startup_phase', None) is _StartupPhase.ANIMATING:
            return
        if self._collapsed:
            self._center_logo()
        else:
            self._sync_startup_logo_hitbox()

    def _append_chat(self, text):
        entry = str(text)
        self._chat_history.append(entry)
        if len(self._chat_history) > 200:
            del self._chat_history[:-200]
        if hasattr(self, '_chat_log'):
            try:
                if hasattr(self, '_append_quick_chat_html'):
                    self._append_quick_chat_html(entry, store=False)
                else:
                    self._chat_log.append(entry)
                self._chat_log.document().adjustSize()
                sb=self._chat_log.verticalScrollBar(); sb.setValue(sb.maximum())
            except Exception:
                pass

    def set_pc_control_active(self, active: bool = True, reason: str = 'PC CONTROL ACTIVE') -> None:
        def _apply():
            try:
                active_b = bool(active)
                self._pc_control_active = active_b
                if active_b:
                    self._pc_control_restore_command = bool(getattr(self, '_command_window', None) and self._command_window.isVisible())
                    self._pc_control_restore_reactor = bool(self.isVisible())
                    if getattr(self, '_command_window', None) is not None:
                        self._command_window.hide()
                    self.hide()
                else:
                    if self._pc_control_restore_reactor:
                        self.show(); self.raise_(); self.activateWindow()
                    if self._pc_control_restore_command:
                        win = getattr(self, '_command_window', None)
                        if win is not None:
                            win.show(); win.raise_()
                    self._pc_control_restore_command = False
                    self._pc_control_restore_reactor = False
                self.write_log(f'SYS: {reason}' if active_b else 'SYS: PC control finished — JARVIS UI restored.')
            except Exception as exc:
                try: self.write_log(f'ERR: PC control UI — {exc}')
                except Exception: pass
        self.run_on_ui(_apply)

    def begin_pc_control(self, reason: str = 'PC CONTROL ACTIVE') -> None:
        self.set_pc_control_active(True, reason)

    def end_pc_control(self) -> None:
        self.set_pc_control_active(False)

    def _send_backend_command(self,text,log=True):
        text=str(text).strip()
        if not text: return
        current=Path(self._current_file) if self._current_file else None
        lower=text.lower()
        if current and current.is_file() and any(k in lower for k in ("play ","show ","open ","preview ")):
            ext=current.suffix.lower()
            if ext in {'.mp4','.avi','.mov','.mkv','.wmv','.webm','.m4v'}:
                self._video_preview_widget.load_file(current); self._open_video_panel()
            elif ext in {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}:
                self.show_image_path(current,'Loaded image'); self._open_image_panel()
            elif ext in {'.obj','.stl','.ply','.gltf','.glb','.off','.dae','.3ds','.fbx'}:
                try:
                    self._model_view.load_model(current); self._model_status.setText(f'Loaded: {current.name}'); self._open_3d_panel()
                except Exception as exc:
                    self._model_status.setText(f'3D preview error: {exc}'); self._open_3d_panel()
        if self.on_text_command:
            if log:
                self._activity_add(f'YOU: {text}')
                self._append_chat(f'<b style="color:{C.WHITE}">YOU</b>  {text}')
            threading.Thread(target=self.on_text_command,args=(text,),daemon=True).start()

    def begin_assistant_stream(self, who: str = 'J.A.R.V.I.S.'):
        self.run_on_ui(lambda: getattr(self, '_command_window', None)
                       and self._command_window.isVisible()
                       and self._command_window.begin_assistant_stream(who))

    def stream_assistant_text(self, chunk: str):
        text = str(chunk or '')
        if not text:
            return
        self.run_on_ui(lambda: self._stream_assistant_text_ui(text))

    def _stream_assistant_text_ui(self, chunk: str):
        win = getattr(self, '_command_window', None)
        if win is None or not win.isVisible():
            return
        if not getattr(win, '_stream_active', False):
            win.begin_assistant_stream()
        win.append_assistant_stream(chunk)

    def finish_assistant_stream(self):
        self.run_on_ui(lambda: getattr(self, '_command_window', None)
                       and self._command_window.isVisible()
                       and self._command_window.finish_assistant_stream())

    def _log_from_backend(self,text):
        self._activity_add(text)
        raw=str(text)
        tl=raw.lower()
        _control_start = ('pc_control_start', 'automation_start', 'multimodal_start', 'computer_control_start', 'desktop_control_start')
        _control_end = ('pc_control_end', 'automation_end', 'multimodal_end', 'computer_control_end', 'desktop_control_end')
        if any(tl.startswith(x) for x in _control_start): self.set_pc_control_active(True, raw)
        elif any(tl.startswith(x) for x in _control_end): self.set_pc_control_active(False)

        if tl == 'jarvis_stream_end':
            self.finish_assistant_stream()
            return
        if tl.startswith('jarvis_stream:'):
            self.stream_assistant_text(raw.split(':', 1)[1].lstrip())
            return

        _own = str(getattr(self, '_assistant_name', 'jarvis')).lower()
        if not (tl.startswith('you:') or tl.startswith('jarvis:') or tl.startswith(_own + ':')
                or tl.startswith('err:') or tl.startswith('sys:') or tl.startswith('vis:')
                or tl.startswith('file:') or tl.startswith('warn:')):
            raw = 'SYS: ' + raw
            tl = raw.lower()
        safe=raw.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        self._append_chat(f'<span style="color:{C.TEXT}">{safe}</span>')
        try:
            win = getattr(self, '_command_window', None)
            if win is not None and win.isVisible():
                who = 'J.A.R.V.I.S.'
                body = raw
                you_flag = False
                _cat = 'jarvis'
                if tl.startswith('you:'): who, body, you_flag, _cat = 'You', raw[4:].strip(), True, 'you'
                elif tl.startswith('sys:'): who, body, _cat = 'SYS', raw.split(':', 1)[-1].strip(), 'sys'
                elif tl.startswith('vis:'): who, body, _cat = 'VIS', raw.split(':', 1)[-1].strip(), 'vis'
                elif tl.startswith('file:'): who, body, _cat = 'FILE', raw.split(':', 1)[-1].strip(), 'file'
                elif tl.startswith('err:'): who, body, _cat = 'ERROR', raw.split(':', 1)[-1].strip(), 'err'
                elif tl.startswith('warn:'): who, body, _cat = 'WARN', raw.split(':', 1)[-1].strip(), 'warn'
                elif ':' in raw[:32] and ('jarvis' in tl[:16] or _own in tl[:16]): body = raw.split(':', 1)[-1].strip()
                # Double-check the toggle at the source — belt and braces
                # so the green button truly hides SYS/VIS even on message
                # bursts that race the flag update.
                try:
                    _hide_now = bool(_read_full_config().get('hide_system_chat', False))
                except Exception:
                    _hide_now = bool(getattr(win, '_hide_system_chat', False))
                if not (_hide_now and _cat not in ('jarvis', 'you')):
                    win.append_msg(who, body, you=you_flag)
        except Exception:
            pass
        if 'error' in tl or tl.startswith('err:'): self._status.setText('VOICE LINK · ATTENTION')
        elif 'listening' in tl or 'online' in tl: self._status.setText('VOICE LINK · ONLINE')

    def _activity_add(self,text):
        try:
            tl = getattr(self, '_activity', None)
            if tl is not None and hasattr(tl, 'add_entry'):
                tl.add_entry(str(text)[:280])
        except Exception:
            pass

    def get_activity_lines(self, n: int = 80) -> list:
        try:
            tl = getattr(self, '_activity', None)
            if tl is not None and hasattr(tl, 'lines'):
                return list(tl.lines(n))
        except Exception:
            pass
        return []

    def _apply_state(self,state):
        state_u = str(state).upper()
        previous = getattr(self.hud, 'state', '')
        if state_u in {'PC_CONTROL', 'AUTOMATION', 'MULTIMODAL', 'COMPUTER_CONTROL', 'DESKTOP_CONTROL'} and not getattr(self, '_pc_control_active', False):
            self.set_pc_control_active(True, f'{state_u} ACTIVE')
        elif state_u in {'PC_CONTROL_END', 'AUTOMATION_END', 'MULTIMODAL_END', 'COMPUTER_CONTROL_END', 'DESKTOP_CONTROL_END'} and getattr(self, '_pc_control_active', False):
            self.set_pc_control_active(False)
        self.hud.state=state; self.hud.speaking=(state_u=='SPEAKING'); self.hud.update(); self._statusbar.setText(f'{state_u}  ·  F4 MUTE  ·  F11 FULLSCREEN  ·  ESC INTERRUPT'); self._status.setText(f'VOICE LINK · {state_u}')
        try:
            if getattr(self, '_header_logo', None) is not None and hasattr(self._header_logo, 'set_state'):
                self._header_logo.set_state(state)
        except Exception:
            pass
        try:
            win = getattr(self, '_command_window', None)
            if win is not None and win.isVisible():
                win.set_status(state)
        except Exception:
            pass

        if getattr(self, '_startup_phase', None) is _StartupPhase.STARTUP:
            if state_u == 'SPEAKING':
                self._startup_voice_seen = True
            elif getattr(self, '_startup_voice_seen', False) and previous == 'SPEAKING' and state_u != 'SPEAKING':
                QTimer.singleShot(80, self._startup_animate)

    def set_state(self,state): self._state_sig.emit(state)
    def write_log(self,text): self._log_sig.emit(str(text))

    def show_content(self,title,text):
        raw_title = str(title or 'CONTENT')
        raw_text = str(text or '')
        self._content_title.setText(raw_title.upper()[:80])
        try:
            self._content_text.set_markdown(raw_text)
        except Exception:
            self._content_text.setPlainText(raw_text)
        self._set_panel_visible(self._content_panel,True)
        urls=self._extract_image_urls(raw_text)
        if urls: self.show_image_url(urls[0],caption=raw_title)

    def _open_content_markdown_file(self):
        try:
            path,_=QFileDialog.getOpenFileName(self,'Open Markdown',str(Path.home()),'Markdown (*.md *.markdown);;All Files (*.*)')
            if not path:
                return
            p=Path(path)
            raw=p.read_text(encoding='utf-8', errors='replace')
            self.show_content(p.stem, raw)
        except Exception as exc:
            self.write_log(f'ERR: Markdown open — {exc}')

    @staticmethod
    def _extract_image_urls(text):
        import re
        urls=[]; urls.extend(re.findall(r'https?://[^\s\)\]>]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s\)\]>]+)?',text,re.I))
        for u in re.findall(r'!\[[^\]]*\]\((https?://[^\)]+)\)',text):
            if u not in urls: urls.append(u)
        return urls

    def show_image_url(self,url,caption='Research image'):
        if not url: return
        self._image_status.setText(f'{caption}  ·  loading image…'); self._set_panel_visible(self._image_panel,True)
        def worker():
            try:
                import requests
                r=requests.get(url,timeout=12,headers={'User-Agent':'Mozilla/5.0'}); r.raise_for_status(); self._image_bytes_sig.emit(r.content,caption)
            except Exception as e: self._image_bytes_sig.emit(b'',f'{caption}  ·  {e}')
        threading.Thread(target=worker,daemon=True).start()

    def show_image_path(self,path,caption='Generated image'):
        try: self._image_bytes_sig.emit(Path(path).read_bytes(),caption)
        except Exception as e: self._image_status.setText(str(e))

    def _show_image_bytes(self,data,caption):
        if not data: self._image_status.setText(str(caption)); return
        px=QPixmap();
        if not px.loadFromData(data): self._image_status.setText('Image could not be decoded.'); return
        self._image_label.setPixmap(px.scaled(self._image_label.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)); self._image_status.setText(str(caption)); self._set_panel_visible(self._image_panel,True)

    @property
    def current_file(self): return self._current_file

    def play_local_video(self, path=None):
        try:
            raw = path or self._current_file
            if not raw:
                self._open_video()
                return 'No file specified — I opened the media picker inside the JARVIS video panel.'
            p = Path(str(raw)).expanduser()
            if not p.exists() or not p.is_file():
                return f'I could not find that file: {p}. Please check the path and try again.'
            media_ext = {'.mp4','.avi','.mov','.mkv','.wmv','.webm','.m4v','.mp3','.wav','.ogg','.m4a','.aac','.flac'}
            if p.suffix.lower() not in media_ext:
                return f'{p.name} is not a playable media type. Supported: MP4, AVI, MOV, MKV, WebM, MP3, WAV, OGG, M4A, AAC, FLAC.'
            self._current_file = str(p)
            self._video_preview_widget.load_file(p)
            self._set_panel_visible(self._video_panel, True)
            self._video_panel.raise_(); self._video_panel.activateWindow()
            kind = 'audio' if p.suffix.lower() in {'.mp3','.wav','.ogg','.m4a','.aac','.flac'} else 'video'
            return (
                f'Opened {p.name} in the JARVIS {kind} panel and started playback. '
                f'If this {kind} needs a codec Windows lacks, use the BROWSER button in the panel.'
            )
        except Exception as exc:
            self.write_log(f'ERR: Local video — {exc}')
            return f'I opened the video panel but playback reported: {exc}. Use the BROWSER button in the panel as fallback.'

    def _open_file(self):
        p,_=QFileDialog.getOpenFileName(self,'Open file',str(Path.home()),'All Files (*.*)')
        if p: self._register_opened_file(p)

    def _open_model(self):
        path,_=QFileDialog.getOpenFileName(self,'Open 3D model',str(Path.home()),'3D Models (*.obj *.stl *.ply *.off *.glb *.gltf *.dae *.3ds);;All Files (*.*)')
        if not path:
            self._open_3d_panel()
            return
        try:
            self._model_view.load_model(path)
            self._model_status.setText(f'Loaded: {Path(path).name}')
            self._open_3d_panel()
        except Exception as exc:
            self._model_status.setText(f'3D preview error: {exc}')
            self._open_3d_panel()

    def _open_video(self):
        p,_=QFileDialog.getOpenFileName(self,'Open video',str(Path.home()),'Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm *.m4v);;Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)')
        if p: self._register_opened_file(p)

    def _register_opened_file(self,p):
        p=Path(p); self._current_file=str(p); ext=p.suffix.lower(); self._activity_add(f'FILE: {p.name}')
        model_ext={'.obj','.stl','.ply','.gltf','.glb','.off','.dae','.fbx','.3ds'}; media_ext={'.mp4','.avi','.mov','.mkv','.wmv','.webm','.m4v','.mp3','.wav','.ogg','.m4a','.aac','.flac'}; image_ext={'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}
        if ext in model_ext:
            try:
                self._model_view.load_model(p); self._model_status.setText(f'Loaded: {p.name}'); self._open_3d_panel()
            except Exception as exc:
                self._model_status.setText(f'3D preview error: {exc}'); self._open_3d_panel()
        elif ext in media_ext:
            self._video_preview_widget.load_file(p); self._set_panel_visible(self._video_panel,True)
        elif ext in image_ext:
            self.show_image_path(p,'Loaded image')
        else:
            self._set_panel_visible(self._activity_panel,True); self._activity_add(f'Loaded: {p}')

    def set_audio_level(self, level):
        """Feed the mic level into every reactive visual.

        Safe to call from ANY thread (the audio callback runs on sounddevice's
        own thread): the value crosses to the GUI thread via a queued signal.
        """
        try:
            lv = max(0.0, min(1.0, float(level)))
        except Exception:
            return
        self._audio_level_sig.emit(lv)

    def _toggle_mute(self):
        if self._muted and not bool(getattr(self, '_features', {}).get('mic_access', True)):
            self.write_log('SYS: Microphone access is revoked in Settings → Permissions.')
            return
        self._muted=not self._muted; self.hud.muted=self._muted; self._apply_state('MUTED' if self._muted else 'LISTENING'); self.write_log('SYS: Microphone muted.' if self._muted else 'SYS: Microphone active.')

    def _toggle_push_to_talk(self):
        self._voice_input_enabled = not self._voice_input_enabled
        self.write_log(
            'SYS: Push-to-talk microphone active.'
            if self._voice_input_enabled
            else 'SYS: Push-to-talk microphone paused.'
        )
        self._apply_state('LISTENING' if self._voice_input_enabled else 'MUTED')

    @property
    def voice_input_enabled(self):
        return self._voice_input_enabled and not self._muted

    def _toggle_inline_cam(self, checked: bool | None = None):
        def _apply():
            try:
                logo = getattr(self, '_header_logo', None)
                lbl = getattr(self, '_cam_live_lbl', None)
                if logo is None or lbl is None:
                    return
                want = (not self._cam_inline) if checked is None else bool(checked)
                self._cam_toggle_btn.setChecked(want)
                if want and not self._cam_inline:
                    gp = logo.mapToGlobal(QPoint(0, 0))
                    g = QRect(gp, logo.size())
                    lbl.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                       | Qt.WindowType.WindowStaysOnTopHint)
                    lbl.setGeometry(g)
                    logo.hide()
                    self._cam_inline = True
                    self.start_camera_stream()
                    lbl.show(); lbl.raise_()
                    self.write_log('VIS: Webcam docked into the reactor frame.')
                elif not want and self._cam_inline:
                    self._cam_inline = False
                    self.stop_camera_stream()
                    logo.show()
                    self.write_log('VIS: Webcam undocked — reactor restored.')
            except Exception as e:
                self.write_log(f'ERR: Reactor webcam — {e}')
        self.run_on_ui(_apply)

    def toggle_camera_overlay(self, enabled: bool | None = None):
        def _toggle():
            current = bool(getattr(self, '_cam_live_lbl', None) and self._cam_live_lbl.isVisible())
            target = (not current) if enabled is None else bool(enabled)
            if target:
                screen = QApplication.primaryScreen()
                if screen is not None:
                    sg = screen.availableGeometry()
                    w = min(720, max(360, int(sg.width() * 0.46)))
                    h = int(w * 0.75)
                    h = min(540, max(270, h))
                    self._cam_live_lbl.resize(w, h)
                    self._cam_live_lbl.move(
                        sg.left() + (sg.width() - w) // 2,
                        sg.top() + (sg.height() - h) // 2,
                    )
                self.start_camera_stream()
                self._cam_live_lbl.show()
                self._cam_live_lbl.raise_()
                self._cam_live_lbl.activateWindow()
                try:
                    self.write_log('VIS: Vision overlay enabled — live camera active.')
                except Exception:
                    pass
            else:
                self.stop_camera_stream()
                try:
                    self._cam_live_lbl.set_scan(False)
                    self._cam_live_lbl._had_faces = False
                    self._cam_live_lbl._faces = []
                    self._cam_live_lbl.hide()
                except Exception:
                    pass
                try:
                    self.write_log('VIS: Vision overlay disabled.')
                except Exception:
                    pass
        self.run_on_ui(_toggle)

    def start_camera_stream(self):
        self.run_on_ui(self._do_start_camera_stream)

    def _do_start_camera_stream(self):
        if not bool(getattr(self, '_features', {}).get('camera_access', True)):
            self.write_log('SYS: Camera access is revoked in Settings → Permissions.')
            return
        if getattr(self, '_cam_thread', None) is not None and self._cam_thread.is_alive():
            self._cam_stream_sig.emit(True)
            return
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        self._cam_thread = threading.Thread(target=self._cam_loop, daemon=True, name='jarvis-camera')
        self._cam_thread.start()

    def _cam_loop(self):
        _shared_started = False
        try:
            from vision.camera import get_camera_manager as _gmgr
            mgr = _gmgr()
            if mgr.enabled:
                def _cb(jpg):
                    try:
                        if jpg:
                            self._cam_frame_sig.emit(jpg)
                    except Exception:
                        pass
                mgr.subscribe(_cb, max_fps=24.0)
                if not mgr.running:
                    if not mgr.start():
                        mgr.unsubscribe(_cb)
                        raise RuntimeError('shared camera failed to start')
                    _shared_started = True
                self._cam_stop.wait()
                mgr.unsubscribe(_cb)
                if _shared_started:
                    mgr.stop()
                self._cam_stream_sig.emit(False)
                return
        except Exception:
            try:
                _cb = locals().get('_cb')
                if _cb is not None:
                    _gmgr().unsubscribe(_cb)
            except Exception:
                pass
            if _shared_started:
                try:
                    _gmgr().stop()
                except Exception:
                    pass
        try:
            import cv2
            try:
                _cfg_idx = int((_read_full_config().get("camera_index", 0) or 0))
            except Exception:
                _cfg_idx = 0
            cap = cv2.VideoCapture(_cfg_idx)
            if not cap.isOpened() and _cfg_idx != 0:
                cap.release()
                cap = cv2.VideoCapture(0)
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ok, frame = cap.read()
                if ok:
                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e: self.write_log(f'ERR: Camera — {e}')
        finally: self._cam_stream_sig.emit(False)

    def stop_camera_stream(self):
        self.run_on_ui(self._do_stop_camera_stream)

    def _do_stop_camera_stream(self):
        try:
            if getattr(self, '_cam_live_lbl', None) is not None:
                self._cam_live_lbl._save_geom()
        except Exception:
            pass
        self._cam_stop.set()
        self._cam_thread = None
        try:
            self._cam_live_lbl.set_scan(False)
            self._cam_live_lbl._faces = []
            self._cam_live_lbl._had_faces = False
            self._cam_live_lbl.hide()
        except Exception:
            pass

    def _show_camera_frame(self,img_bytes):
        # Chat-tab Vision consumes the same camera stream directly.
        # Suppress the legacy floating camera preview in that mode.
        if bool(getattr(self, '_chat_vision_inline', False)):
            try:
                self._cam_preview.hide()
            except Exception:
                pass
            return
        self._cam_preview.show_frame(img_bytes); self._cam_preview.show(); self._cam_preview.raise_(); self._position_overlays()

    def scan_pulse(self):
        try:
            if self._cam_live_lbl.isVisible():
                self._cam_live_lbl.set_scan(True, single=True)
        except Exception:
            pass

    def _on_cam_stream(self,start):
        # In Chat-tab Vision mode, never open the legacy floating webcam.
        if bool(getattr(self, '_chat_vision_inline', False)):
            try:
                self._cam_live_lbl.hide()
            except Exception:
                pass
            return
        if start:
            self._cam_live_lbl.show(); self._cam_live_lbl.raise_()
        else:
            try: self._cam_live_lbl.set_scan(False)
            except Exception: pass
            self._cam_live_lbl.hide()

    def _on_cam_frame(self,data):
        if bool(getattr(self, '_chat_vision_inline', False)):
            try:
                win = getattr(self, '_command_window', None)
                if win is not None and hasattr(win, '_on_local_camera_frame'):
                    win._on_local_camera_frame(data)
            except Exception:
                pass
            # Never send inline Vision frames to the legacy floating camera surface.
            return
        px=QPixmap(); px.loadFromData(data)
        if px.isNull(): return
        try:
            self._cam_live_lbl.set_frame(px)
        except Exception:
            pass
        n = getattr(self._cam_live_lbl, '_frame_n', 0) + 1
        self._cam_live_lbl._frame_n = n
        if n % 10:
            return
        if getattr(self, '_face_detect_busy', False):
            return
        self._face_detect_busy = True
        jpg = bytes(data)
        lbl = self._cam_live_lbl

        def _detect():
            try:
                faces = _detect_faces(jpg)
            except Exception:
                faces = []
            def _apply():
                self._face_detect_busy = False
                try:
                    lbl.set_faces(faces)
                    had = bool(getattr(lbl, '_had_faces', False))
                    if faces and not had:
                        spots = []
                        for (fx, fy, fw, fh) in faces:
                            cxp = fx + fw / 2
                            spots.append('left' if cxp < 0.38 else ('right' if cxp > 0.62 else 'center'))
                        self.write_log(f"VIS: {len(faces)} face(s) in webcam view ({', '.join(spots)}).")
                    lbl._had_faces = bool(faces)
                except Exception:
                    pass
            self.run_on_ui(_apply)
        threading.Thread(target=_detect, daemon=True).start()

    def _check_config(self):
        try:
            d=json.loads(API_FILE.read_text(encoding='utf-8')); return bool(d.get('gemini_api_key')) and bool(d.get('os_system'))
        except Exception: return False

    def _show_setup(self):
        ov=SetupOverlay(None); ov.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint); ov.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        ow,oh=460,390; sg=QApplication.primaryScreen().availableGeometry(); ov.setGeometry(sg.left()+(sg.width()-ow)//2,sg.top()+(sg.height()-oh)//2,ow,oh); ov.done.connect(self._on_setup_done); ov.show(); ov.raise_(); ov.activateWindow(); self._overlay=ov

    def _on_setup_done(self,key,os_name):
        os.makedirs(CONFIG_DIR,exist_ok=True); cfg=_read_full_config(); cfg.update({'gemini_api_key':key,'os_system':os_name}); API_FILE.write_text(json.dumps(cfg,indent=4),encoding='utf-8'); self._ready=True
        if self._overlay: self._overlay.hide(); self._overlay=None
        self._restore_window_position(animate=True)
        self._apply_state('LISTENING'); self.write_log(f'SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.')

    def prompt_reconfig(self): self._ready=False; self._reconfig_sig.emit()

    def _show_confirm_banner(self,title,detail):
        try:
            self._hide_confirm_banner(); ov=ConfirmBanner(title,detail,parent=self.centralWidget()); ov.answered.connect(self._on_confirm_answered); ov.adjustSize(); ov.move((self.centralWidget().width()-ov.width())//2,18); ov.show(); ov.raise_(); self._confirm_overlay=ov
        except Exception as e: self.write_log(f'ERR: Confirmation — {e}')

    def _hide_confirm_banner(self):
        ov=self._confirm_overlay
        if ov: ov.hide(); ov.deleteLater(); self._confirm_overlay=None

    def _on_confirm_answered(self,accepted):
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve; resolve(bool(accepted))
        except Exception as e: self.write_log(f'ERR: Confirmation failed — {e}')

    def show_confirm(self,title,detail): self._confirm_sig.emit(str(title),str(detail))
    def hide_confirm(self): self._confirm_hide_sig.emit()

    def _open_customize(self):
        try:
            cfg=_read_full_config(); ov=CustomizeOverlay(cfg.get('assistant_name','JARVIS') or 'JARVIS',cfg.get('user_name',''),cfg.get('ui_color','') or DEFAULT_UI_COLOR,cfg.get('voice_name',''),parent=self.centralWidget()); ov.saved.connect(self._apply_name_update); ov.adjustSize(); ov.move(max(10,(self.centralWidget().width()-ov.width())//2),max(10,(self.centralWidget().height()-ov.height())//2)); ov.show(); ov.raise_(); self._customize_overlay=ov
        except Exception as e: self.write_log(f'ERR: Customize — {e}')

    def _apply_name_update(self,name,user_name,ui_color='',voice=''):
        self._assistant_name=name.strip() or 'JARVIS'; self._brand.setText(self._assistant_name.upper()); self.hud.assistant_name=self._assistant_name.upper(); self.hud.update()
        if ui_color:
            old=current_palette()
            if apply_ui_accent(ui_color): retheme_all_widgets(old,current_palette())
        cfg=_read_full_config(); cfg['assistant_name']=self._assistant_name; cfg['user_name']=user_name.strip();
        if ui_color: cfg['ui_color']=ui_color.strip().lower()
        API_FILE.parent.mkdir(parents=True,exist_ok=True); API_FILE.write_text(json.dumps(cfg,indent=4),encoding='utf-8')
        if voice: self.write_log(f'SYS: Voice updated — {voice}')
        self.write_log(f'SYS: Identity updated — {self._assistant_name.upper()}')

    def _show_audio_devices(self): self.write_log('SYS: Audio devices are controlled by the existing JARVIS audio configuration.')

    def _remote_clicked(self):
        if self.on_remote_clicked:
            try: self.on_remote_clicked()
            except Exception as e: self.write_log(f'ERR: Remote — {e}')

    def _do_interrupt(self):
        if self.on_interrupt: self.on_interrupt()
    def _toggle_fullscreen(self): self.showNormal() if self.isFullScreen() else self.showFullScreen()
    def _update_clock(self): self._top_metrics.setToolTip(time.strftime('%Y-%m-%d  %H:%M:%S'))
    def _update_metrics(self):
        try:
            s=_metrics.snapshot(); gpu=f'{s["gpu"]:.0f}%' if s['gpu']>=0 else 'N/A'; self._top_metrics.setText(f'CPU {s["cpu"]:.0f}%   RAM {s["mem"]:.0f}%   GPU {gpu}')
            try:
                win = getattr(self, '_command_window', None)
                if win is not None and win.isVisible():
                    tmp = f'{s["tmp"]:.0f}°C' if s.get('tmp', -1) >= 0 else '--'
                    win.set_metrics_text(f'CPU {s["cpu"]:.0f}%  |  RAM {s["mem"]:.0f}%  |  GPU {gpu}  |  TEMP {tmp}')
            except Exception:
                pass
            for attr, value in (('cpu', f'{s["cpu"]:.0f}%'), ('mem', f'{s["mem"]:.0f}%'), ('gpu', gpu)):
                label = getattr(self, '_dashboard_' + attr, None)
                if label is not None:
                    label.setText(value)
        except Exception: pass
    def notify_phone_connected(self): self.write_log('SYS: Phone connection detected.')
    def _create_desktop_shortcut(self): self.write_log('SYS: Desktop shortcut creation remains available in the original system module.')

    def eventFilter(self,obj,event):
        is_panel_drag = bool(obj.property('_jarvis_panel_drag')) if isinstance(obj, QWidget) else False
        is_popup_drag = bool(obj.property('_jarvis_popup_drag')) if isinstance(obj, QWidget) else False
        if isinstance(obj, QWidget) and bool(obj.property('_jarvis_floating_panel')) and event.type() == QEvent.Type.Resize:
            self._schedule_panel_autosave(obj)
            return super().eventFilter(obj, event)

        if is_panel_drag or is_popup_drag:
            target = obj.window()
            if target is not None:
                if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self._drag_target = target
                    self._drag_start_global = event.globalPosition().toPoint()
                    self._drag_start_window = target.frameGeometry().topLeft()
                    self._drag_moved = False
                    return False
                if event.type() == QEvent.Type.MouseMove and getattr(self, '_drag_start_global', None) is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    current = event.globalPosition().toPoint()
                    delta = current - self._drag_start_global
                    if not self._drag_moved and delta.manhattanLength() < QApplication.startDragDistance():
                        return False
                    self._drag_moved = True
                    target.move(self._drag_start_window + delta)
                    return True
                if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                    if getattr(self, '_drag_moved', False):
                        self._remember_panel_position(target)
                    self._drag_pos = None; self._drag_target = None; self._drag_start_global = None; self._drag_start_window = None; self._drag_moved = False
                    return False

        target = obj.window() if isinstance(obj, QWidget) else None
        return super().eventFilter(obj,event)

    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self,e):
        for url in e.mimeData().urls():
            p=url.toLocalFile()
            if p and Path(p).is_file(): self._register_opened_file(p); break
        e.acceptProposedAction()

    def closeEvent(self,e):
        self._shutdown_startup_sequence()
        self._set_taskbar_button_hidden(False)
        try:
            if getattr(self, '_three_d_display', None): self._three_d_display.close()
        except Exception: pass
        self.stop_camera_stream()
        try:
            vp=self._video_panel.findChild(VideoPreview); vp.stop() if vp else None
        except Exception: pass
        e.accept()


class _RootShim:
    def __init__(self, app: QApplication): self._app=app
    def mainloop(self): self._app.exec()
    def protocol(self,*_): pass


def _panel_design_css():
    """Minimal module skin matching the Settings backdrop and main chat surface."""
    return f"""
    QFrame#HudPanel, QFrame#JarvisPanel_video, QFrame#JarvisPanel_model,
    QFrame#JarvisPanel_web, QFrame#JarvisPanel_task, QFrame#JarvisPanel_world,
    QFrame#JarvisPanel_memory, QFrame#JarvisPanel_tools, QFrame#JarvisPanel_window,
    QFrame#JarvisPanel_image, QFrame#JarvisPanel_activity, QFrame#JarvisPanel_content,
    QFrame#PanelContent {{
        background: transparent;
        border: none;
        border-radius: 0;
    }}
    QLabel {{ background: transparent; border: none; }}
    QLineEdit, QTextEdit, QListWidget, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: rgba(1, 15, 25, 235);
        color: {C.WHITE};
        border: 1px solid rgba(88, 214, 245, 62);
        border-radius: 8px;
        padding: 7px 10px;
        font: 9pt "Rajdhani";
        selection-background-color: rgba(10, 134, 184, 120);
        selection-color: {C.WHITE};
    }}
    QLineEdit:hover, QTextEdit:hover, QListWidget:hover, QComboBox:hover,
    QSpinBox:hover, QDoubleSpinBox:hover {{
        border-color: rgba(88, 214, 245, 120);
    }}
    QLineEdit:focus, QTextEdit:focus, QListWidget:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {C.PRI};
        background: rgba(2, 24, 38, 245);
    }}
    QComboBox::drop-down {{ width: 24px; border: none; background: transparent; }}
    QComboBox QAbstractItemView {{
        background: #02060c; color: {C.TEXT};
        border: 1px solid rgba(88, 214, 245, 85);
        border-radius: 6px;
        selection-background-color: rgba(10, 134, 184, 130);
        selection-color: {C.WHITE};
    }}
    QListWidget {{ outline: none; padding: 6px; }}
    QListWidget::item {{ padding: 8px 10px; margin: 2px 0; border-radius: 7px; }}
    QListWidget::item:hover {{ background: rgba(0, 106, 145, 55); }}
    QListWidget::item:selected {{ background: rgba(0, 137, 183, 85); border: 1px solid rgba(88, 214, 245, 95); }}
    QCheckBox {{ color: {C.TEXT_MED}; spacing: 8px; font: 600 9pt "Rajdhani"; background: transparent; }}
    QPushButton {{
        background: rgba(1, 15, 25, 210); color: {C.TEXT};
        border: 1px solid rgba(88, 214, 245, 62); border-radius: 7px;
        padding: 7px 12px; font: 700 8pt "Exo 2"; letter-spacing: 1px;
    }}
    QPushButton:hover {{ color: {C.WHITE}; background: rgba(0, 72, 98, 125); border-color: {C.PRI}; }}
    QPushButton:pressed {{ background: rgba(0, 120, 157, 185); }}
    QScrollBar:vertical {{ background: transparent; width: 6px; margin: 3px; }}
    QScrollBar::handle:vertical {{ background: rgba(30,116,142,105); border-radius: 3px; min-height: 26px; }}
    QScrollBar::handle:vertical:hover {{ background: {C.PRI}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 3px; }}
    QScrollBar::handle:horizontal {{ background: rgba(30,116,142,105); border-radius: 3px; min-width: 26px; }}
    """


def _new_panel_base(self):
    f = _HudPanel(None)
    f.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    f.setMinimumSize(380, 250)
    f.setObjectName('HudPanel')
    f.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    f.setMouseTracking(True)
    f.setStyleSheet(_panel_design_css())
    f.setProperty('_jarvis_floating_panel', True)

    # Reuse the exact Settings background language: angular dark field, subtle
    # grid, cyan atmospheric glow, and a single clean frame.
    try:
        backdrop = _ControlCenterBackdrop(f)
        backdrop.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        backdrop.setGeometry(f.rect())
        backdrop.lower()
        f._settings_backdrop = backdrop

        def _fit_backdrop(ev, _f=f, _b=backdrop):
            QFrame.resizeEvent(_f, ev)
            try:
                _b.setGeometry(_f.rect())
                _b.lower()
            except Exception:
                pass
        f.resizeEvent = _fit_backdrop
    except Exception:
        pass
    return f


def _new_panel_header(self, parent, icon='✦', close_cb=None, title='', subtitle=''):
    bar = QFrame(parent)
    bar.setObjectName('PanelHeader')
    bar.setFixedHeight(54)
    bar.setStyleSheet(f"""
        QFrame#PanelHeader {{
            background: transparent;
            border: none;
            border-radius: 0;
        }}
        QLabel {{ background: transparent; }}
    """)
    row = QHBoxLayout(bar); row.setContentsMargins(12, 6, 8, 6); row.setSpacing(9)
    icon_l = QLabel(icon, bar)
    icon_l.setFixedWidth(26); icon_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_l.setStyleSheet(f'color:{C.PRI};font:800 15pt "Rajdhani";background:transparent;border:none;border-radius:0;padding:0;')
    row.addWidget(icon_l)
    title_box = QVBoxLayout(); title_box.setSpacing(1); title_box.setContentsMargins(0,0,0,0)
    title_l = QLabel(title or 'HUD WINDOW', bar)
    title_l.setStyleSheet(f'color:{C.WHITE};font:800 9.5pt "Orbitron";letter-spacing:1.8px;background:transparent;border:none;')
    title_box.addWidget(title_l)
    if subtitle:
        sub_l = QLabel(subtitle, bar)
        sub_l.setStyleSheet(f'color:{C.TEXT_DIM};font:700 6.5pt "Exo 2";letter-spacing:1.4px;background:transparent;border:none;')
        title_box.addWidget(sub_l)
    row.addLayout(title_box, 1)
    live = QLabel('● LIVE', bar)
    live.setStyleSheet(f'color:{C.GREEN};font:800 6.5pt "Exo 2";background:transparent;border:none;')
    row.addWidget(live)
    x = _GlowSquareButton('×', bar, 30)
    x.setToolTip('Close')
    if close_cb: x.clicked.connect(close_cb)
    row.addWidget(x)
    parent.layout().insertWidget(0, bar)
    for w in (bar, icon_l, title_l):
        w.setProperty('_jarvis_panel_drag', True)
        w.installEventFilter(self)
        w.setCursor(Qt.CursorShape.SizeAllCursor)
    return x


def _new_quick_panel(self):
    f = self._panel_base(); f.setObjectName('JarvisPanel_quick'); f.setFixedWidth(390); f.setMinimumHeight(520)
    l = QVBoxLayout(f); l.setContentsMargins(12,12,12,12); l.setSpacing(10)
    self._panel_header(f, '✦', lambda: self._set_panel_visible(f, False), 'J.A.R.V.I.S', '')

    hero = QFrame(); hero.setStyleSheet(f'background:rgba(0,31,45,115);border:1px solid rgba(69,184,219,105);border-radius:12px;')
    hl = QVBoxLayout(hero); hl.setContentsMargins(11,10,11,10); hl.setSpacing(3)
    self._panel_command_input = QLineEdit(); self._panel_command_input.setPlaceholderText('Ask JARVIS to do something…'); self._panel_command_input.setMinimumHeight(39)
    self._panel_command_input.returnPressed.connect(lambda: self._send_backend_command(self._panel_command_input.text()))
    hl.addWidget(self._panel_command_input)
    l.addWidget(hero)

    section = QLabel('MODULES'); section.setStyleSheet(f'color:{C.PRI};font:800 7pt "Orbitron";letter-spacing:2px;background:transparent;padding-left:3px;')
    l.addWidget(section)
    grid = QGridLayout(); grid.setSpacing(8)
    actions = [
        ('ACTIVITY', '◉', self._open_activity_panel), ('TOOLS', '▣', self._open_tools_panel),
        ('WEB', '◌', self._open_webview_panel), ('MEMORY', '◆', self._open_memory_panel),
        ('SYSTEM', '◎', self._open_world_monitor), ('VIDEO', '▶', self._open_video_panel),
        ('IMAGE', '▧', self._open_image_panel), ('3D', '◇', self._open_3d_display),
        ('WEB TASK', '⌁', self._open_web_task_panel), ('CONTENT', '⌕', lambda: self._set_panel_visible(self._content_panel, True)),
        ('BRAIN', '🧠', self._open_brain_panel), ('CONTROL CENTER', '⚙', self._open_full_settings),
        ('LAYOUT', '⌘', self._open_layout_editor),
    ]
    for i, (txt, ic, cb) in enumerate(actions):
        b = _GlowButton(txt, ic, compact=False); b.setMinimumHeight(46); b.clicked.connect(cb)
        grid.addWidget(b, i // 2, i % 2)
    l.addLayout(grid)

    status = QFrame(); status.setStyleSheet(f'background:rgba(0,19,29,145);border:1px solid rgba(64,154,183,85);border-radius:10px;')
    sr = QHBoxLayout(status); sr.setContentsMargins(9,7,9,7); sr.setSpacing(8)
    dot = QLabel('●'); dot.setStyleSheet(f'color:{C.GREEN};font-size:10pt;background:transparent;'); sr.addWidget(dot)
    tx = QLabel('VOICE LINK READY'); tx.setStyleSheet(f'color:{C.TEXT};font:800 7pt "Exo 2";letter-spacing:1px;background:transparent;'); sr.addWidget(tx,1)
    sr.addWidget(QLabel('ENTER', status))
    l.addWidget(status)
    return f


def _new_floating_panel(self, widget, kind):
    names = {
        'video': ('VIDEO PREVIEW','LOCAL MEDIA','▶'), 'model': ('3D DISPLAY','HOLOGRAM','◇'),
        'web': ('WEB CONSOLE','BROWSER SURFACE','◌'), 'task': ('WEB TASK','RESEARCH QUEUE','⌁'),
        'world': ('SYSTEM MONITOR','LIVE TELEMETRY','◎'), 'memory': ('MEMORY CORE','LONG-TERM STORE','◆'),
        'brain': ('JARVIS BRAIN','PERSISTENT NEURAL CORE','🧠'), 'tools': ('TOOLS DECK','COMMAND LIBRARY','▣'), 'window': ('WORKSPACE','CUSTOM SURFACE','▤'),
    }
    title, sub, icon = names.get(kind, ('HUD WINDOW','J.A.R.V.I.S','✦'))
    f = self._panel_base(); f.setObjectName(f'JarvisPanel_{kind}'); f.setProperty('_jarvis_panel_kind', str(kind)); f.setProperty('_jarvis_panel_key', str(kind))
    l = QVBoxLayout(f); l.setContentsMargins(11,11,11,11); l.setSpacing(9)
    self._panel_header(f, icon, lambda: self._set_panel_visible(f,False), title, sub)
    content = QFrame(); content.setObjectName('PanelContent'); content.setStyleSheet('QFrame#PanelContent{background:transparent;border:none;border-radius:0;}')
    cl = QVBoxLayout(content); cl.setContentsMargins(9,9,9,9); cl.setSpacing(7); cl.addWidget(widget,1)
    l.addWidget(content,1)
    self._install_panel_drag_surface(f)
    return f


def _new_model_panel(self):
    f=self._panel_base(); f.setObjectName('JarvisPanel_model'); f.setMinimumSize(560,470)
    l=QVBoxLayout(f); l.setContentsMargins(11,11,11,11); l.setSpacing(9)
    self._panel_header(f,'◇',lambda:self._set_panel_visible(f,False),'3D DISPLAY','INTERACTIVE HOLOGRAM')
    stage=QFrame(); stage.setStyleSheet('background:rgba(1,15,25,115);border:none;border-radius:0px;')
    sl=QVBoxLayout(stage); sl.setContentsMargins(6,6,6,6); sl.setSpacing(6)
    self._model_view=Model3DView(); sl.addWidget(self._model_view,1)
    self._model_status=QLabel('NO MODEL LOADED  ·  READY'); self._model_status.setStyleSheet(f'color:{C.TEXT_DIM};font:700 7pt "Exo 2";padding:2px 5px;background:transparent;'); sl.addWidget(self._model_status)
    l.addWidget(stage,1)
    row=QHBoxLayout(); row.setSpacing(7)
    for label,icon,cb in [('OPEN MODEL','＋',self._open_model),('RESET','↻',self._model_view.reset_view),('WIREFRAME','◇',self._model_view.toggle_wireframe)]:
        b=_GlowButton(label,icon,compact=True); b.clicked.connect(cb); row.addWidget(b)
    row.addStretch(1); l.addLayout(row)
    return f


def _new_activity_panel(self):
    f=self._panel_base(); f.setObjectName('JarvisPanel_activity'); f.setMinimumSize(620,420)
    l=QVBoxLayout(f); l.setContentsMargins(11,11,11,11); l.setSpacing(9)
    self._panel_header(f,'◉',lambda:self._set_panel_visible(f,False),'ACTIVITY','LIVE EVENT STREAM')
    top=QFrame(); top.setStyleSheet('background:transparent;border:none;')
    tr=QHBoxLayout(top); tr.setContentsMargins(9,7,9,7); tr.setSpacing(8)
    lab=QLabel('FILTER'); lab.setStyleSheet(f'color:{C.TEXT_DIM};font:800 6.5pt "Exo 2";background:transparent;'); tr.addWidget(lab)
    self._activity_filter=QComboBox(); self._activity_filter.addItems(['ALL','VOICE','AI','TOOL','WEB','FILE','OK','WARN','ERROR','SYS']); self._activity_filter.setMinimumHeight(32); self._activity_filter.currentTextChanged.connect(lambda t:self._activity.set_filter(t)); tr.addWidget(self._activity_filter,1)
    tr.addWidget(QLabel('LIVE'))
    l.addWidget(top)
    body=QFrame(); body.setStyleSheet('background:transparent;border:none;')
    bl=QVBoxLayout(body); bl.setContentsMargins(6,6,6,6); bl.addWidget(_ActivityTimeline())
    self._activity=bl.itemAt(0).widget(); l.addWidget(body,1)
    self._activity_add('SYS: UI online')
    return f


def _new_image_panel(self):
    f=self._panel_base(); f.setObjectName('JarvisPanel_image'); f.setMinimumSize(700,520)
    l=QVBoxLayout(f); l.setContentsMargins(11,11,11,11); l.setSpacing(9)
    self._panel_header(f,'▧',lambda:self._set_panel_visible(f,False),'IMAGE PREVIEW','VISUAL ANALYSIS SURFACE')
    toolbar=QFrame(); toolbar.setStyleSheet('background:transparent;border:none;')
    tl=QHBoxLayout(toolbar); tl.setContentsMargins(9,7,9,7); tl.setSpacing(7)
    self._image_status=QLabel('NO IMAGE LOADED'); self._image_status.setStyleSheet(f'color:{C.TEXT};font:800 7pt "Exo 2";letter-spacing:1px;background:transparent;'); tl.addWidget(self._image_status,1)
    o=_GlowButton('OPEN','＋',compact=True); o.clicked.connect(self._open_image_file); tl.addWidget(o)
    c=_GlowButton('CLEAR','×',compact=True); c.clicked.connect(lambda:self._image_label.clear()); tl.addWidget(c)
    l.addWidget(toolbar)
    stage=QFrame(); stage.setStyleSheet('background:rgba(1,15,25,115);border:none;border-radius:0px;')
    st=QVBoxLayout(stage); st.setContentsMargins(8,8,8,8)
    self._image_label=QLabel('DROP OR OPEN AN IMAGE'); self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self._image_label.setMinimumHeight(330); self._image_label.setStyleSheet(f'color:{C.TEXT_DIM};font:800 9pt "Exo 2";letter-spacing:1.5px;background:rgba(0,18,28,125);border:1px dashed rgba(67,170,199,95);border-radius:10px;'); self._image_label.setScaledContents(False); st.addWidget(self._image_label,1)
    l.addWidget(stage,1)
    return f


def _new_content_panel(self):
    f=self._panel_base(); f.setObjectName('JarvisPanel_content'); f.setMinimumSize(700,470)
    l=QVBoxLayout(f); l.setContentsMargins(11,11,11,11); l.setSpacing(9)
    self._panel_header(f,'⌕',lambda:self._set_panel_visible(f,False),'CONTENT SURFACE','RESEARCH · DOCUMENTS · ANSWERS')
    title=QFrame(); title.setStyleSheet('background:rgba(0,24,36,125);border:1px solid rgba(69,167,198,85);border-radius:10px;')
    tl=QHBoxLayout(title); tl.setContentsMargins(10,8,10,8)
    self._content_title=QLabel('READY'); self._content_title.setStyleSheet(f'color:{C.WHITE};font:800 9pt "Orbitron";letter-spacing:1.5px;background:transparent;'); tl.addWidget(self._content_title,1)
    tag=QLabel('MARKDOWN · CONTENT'); tag.setStyleSheet(f'color:{C.PRI};font:800 6.5pt "Exo 2";background:transparent;'); tl.addWidget(tag)
    l.addWidget(title)
    toolbar=QFrame(); toolbar.setStyleSheet('background:transparent;border:none;')
    tr=QHBoxLayout(toolbar); tr.setContentsMargins(2,0,2,0); tr.setSpacing(6)
    self._content_open_md=_GlowButton('OPEN .MD','＋',compact=True); self._content_open_md.clicked.connect(self._open_content_markdown_file); tr.addWidget(self._content_open_md)
    self._content_copy_btn=_GlowButton('COPY ALL','▣',compact=True); self._content_copy_btn.clicked.connect(lambda: self._content_text.copy_all()); tr.addWidget(self._content_copy_btn)
    tip=QLabel('**bold**  ·  ```code``` + COPY  ·  [links](https://...)'); tip.setStyleSheet(f'color:{C.TEXT_DIM};font:700 7pt "Exo 2";background:transparent;'); tr.addWidget(tip,1)
    l.addWidget(toolbar)
    self._content_text=MarkdownTextBrowser(); self._content_text.setObjectName('ContentMarkdown')
    self._content_text.setStyleSheet(_hud_field_css() + "QTextBrowser{padding:10px;} QScrollBar:vertical{width:6px;background:transparent;} QScrollBar::handle:vertical{background:rgba(30,116,142,115);border-radius:3px;min-height:26px;} QScrollBar::handle:vertical:hover{background:%s;}" % C.PRI)
    l.addWidget(self._content_text,1)
    return f


MainWindow._panel_base = _new_panel_base
MainWindow._panel_header = _new_panel_header
MainWindow._build_quick_panel = _new_quick_panel
MainWindow._build_floating_panel = _new_floating_panel
MainWindow._build_model_panel = _new_model_panel
MainWindow._build_activity_panel = _new_activity_panel
MainWindow._build_image_panel = _new_image_panel
MainWindow._build_content_panel = _new_content_panel


_old_open_full_settings = MainWindow._open_full_settings
_old_open_layout_editor = MainWindow._open_layout_editor

def _skin_window(win):
    """Make Settings/utility windows visually match the main JARVIS surface."""
    if win is None:
        return
    try:
        win.setStyleSheet(f"""
            QWidget {{ background: transparent; color: {C.WHITE}; font-family: Rajdhani, Exo 2; }}
            QFrame {{ background: transparent; border: none; border-radius: 0; }}
            QLabel {{ background: transparent; border: none; }}
            QLineEdit, QTextEdit, QComboBox, QListWidget, QSpinBox, QDoubleSpinBox {{
                background: rgba(1,15,25,235); color: {C.WHITE};
                border: 1px solid rgba(88,214,245,62); border-radius: 8px; padding: 7px 10px;
            }}
            QLineEdit:hover, QTextEdit:hover, QComboBox:hover, QListWidget:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
                border-color: rgba(88,214,245,120);
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QListWidget:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {C.PRI}; background: rgba(2,24,38,245);
            }}
            QPushButton {{
                background: rgba(1,15,25,210); color: {C.TEXT};
                border: 1px solid rgba(88,214,245,62); border-radius: 7px;
                padding: 7px 12px; font: 700 8pt "Exo 2"; letter-spacing: 1px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; background: rgba(0,72,98,125); border-color: {C.PRI}; }}
            QPushButton:pressed {{ background: rgba(0,120,157,185); }}
            QTabWidget {{ background: transparent; }}
            QTabWidget::pane {{ border: none; background: transparent; }}
            QTabBar {{ background: transparent; }}
            QTabBar::tab {{
                background: rgba(1,15,25,210); color: {C.TEXT_DIM};
                border: 1px solid rgba(88,214,245,45); border-radius: 7px;
                padding: 8px 12px; margin: 2px 4px 4px 0; font: 700 8pt "Exo 2";
            }}
            QTabBar::tab:hover {{ color: {C.WHITE}; background: rgba(0,72,98,105); border-color: rgba(88,214,245,120); }}
            QTabBar::tab:selected {{ color: {C.WHITE}; background: rgba(0,92,124,135); border-color: {C.PRI}; }}
            QCheckBox {{ background: transparent; color: {C.TEXT_MED}; spacing: 8px; }}
            QGroupBox {{ background: transparent; border: none; margin-top: 10px; padding-top: 8px; }}
            QGroupBox::title {{ background: transparent; color: {C.PRI}; padding: 0 4px; font: 800 8pt "Orbitron"; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle:vertical {{ background: rgba(30,116,142,105); border-radius: 3px; min-height: 26px; }}
            QScrollBar::handle:vertical:hover {{ background: {C.PRI}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    except Exception:
        pass


def _open_full_settings_redesign(self):
    result = _old_open_full_settings(self)
    try:
        _skin_window(getattr(self, '_full_settings_window', None))
    except Exception:
        pass
    return result

def _open_layout_editor_redesign(self):
    result = _old_open_layout_editor(self)
    try:
        _skin_window(getattr(self, '_layout_editor', None))
    except Exception:
        pass
    return result

MainWindow._open_full_settings = _open_full_settings_redesign
MainWindow._open_layout_editor = _open_layout_editor_redesign


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app=QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle('Fusion')
        try:
            from ui_theme import tooltip_css as _tt_css
            self._app.setStyleSheet(
                _tt_css()
                + "QComboBox QAbstractItemView, QListView, QListWidget {"
                  "background:rgba(4,16,24,242); color:#dff4fb;"
                  "selection-background-color:rgba(0,180,230,70);"
                  "selection-color:#ffffff; border:1px solid rgba(10,134,184,120); }"
                + "QMenu { background:rgba(4,16,24,244); color:#dff4fb;"
                  "border:1px solid rgba(10,134,184,120); padding:4px; }"
                + f"QTextEdit#QuickChat {{ font-family: '{FONT_CHAT}'; }}"
                  f"QTabBar::tab {{ font-family: '{FONT_CHAT}'; }}")
        except Exception:
            pass
        cfg=_read_full_config(); fam=cfg.get('ui_font')
        if fam: self._app.setFont(QFont(fam,10))
        self._win=MainWindow(face_path); self._win.show(); self.root=_RootShim(self._app)

    @property
    def muted(self): return self._win._muted
    @muted.setter
    def muted(self,v):
        if bool(v)!=self._win._muted: self._win._toggle_mute()
    @property
    def voice_input_enabled(self):
        return self._win.voice_input_enabled
    def feature_enabled(self, name: str) -> bool:
        return bool(self._win._features.get(name, True))
    @property
    def current_file(self): return self._win.current_file
    def __getattr__(self, name: str):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        try:
            win = object.__getattribute__(self, '_win')
        except AttributeError:
            raise AttributeError(name)
        return getattr(win, name)

    @property
    def on_text_command(self): return self._win.on_text_command
    @on_text_command.setter
    def on_text_command(self,cb): self._win.on_text_command=cb

    def begin_assistant_stream(self, who='J.A.R.V.I.S.'):
        return self._win.begin_assistant_stream(who)

    def stream_assistant_text(self, chunk):
        return self._win.stream_assistant_text(chunk)

    def finish_assistant_stream(self):
        return self._win.finish_assistant_stream()
    @property
    def on_remote_clicked(self): return self._win.on_remote_clicked
    @on_remote_clicked.setter
    def on_remote_clicked(self,cb): self._win.on_remote_clicked=cb
    @property
    def on_interrupt(self): return self._win.on_interrupt
    @on_interrupt.setter
    def on_interrupt(self,cb): self._win.on_interrupt=cb
    @property
    def on_voice_change(self): return self._win.on_voice_change
    @on_voice_change.setter
    def on_voice_change(self,cb): self._win.on_voice_change=cb
    @property
    def on_audio_device_change(self): return self._win.on_audio_device_change
    @on_audio_device_change.setter
    def on_audio_device_change(self,cb): self._win.on_audio_device_change=cb
    def show_confirm(self,title,detail): self._win._confirm_sig.emit(str(title)[:120],str(detail)[:300])
    def hide_confirm(self): self._win._confirm_hide_sig.emit()
    @property
    def get_plugins(self): return self._win.get_plugins
    @get_plugins.setter
    def get_plugins(self,cb): self._win.get_plugins=cb
    @property
    def request_say(self): return self._win.request_say
    @request_say.setter
    def request_say(self,cb): self._win.request_say=cb
    def run_on_ui(self, fn): return self._win.run_on_ui(fn)
    def call_on_ui(self, fn, timeout=15.0): return self._win.call_on_ui(fn, timeout=timeout)
    def set_audio_level(self,level): self._win.set_audio_level(level)
    def notify_phone_connected(self): self._win.notify_phone_connected()
    def set_state(self,state): self._win._state_sig.emit(state)
    def write_log(self,text): self._win._log_sig.emit(text)
    def wait_for_api_key(self):
        while not self._win._ready: time.sleep(0.1)
    def show_content(self,title,text): self._win._content_sig.emit(title[:80],text[:12000])
    def show_image_url(self,url,caption='Research image'): self._win.show_image_url(url,caption)
    def show_image_path(self,path,caption='Generated image'): self._win.show_image_path(path,caption)
    def prompt_reconfig(self): self._win._ready=False; self._win._reconfig_sig.emit()
    def show_camera_frame(self,img_bytes): self._win._camera_sig.emit(img_bytes)
    def start_camera_stream(self): self._win.start_camera_stream()
    def stop_camera_stream(self): self._win.stop_camera_stream()
    @property
    def assistant_name(self): return self._win._assistant_name
    def start_speaking(self): self.set_state('SPEAKING')
    def stop_speaking(self):
        if not self.muted: self.set_state('LISTENING')