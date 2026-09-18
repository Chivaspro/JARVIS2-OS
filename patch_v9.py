from pathlib import Path

root=Path('/mnt/data/jarvis_v9')
ui=root/'ui.py'; st=root/'ui_settings.py'

s=ui.read_text(encoding='utf-8')
# 1) Futuristic reactor: smooth single-direction phase and no conflicting fast rotation.
s=s.replace("self._phase = (self._phase + 2.35 * max(0.25, float(getattr(self, '_animation_speed', 1.0)))) % 360.0",
            "self._phase = (self._phase + 1.65 * max(0.25, float(getattr(self, '_animation_speed', 1.0)))) % 360.0")
# reduce competing counter-rotations for stable 360 loop
s=s.replace("base_a + self._phase * 0.62", "base_a + self._phase")
s=s.replace("deg - self._phase * 0.30", "deg + self._phase")
s=s.replace("i * 10.0 - self._phase * 0.72", "i * 10.0 + self._phase")
s=s.replace("self._phase*0.8", "self._phase")
s=s.replace("self._phase*0.42", "self._phase*0.35")
s=s.replace("self._phase*0.55", "self._phase*0.45")
s=s.replace("self._phase*0.60", "self._phase*0.50")
s=s.replace("self._phase*0.58", "self._phase*0.55")
s=s.replace("self._phase*1.05", "self._phase")
s=s.replace("self._phase*0.88", "self._phase")
# 2) Main position persistence honors setting.
s=s.replace("    def _save_window_position(self):\n        try:\n            _ui_save(API_FILE, ui_x=self.x(), ui_y=self.y())\n        except Exception:\n            pass",
"    def _save_window_position(self):\n        try:\n            cfg = _read_full_config()\n            feats = cfg.get('features', {}) if isinstance(cfg.get('features', {}), dict) else {}\n            if feats.get('remember_position', True):\n                _ui_save(API_FILE, ui_x=self.x(), ui_y=self.y())\n        except Exception:\n            pass")
s=s.replace("            cfg=_read_full_config(); x=cfg.get('ui_x'); y=cfg.get('ui_y')\n            if isinstance(x,int) and isinstance(y,int): self.move(x,y); return",
"            cfg=_read_full_config(); feats=cfg.get('features', {}) if isinstance(cfg.get('features', {}), dict) else {}\n            x=cfg.get('ui_x'); y=cfg.get('ui_y')\n            if feats.get('remember_position', True) and isinstance(x,int) and isinstance(y,int): self.move(x,y); return")
# 3) Add Windows taskbar helpers before _save_window_position.
needle="    def _save_window_position(self):\n"
insert="""    def _set_taskbar_hidden(self, hidden: bool):\n        if _OS != 'Windows':\n            return\n        try:\n            import ctypes\n            user32 = ctypes.windll.user32\n            hwnd = user32.FindWindowW('Shell_TrayWnd', None)\n            if hwnd:\n                user32.ShowWindow(hwnd, 0 if hidden else 5)\n                # Also refresh the secondary taskbar/appbar windows where present.\n                for cls in ('Shell_SecondaryTrayWnd',):\n                    hwnd2 = user32.FindWindowW(cls, None)\n                    if hwnd2:\n                        user32.ShowWindow(hwnd2, 0 if hidden else 5)\n        except Exception:\n            pass\n\n"""
s=s.replace(needle,insert+needle,1)
# 4) Initialize/apply hide_taskbar.
s=s.replace("        self._compact_size = max(120, min(500, int(cfg.get(\"compact_size\", 176) or 176)))\n", "        self._compact_size = max(120, min(500, int(cfg.get(\"compact_size\", 176) or 176)))\n        self._hide_taskbar = bool((cfg.get('features', {}) or {}).get('hide_taskbar', False))\n")
s=s.replace("        self._apply_initial_visibility()\n", "        self._apply_initial_visibility()\n        self._set_taskbar_hidden(self._hide_taskbar)\n",1)
# 5) Apply setting: don't save position when disabled, apply taskbar, avoid forced size jumps.
s=s.replace("            self._save_window_position()\n        except Exception as e:\n            self.write_log(f'ERR: Applying settings — {e}')",
"            self._hide_taskbar = bool(self._features.get('hide_taskbar', False))\n            self._set_taskbar_hidden(self._hide_taskbar)\n            self._save_window_position()\n        except Exception as e:\n            self.write_log(f'ERR: Applying settings — {e}')",1)
# 6) On close always restore taskbar.
s=s.replace("    def closeEvent(self,e):\n        try:\n            if getattr(self, '_three_d_display', None): self._three_d_display.close()\n        except Exception: pass",
"    def closeEvent(self,e):\n        self._set_taskbar_hidden(False)\n        try:\n            if getattr(self, '_three_d_display', None): self._three_d_display.close()\n        except Exception: pass")
ui.write_text(s,encoding='utf-8')

s=st.read_text(encoding='utf-8')
# 7) Defaults + settings controls.
s=s.replace('        "remember_position": True,\n', '        "remember_position": True,\n        "hide_taskbar": False,\n        "autostart": False,\n')
# Theme facelift
s=s.replace('QDialog { background:#00060a; color:#8ffcff; }', 'QDialog { background:#00050b; color:#8ffcff; }')
s=s.replace('background:#000a11;\n                border:1px solid #1a5c7a;\n                border-radius:16px;', 'background:rgba(0,10,18,235);\n                border:1px solid #00d4ff;\n                border-radius:18px;')
s=s.replace('background:#00131c;\n                border-bottom:1px solid #17465a;', 'background:#001824;\n                border-bottom:1px solid #00d4ff;')
# Add controls after reactor_size row
old='        self.reactor_size = self._slider_row(rv, "Compact reactor size", int(self._data.get("compact_size", 176)), 120, 500, " px")\n        lay.addWidget(reactor)'
new='''        self.reactor_size = self._slider_row(rv, "Arc Reactor size", int(self._data.get("compact_size", 176)), 120, 500, " px")\n        lay.addWidget(reactor)'''
s=s.replace(old,new)
old='''        self._remember = QCheckBox("Remember Arc Reactor position")\n        self._remember.setChecked(bool(self._data.get("features", {}).get("remember_position", True)))\n        for cb in (self._compact, self._drag, self._remember):\n            bv.addWidget(cb)'''
new='''        self._remember = QCheckBox("Remember Arc Reactor position")\n        self._remember.setChecked(bool(self._data.get("features", {}).get("remember_position", True)))\n        self._autostart = QCheckBox("Auto-start J.A.R.V.I.S with Windows")\n        self._autostart.setChecked(bool(self._data.get("features", {}).get("autostart", False)))\n        self._taskbar = QCheckBox("Hide Windows taskbar while J.A.R.V.I.S is running")\n        self._taskbar.setChecked(bool(self._data.get("features", {}).get("hide_taskbar", False)))\n        for cb in (self._compact, self._drag, self._remember, self._autostart, self._taskbar):\n            bv.addWidget(cb)'''
s=s.replace(old,new)
# Update collect features
s=s.replace('        features["compact_mode"] = self._compact.isChecked()\n', '        features["compact_mode"] = self._compact.isChecked()\n        features["autostart"] = self._autostart.isChecked()\n        features["hide_taskbar"] = self._taskbar.isChecked()\n')
# Reset
s=s.replace('        self._remember.setChecked(True)\n', '        self._remember.setChecked(True)\n        self._autostart.setChecked(False)\n        self._taskbar.setChecked(False)\n')
# In save_settings, after parent apply, toggle autostart if available
s=s.replace("            if parent is not None and hasattr(parent, \"_apply_full_settings\"):\n                parent._apply_full_settings(dict(self._data))\n            return self._data",
"            if parent is not None and hasattr(parent, \"_apply_full_settings\"):\n                parent._apply_full_settings(dict(self._data))\n                if hasattr(parent, '_check_autostart') and hasattr(parent, '_toggle_autostart'):\n                    desired = bool(self._autostart.isChecked())\n                    if parent._check_autostart() != desired:\n                        parent._toggle_autostart()\n            return self._data")
st.write_text(s,encoding='utf-8')

# 8) Make Quick Actions popup feel more like a HUD card.
# Replace heavy opaque background with 40% glass-like background.
s=ui.read_text(encoding='utf-8')
s=s.replace("QFrame#QuickPopup { background: rgba(1,13,20,238); border: 1px solid", "QFrame#QuickPopup { background: rgba(1,13,20,150); border: 1px solid")
# make reactor visual angular-ish with stronger outer chassis contrast
s=s.replace("p.setPen(QPen(qcol(C.PRI, 225 if not self._hover else 250), max(2.5, b * 0.015)))", "p.setPen(QPen(qcol(C.PRI, 235 if not self._hover else 255), max(2.2, b * 0.013)))")
ui.write_text(s,encoding='utf-8')

print('patched')
