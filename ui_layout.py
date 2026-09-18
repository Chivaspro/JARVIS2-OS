"""JARVIS layout helpers + interactive Layout Editor.

The editor can be launched standalone (python ui_layout.py) and can also be
opened from the main JARVIS Settings panel.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor

from ui_theme import (
    BG, ACCENT, TEXT_BRIGHT, STROKE_MID, ACCENT_BRIGHT,
    composed_app_css as _composed_app_css,
)


def clamp_size(width: int, height: int, min_size=(80, 80), max_size=(2000, 1400)) -> QSize:
    w = max(min_size[0], min(max_size[0], int(width)))
    h = max(min_size[1], min(max_size[1], int(height)))
    return QSize(w, h)


def clamp_position(x: int, y: int, screen_rect):
    x = max(screen_rect.left(), min(screen_rect.right(), int(x)))
    y = max(screen_rect.top(), min(screen_rect.bottom(), int(y)))
    return x, y


LAYOUT_DEFAULTS = {"elements": []}


def load_layout(path: str | Path) -> dict:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return dict(LAYOUT_DEFAULTS)


def save_layout(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")


class LayoutEditorDialog:
    """Qt dialog created lazily to keep the helper module import-light."""
    def __init__(self, main_window=None):
        from PyQt6.QtWidgets import (
            QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
            QListWidget, QPushButton, QSlider, QSpinBox, QVBoxLayout, QColorDialog,
            QCheckBox, QGroupBox,
        )
        from PyQt6.QtGui import QColor, QFont

        self._QDialog = QDialog
        self.main = main_window
        self.dialog = QDialog(main_window)
        self.dialog.setWindowTitle("JARVIS UI Layout Editor")
        self.dialog.resize(920, 650)
        # The editor used to carry its own copy of the visual language (a fifth
        # stylesheet, with its own radii). It now composes the shared chrome, so
        # the editor cannot drift away from the control center it is opened from.
        self.dialog.setStyleSheet(_composed_app_css())
        root = QHBoxLayout(self.dialog)
        self.list = QListWidget(); self.list.setMinimumWidth(220); root.addWidget(self.list)

        right = QVBoxLayout(); root.addLayout(right,1)
        props = QGroupBox("SELECTED ELEMENT"); form = QFormLayout(props)
        self._name = QLineEdit(); form.addRow("Name", self._name)
        self._x=QSpinBox(); self._x.setRange(-4000,4000); form.addRow("X",self._x)
        self._y=QSpinBox(); self._y.setRange(-4000,4000); form.addRow("Y",self._y)
        self._w=QSpinBox(); self._w.setRange(20,4000); form.addRow("Width",self._w)
        self._h=QSpinBox(); self._h.setRange(20,4000); form.addRow("Height",self._h)
        self._opacity=QSlider(Qt.Orientation.Horizontal); self._opacity.setRange(0,100); form.addRow("Opacity",self._opacity)
        self._visible=QCheckBox("Visible"); form.addRow("",self._visible)
        self._color_btn=QPushButton("Choose Color"); self._color_btn.clicked.connect(self.choose_color); form.addRow("Color",self._color_btn)
        self._text=QLineEdit(); form.addRow("Text / Label",self._text)
        right.addWidget(props)

        add_group = QGroupBox("ADD ELEMENT")
        add_row = QHBoxLayout(add_group)
        self._type = QComboBox(); self._type.addItems(["Text","Button","Panel","Separator","Window","Monitor","WebView"]); add_row.addWidget(self._type)
        add = QPushButton("ADD"); add.clicked.connect(self.add_element); add_row.addWidget(add)
        right.addWidget(add_group)

        btns=QHBoxLayout();
        apply_btn=QPushButton("APPLY"); apply_btn.clicked.connect(self.apply_selected); btns.addWidget(apply_btn)
        reset_btn=QPushButton("RESET"); reset_btn.clicked.connect(self.reload_elements); btns.addWidget(reset_btn)
        delete_btn=QPushButton("DELETE"); delete_btn.clicked.connect(self.delete_selected); btns.addWidget(delete_btn)
        save_btn=QPushButton("SAVE LAYOUT"); save_btn.clicked.connect(self.save); btns.addWidget(save_btn)
        right.addLayout(btns)
        right.addStretch(1)
        self._color=f"#56d6f5"
        self.list.currentRowChanged.connect(self.select_row)
        self._refresh_elements()
        self._update_color_button()

    def show(self):
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow(); return self.dialog

    def _candidates(self):
        m=self.main
        if m is None: return []
        out=[]
        mapping=[
            ("Arc Reactor",getattr(m,"_header_logo",None)),
            ("HUD",getattr(m,"hud",None)),
            ("Main Surface",getattr(m,"surface",None)),
            ("Top Bar",getattr(m,"_topbar",None)),
            ("Status Bar",getattr(m,"_statusbar",None)),
            ("Quick Panel",getattr(m,"quick_panel",None)),
            ("Settings Panel",getattr(m,"_settings_panel",None)),
            ("Activity Panel",getattr(m,"_activity_panel",None)),
            ("Video Preview",getattr(m,"_video_panel",None)),
            ("Image Preview",getattr(m,"_image_panel",None)),
            ("Webview",getattr(m,"_webview_panel",None)),
            ("Web Task",getattr(m,"_web_task_panel",None)),
            ("World Monitor",getattr(m,"_world_monitor_panel",None)),
            ("Memory Core",getattr(m,"_memory_panel",None)),
            ("Content Surface",getattr(m,"_content_panel",None)),
            ("Command Deck",getattr(m,"_quick_popup",None)),
            ("Control Center",getattr(m,"_full_settings_window",None)),
            ("3D Hologram",getattr(m,"_three_d_display",None)),
        ]
        if getattr(m,"_quick_popup",None) is not None and not any(n == "Command Deck" for n,_ in mapping):
            mapping.append(("Command Deck",m._quick_popup))
        for i,w in enumerate(getattr(m,"_workspace_windows",[]) or [], 1):
            if w is not None:
                mapping.append((f"New Window {i:02d}",w))
        return [(n,w) for n,w in mapping if w is not None]

    def _refresh_elements(self):
        self.list.clear()
        self._map=[]
        for n,w in self._candidates(): self.list.addItem(n); self._map.append((n,w))
        for elem in getattr(self.main,"_layout_elements",{}).values() if self.main is not None else []:
            self.list.addItem(str(elem.get("name","Element"))); self._map.append((elem.get("name","Element"),elem))
        if self.list.count(): self.list.setCurrentRow(0)

    def select_row(self,row):
        if row<0 or row>=len(getattr(self,"_map",[])): return
        name,obj=self._map[row]
        self._name.setText(name)
        if hasattr(obj,"geometry"):
            g=obj.geometry(); self._x.setValue(g.x()); self._y.setValue(g.y()); self._w.setValue(g.width()); self._h.setValue(g.height());
            self._opacity.setValue(int((obj.windowOpacity() if obj.windowOpacity() else 1.0)*100) if hasattr(obj,"windowOpacity") else 100)
            self._visible.setChecked(obj.isVisible())
        else:
            self._x.setValue(int(obj.get("x",50))); self._y.setValue(int(obj.get("y",50))); self._w.setValue(int(obj.get("w",180))); self._h.setValue(int(obj.get("h",50))); self._opacity.setValue(int(obj.get("opacity",100))); self._visible.setChecked(bool(obj.get("visible",True))); self._text.setText(str(obj.get("text","")))

    def choose_color(self):
        from PyQt6.QtWidgets import QColorDialog
        c=QColorDialog.getColor(QColor(self._color), self.dialog, "Element color")
        if c.isValid(): self._color=c.name(); self._update_color_button()
    def _update_color_button(self): self._color_btn.setText(self._color)

    def apply_selected(self):
        if self.list.currentRow()<0: return
        name,obj=self._map[self.list.currentRow()]
        if hasattr(obj,"setGeometry"):
            obj.setGeometry(self._x.value(),self._y.value(),self._w.value(),self._h.value()); obj.setVisible(self._visible.isChecked())
            try:
                if hasattr(self.main, "_remember_panel_position") and getattr(obj, "isWindow", lambda: False)():
                    if obj.property('_jarvis_panel_key') is None:
                        key = str(self._name.text().strip() or name).lower().replace(' ', '_')
                        obj.setProperty('_jarvis_panel_key', key)
                    obj.setProperty('_jarvis_floating_panel', True)
                    self.main._remember_panel_position(obj)
            except Exception:
                pass
            # The reactor has its own opacity API so the editor does not double-stack effects.
            if hasattr(obj,"set_graphic_opacity"):
                obj.set_graphic_opacity(self._opacity.value())
            elif hasattr(obj,"setWindowOpacity"):
                obj.setWindowOpacity(self._opacity.value()/100.0)
            if self._text.text() and hasattr(obj,"setText"): obj.setText(self._text.text())
            if hasattr(obj,"setStyleSheet") and self._color:
                try:
                    import re
                    ss=obj.styleSheet()
                    ss=re.sub(r'color\s*:\s*#[0-9a-fA-F]{6}', f'color:{self._color}', ss)
                    if 'color:' not in ss:
                        ss += f'\ncolor:{self._color};'
                    obj.setStyleSheet(ss)
                except Exception:
                    pass
        else:
            obj.update({"name":self._name.text().strip() or name,"x":self._x.value(),"y":self._y.value(),"w":self._w.value(),"h":self._h.value(),"opacity":self._opacity.value(),"visible":self._visible.isChecked(),"text":self._text.text(),"color":self._color})
            self.main._apply_layout_elements()
        self._refresh_elements(); self.list.setCurrentRow(min(self.list.currentRow(),self.list.count()-1))
        if self.main is not None:
            self.main._save_layout_elements()

    def add_element(self):
        if self.main is None: return
        kind=self._type.currentText(); idx=len(getattr(self.main,"_layout_elements",{}))+1
        name=f"{kind} {idx}"
        if not hasattr(self.main,"_layout_elements"): self.main._layout_elements={}
        self.main._layout_elements[name]={"name":name,"kind":kind,"x":60,"y":60,"w":220 if kind!="Separator" else 220,"h":50 if kind!="Separator" else 6,"opacity":100,"visible":True,"text":name,"color":f"#56d6f5"}
        self.main._apply_layout_elements(); self._refresh_elements(); self.list.setCurrentRow(self.list.count()-1)

    def delete_selected(self):
        row=self.list.currentRow()
        if row<0 or row>=len(self._map): return
        name,obj=self._map[row]
        if isinstance(obj,dict):
            self.main._layout_elements.pop(name,None); self.main._apply_layout_elements(); self._refresh_elements()

    def save(self):
        if self.main is None: return
        self.main._save_layout_elements()
        self.dialog.setWindowTitle("JARVIS UI Layout Editor — SAVED")

    def reload_elements(self):
        if self.main is not None:
            self.main._load_layout_elements(); self._refresh_elements()


# Keep backward-compatible alias for callers that want a simple name.
LayoutEditor = LayoutEditorDialog


if __name__ == "__main__":
    # Standalone editor has a tiny host canvas so elements can still be created.
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication(sys.argv)
    class Host(QWidget):
        def __init__(self):
            super().__init__(); self.setWindowTitle("JARVIS Layout Host"); self.resize(900,600); self._layout_elements={}
        def _apply_layout_elements(self): pass
        def _save_layout_elements(self): pass
        def _load_layout_elements(self): pass
    host=Host(); host.show(); LayoutEditorDialog(host).show(); raise SystemExit(app.exec())
