"""JARVIS Cursor — a blue-dot overlay that shows where JARVIS is acting.

This module provides a small, translucent blue circle that floats on screen
and smoothly follows JARVIS's mouse actions.  It is purely cosmetic: the
real cursor stays where the user left it; the overlay is a visual indicator
so the user can see JARVIS "pointing" at something.

Usage::

    from core.jarvis_cursor import get_cursor

    cursor = get_cursor()          # singleton, created on first call
    cursor.move_to(x, y)           # smooth animated move
    cursor.click_at(x, y)          # brief pulse animation at (x, y)
    cursor.hide() / cursor.show()  # toggle visibility
    cursor.set_enabled(True/False) # master on/off from settings

The overlay is a frameless, always-on-top, transparent QWidget painted
with QPainter.  It uses a QPropertyAnimation for smooth movement and a
brief scale-pulse for click feedback.  All heavy work is on the GUI
thread; the public API is safe to call from any thread via Qt signal
marshalling.
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import (
    Qt,
    QPointF,
    QRectF,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QRadialGradient,
    QBrush,
    QPen,
    QScreen,
)
from PyQt6.QtWidgets import QWidget, QApplication


# ── tunables ──────────────────────────────────────────────────────────────────

DOT_RADIUS     = 8        # px – radius of the solid inner dot
HALO_RADIUS    = 18       # px – outer glow ring
DOT_COLOR      = QColor(0, 180, 255, 200)   # JARVIS blue
HALO_COLOR     = QColor(0, 140, 255, 60)    # softer outer glow
PULSE_COLOR    = QColor(0, 220, 255, 160)   # click-pulse ring
MOVE_DURATION  = 180      # ms for smooth move animation
PULSE_DURATION = 220      # ms for click-pulse
PULSE_SCALE    = 2.2      # max scale multiplier during pulse
FADE_DELAY     = 600      # ms after last action before fading out
IDLE_OPACITY   = 0.0      # fully hidden when idle
ACTIVE_OPACITY = 1.0      # fully visible when active


# ── singleton ─────────────────────────────────────────────────────────────────

_instance: Optional["JarvisCursor"] = None


def get_cursor() -> "JarvisCursor":
    """Return the process-wide cursor overlay, creating it if needed."""
    global _instance
    if _instance is None:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("QApplication must exist before creating JarvisCursor")
        _instance = JarvisCursor()
    return _instance


# ── overlay widget ────────────────────────────────────────────────────────────

class JarvisCursor(QWidget):
    """A small blue-dot overlay that follows JARVIS's mouse actions."""

    # emitted so the fade-out timer can be started from any thread
    _action_fired = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Frameless, transparent, always on top, no input.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(HALO_RADIUS * 2 + 4, HALO_RADIUS * 2 + 4)

        # State
        self._enabled = True
        self._opacity = IDLE_OPACITY
        self._pulse_t = 0.0          # 0 = no pulse, >0 = animating
        self._pulse_start = 0.0
        self._target_pos: QPointF = QPointF()
        self._current_pos: QPointF = QPointF(-100, -100)
        self._moving = False

        # Animation for smooth movement
        self._move_anim = QPropertyAnimation(self, b"cursorPos")
        self._move_anim.setDuration(MOVE_DURATION)
        self._move_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Fade timer — hides cursor after a period of inactivity
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.setInterval(FADE_DELAY)
        self._fade_timer.timeout.connect(self._on_fade)

        # Pulse timer — drives the click-pulse animation
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(16)  # ~60fps
        self._pulse_timer.timeout.connect(self._tick_pulse)

        # Connect the cross-thread signal
        self._action_fired.connect(self._on_action_fired)

        # Start hidden
        self.hide()

    # ── public API (thread-safe) ──────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        """Master on/off switch.  When disabled the overlay is hidden."""
        self._enabled = bool(enabled)
        if not self._enabled:
            self.hide()
            self._fade_timer.stop()
            self._pulse_timer.stop()

    def move_to(self, x: int, y: int) -> None:
        """Smoothly animate the dot to screen coordinates (x, y)."""
        if not self._enabled:
            return
        self._target_pos = QPointF(x, y)
        self._action_fired.emit()

    def click_at(self, x: int, y: int) -> None:
        """Move to (x, y) and play the click-pulse animation."""
        if not self._enabled:
            return
        self._target_pos = QPointF(x, y)
        self._pulse_start = time.monotonic()
        self._pulse_t = 0.0
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()
        self._action_fired.emit()

    def hide_cursor(self) -> None:
        """Immediately hide the overlay."""
        self._opacity = IDLE_OPACITY
        self.hide()
        self._fade_timer.stop()
        self._pulse_timer.stop()

    def show_cursor(self) -> None:
        """Show the overlay (fades in)."""
        if self._enabled:
            self._opacity = ACTIVE_OPACITY
            self.show()
            self.update()

    # ── internal slots ────────────────────────────────────────────────────

    def _on_action_fired(self) -> None:
        """Slot connected to _action_fired; runs on the GUI thread."""
        self._opacity = ACTIVE_OPACITY
        self.show()

        # Start smooth move animation
        self._move_anim.stop()
        self._move_anim.setStartValue(self._current_pos)
        self._move_anim.setEndValue(self._target_pos)
        self._move_anim.start()

        # Restart fade timer
        self._fade_timer.start()

    def _on_fade(self) -> None:
        """Fade out after inactivity."""
        self._opacity = IDLE_OPACITY
        self.update()
        # Hide after a short delay so the fade is visible
        QTimer.singleShot(300, self._maybe_hide)

    def _maybe_hide(self) -> None:
        if self._opacity <= 0.01:
            self.hide()

    def _tick_pulse(self) -> None:
        """Drive the click-pulse animation."""
        elapsed = (time.monotonic() - self._pulse_start) * 1000.0
        if elapsed > PULSE_DURATION:
            self._pulse_t = 0.0
            self._pulse_timer.stop()
        else:
            self._pulse_t = elapsed / PULSE_DURATION
        self.update()

    # ── animated property ─────────────────────────────────────────────────

    def _get_cursor_pos(self) -> QPointF:
        return self._current_pos

    def _set_cursor_pos(self, pos: QPointF) -> None:
        self._current_pos = pos
        # Centre the widget on the dot position
        r = HALO_RADIUS + 2
        self.move(int(pos.x() - r), int(pos.y() - r))
        self.update()

    cursorPos = pyqtProperty(QPointF, _get_cursor_pos, _set_cursor_pos)

    # ── painting ──────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        if self._opacity < 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(self._opacity)

        cx = HALO_RADIUS + 2
        cy = HALO_RADIUS + 2

        # Pulse scale
        scale = 1.0
        if self._pulse_t > 0:
            # Smooth ease-out: quickly expand, then settle
            t = self._pulse_t
            scale = 1.0 + (PULSE_SCALE - 1.0) * math.sin(math.pi * t)

        # Outer halo (soft glow)
        halo_r = HALO_RADIUS * scale
        halo_grad = QRadialGradient(QPointF(cx, cy), halo_r)
        halo_grad.setColorAt(0.0, QColor(HALO_COLOR.red(), HALO_COLOR.green(),
                                          HALO_COLOR.blue(), int(80 * scale)))
        halo_grad.setColorAt(0.6, QColor(HALO_COLOR.red(), HALO_COLOR.green(),
                                          HALO_COLOR.blue(), int(40 * scale)))
        halo_grad.setColorAt(1.0, QColor(HALO_COLOR.red(), HALO_COLOR.green(),
                                          HALO_COLOR.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo_grad))
        p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        # Pulse ring (only during click animation)
        if self._pulse_t > 0:
            pulse_r = DOT_RADIUS * 2.0 * scale
            pulse_alpha = int(200 * (1.0 - self._pulse_t))
            p.setPen(QPen(QColor(PULSE_COLOR.red(), PULSE_COLOR.green(),
                                 PULSE_COLOR.blue(), pulse_alpha), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), pulse_r, pulse_r)

        # Inner solid dot
        inner_r = DOT_RADIUS * (0.9 + 0.1 * scale)
        inner_grad = QRadialGradient(QPointF(cx, cy), inner_r)
        inner_grad.setColorAt(0.0, QColor(255, 255, 255, 220))
        inner_grad.setColorAt(0.4, DOT_COLOR)
        inner_grad.setColorAt(1.0, QColor(DOT_COLOR.red(), DOT_COLOR.green(),
                                           DOT_COLOR.blue(), 120))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(inner_grad))
        p.drawEllipse(QPointF(cx, cy), inner_r, inner_r)

        p.end()


# ── convenience wrapper for pyautogui integration ─────────────────────────────

def show_jarvis_click(x: int, y: int) -> None:
    """Show the blue dot at (x, y) with a click pulse.  Call after pyautogui.click()."""
    try:
        c = get_cursor()
        c.click_at(x, y)
    except Exception:
        pass


def show_jarvis_move(x: int, y: int) -> None:
    """Show the blue dot moving to (x, y).  Call after pyautogui.moveTo()."""
    try:
        c = get_cursor()
        c.move_to(x, y)
    except Exception:
        pass


def hide_jarvis_cursor() -> None:
    """Hide the blue dot cursor."""
    try:
        c = get_cursor()
        c.hide_cursor()
    except Exception:
        pass
