# Migration Map — modernize-jarvis-architecture

The authoritative OLD → NEW → REASON → STATUS table for every file move in this
change. Written **before** the moves it describes (repo-organization spec);
status is updated as each batch completes and is verified.

Rules applied to every batch: update imports → update startup/asset paths →
import validation → startup smoke → affected tests → checkpoint commit.
No file is removed without a reference search proving zero usage.

## Dependency audit (task 10.1) — GUI/automation libraries

| Dependency | Used by (grep, all call-sites) | Verdict |
|---|---|---|
| `pyautogui` | computer/computer_control.py, computer/computer_settings (via actions shim), actions/send_message.py, actions/screen_processor.py | KEEP — active GUI path |
| `playwright` | computer/browser_control.py (automation contexts), actions/game_updater.py | KEEP — isolated-browser tasks |
| `pywinauto` | computer/verification fallback, computer/local backend capabilities | KEEP — window inspection |
| `pywin32` | actions/system_monitor.py, actions/reminder.py, actions/computer_settings.py (Task Scheduler/volume) | KEEP — OS integration |
| `mss` | actions/screen_processor.py (fast capture) | KEEP — screen pipeline |

## Moves — completed

| Old | New | Reason | Status |
|---|---|---|---|
| actions/computer_control.py | computer/computer_control.py | centralize GUI control; one controller layer | moved/verified (commit 4.1–4.5) |
| actions/browser_control.py | computer/browser_control.py | browser control consolidated | moved/verified |
| actions/open_app.py | computer/windows.py | application/window control consolidated | moved/verified |
| actions/desktop.py | computer/desktop_ops.py | desktop/taskbar control consolidated | moved/verified |
| (new) | computer/computer_use.py | backend-selection facade (Cua adapter + local default) | added/verified |
| (new) | computer/local_backend.py | consolidated local backend | added/verified |
| (new) | computer/cua_backend.py | Cua adapter, availability probe | added/verified |
| (new) | computer/verification.py | post-action verification | added/verified |
| actions/computer_control.py (shim) | — | keeps tool entry point/import paths alive | added/verified |
| actions/browser_control.py (shim) | — | keeps actions/flight_finder + dispatcher imports alive | added/verified |
| actions/open_app.py (shim) | — | keeps dispatcher lazy import alive | added/verified |
| actions/desktop.py (shim) | — | keeps dispatcher lazy import alive | added/verified |
| main.py::_permission_gate | security/permissions.py | one permission implementation for all subsystems | moved/verified (commit 3.1–3.3) |
| (new) | brain/self_inspect.py | self-inspection workflow | added/verified (commit 7.1–7.2) |

## Moves — planned (tasks 8.2–8.5, not yet executed)

| Old | New | Reason | Status |
|---|---|---|---|
| core/voice.py | voice/voice.py | voice ownership | pending 8.2 |
| core/tts.py | voice/tts.py | voice ownership | pending 8.2 |
| core/stt.py | voice/stt.py | voice ownership | pending 8.2 |
| core/hybrid_voice.py | voice/hybrid_voice.py | voice ownership | pending 8.2 |
| core/wake.py | voice/wake.py | voice ownership | pending 8.2 |
| core/audio_devices.py | voice/audio_devices.py | voice ownership | pending 8.2 |
| core/sfx.py | voice/sfx.py | UI sound effects (audio output) | pending 8.2 |
| core/confirm.py | security/confirm.py | irreversible-action gate = security | pending 8.2 |
| core/undo.py | security/undo.py | reversibility = security | pending 8.2 |
| core/diagnostics.py | observability/diagnostics.py | diagnostics = observability | pending 8.2 |
| core/performance.py | observability/performance.py | telemetry = observability | pending 8.2 |
| core/plugin_loader.py | app/plugin_loader.py | app-level service | pending 8.2 |
| core/installer.py | app/installer.py | app-level service | pending 8.2 |
| core/config_guard.py | app/config_guard.py | app-level service | pending 8.2 |
| core/ai_providers.py | app/ai_providers.py | app-level service | pending 8.2 |
| core/llm_client.py | app/llm_client.py | app-level service | pending 8.2 |
| core/model_router.py | app/model_router.py | app-level service | pending 8.2 |
| core/lazy.py | app/lazy.py | app-level utility | pending 8.2 |
| core/brain_bridge.py | app/brain_bridge.py | app↔brain glue | pending 8.2 |
| core/jarvis_cursor.py | ui/jarvis_cursor.py | UI interaction | pending 8.3 |
| ui.py | ui/main_window.py | UI package | pending 8.3 |
| ui_layout.py | ui/layout.py | UI package | pending 8.3 |
| ui_settings.py | ui/settings.py | UI package | pending 8.3 |
| ui_theme.py | ui/theme.py | UI package | pending 8.3 |
| main.py (3.9k lines) | app/main.py + app/lifecycle.py (thin main.py shim) | split entry from orchestration | pending 8.4 |
| start_jarvis.py | scripts/start_jarvis.py (+ root shim) | scripts own launchers | pending 8.5 |
| run_jarvis.bat | scripts/run_jarvis.bat (+ root shim) | scripts own launchers | pending 8.5 |
| run_jarvis.vbs | scripts/run_jarvis.vbs (+ root shim) | scripts own launchers | pending 8.5 |
| jarvis_kokoro.py | — | superseded by core/tts.py OnnxTTSEngine (pending zero-usage proof) | pending 10.3 |
| patch_v9.py | — | one-off migration script, superseded (pending zero-usage proof) | pending 10.3 |

## Notes

* `ui.py` is referenced by tests/ui/qt_harness.py and main.py — the 8.3 batch must
  update those import sites, not just move the file.
* `core/` re-export shims stay for one release so plugins and third-party
  scripts importing `core.tts` etc. keep working (deprecation shim pattern).
* `main.pyw` re-exports the new `app.main` entry (8.4/8.5).
* `core/prompt.txt` stays at `core/prompt.txt` this release — `main.py` reads it
  via PROMPT_PATH; moving it would churn the config path contract for no gain.
  Revisit with the settings migration.
