# JARVIS-OS — J.A.R.V.I.S.

> A desktop-first, voice-controlled AI assistant for Windows, macOS, and Linux with computer-use automation, browser control, vision, memory, coding agents, local/offline voice, web research, and a futuristic Qt HUD.

## What JARVIS-OS can do

JARVIS-OS is designed to act as a general-purpose desktop assistant rather than only a chatbot. It can listen, reason, speak, search the web, inspect the screen/camera, operate GUI applications, work with files, use browser sessions, schedule tasks, remember information, and delegate coding work to agent backends.

The repository is organized so that the core voice/UI experience can run locally while optional services are added only when you enable them.

### Core capabilities

| Area | Capabilities |
| --- | --- |
| **Voice interaction** | Hands-free listening, push-to-talk, voice activity detection, interruption/barge-in, wake-word support, proactive speaking, microphone/device selection, voice testing |
| **AI providers** | Gemini, OpenAI, Anthropic Claude, Groq, Ollama/local models, custom OpenAI-compatible endpoints |
| **Gemini Live** | Native real-time Gemini audio session; current UI includes Charon, Puck, Kore, Fenrir, and Aoede voice choices |
| **Local/offline voice** | Bundled Piper/ONNX voice, Windows SAPI/pyttsx3, plus optional Kokoro; the bundled Piper voice does not require a cloud API key |
| **Cloud TTS** | Fish Audio, ElevenLabs, Edge TTS, Gemini voice output |
| **Computer use / CUA** | Screenshot, screen size, cursor position, app/window inspection, UI/accessibility tree, mouse, keyboard, clipboard, window management, app launching, browser tools, verification |
| **Computer-control fallback** | PyAutoGUI, PyWinAuto, Playwright and MSS-backed local automation when Cua Driver is unavailable |
| **Browser automation** | Navigate, search, click, type, tabs, dialogs, downloads, page inspection, browser state, existing-profile attachment when supported |
| **Vision** | Camera preview, object detection, face/person detection, hand detection, gesture recognition, scene descriptions, screen capture |
| **Object detection backends** | Automatic selection, Ultralytics/YOLO (optional), MediaPipe/EfficientDet, OpenCV DNN, AI multimodal fallback, or disabled mode |
| **Face / identity** | Local face detection and geometric matching; optional InsightFace backend when installed |
| **Web search** | Search, news, research, price and comparison flows; Gemini grounded search with DuckDuckGo-style fallback in the project search layer |
| **YouTube** | Search/open/control through browser automation plus transcript extraction |
| **Messaging** | Browser/desktop workflows for WhatsApp, Telegram, Signal, Discord, Instagram, Messenger and similar services where the local environment supports them |
| **Weather** | Weather reporting action and web-assisted information retrieval |
| **Flights** | Flight-finder workflows |
| **Games** | Steam/Epic updater checks and update workflows |
| **Files** | Create, move, rename, copy, delete/send-to-trash, inspect and process local files |
| **Documents/media** | Image, PDF, DOCX/text, CSV/Excel, JSON/XML, source code, PPTX, audio, video and archive workflows |
| **Audio/video processing** | Audio/video information, conversion, trimming, frame extraction, audio extraction and transcription workflows; FFmpeg is recommended for richer media operations |
| **Memory** | Local persistent memory, session continuity, recall, memory panel, optional Mem0 backend |
| **Knowledge** | Local file/corpus search by default, optional Qdrant vector/knowledge backend |
| **Coding agents** | Aider for focused edits; OpenHands for larger autonomous/multi-file tasks when available |
| **Planning/orchestration** | Optional LangGraph orchestration with a pure-Python fallback |
| **Observability** | Structured logs, action logging, redaction, diagnostics, optional Langfuse tracing |
| **Security** | Permission checks, approval/confirmation gates for higher-risk operations, rule evaluation, coding lock, shared undo stack |
| **Reminders** | OS-native scheduled reminders using Windows Task Scheduler, macOS LaunchAgents, or Linux scheduling tools when available |
| **Background automation** | Topic/background monitoring, proactive actions, reminders and periodic system checks |
| **System control** | Volume, brightness, mute, apps/windows, fullscreen, minimize/maximize, snap, task switching, browser navigation/zoom, screenshots, clipboard, lock, settings/explorer/run, Wi-Fi toggle, display sleep, restart and shutdown |
| **Desktop UI** | PyQt6 futuristic HUD with multiple draggable panels, settings, quick actions, command deck, activity log, world monitor, memory core, web view, image/video views, vision panel, 3D model view and control center |
| **3D models** | Viewer support for OBJ, STL, PLY, OFF, GLB and GLTF assets |
| **Remote dashboard** | Browser-based remote dashboard/pairing, QR-code workflow, WebSocket transport and remote controls |
| **Clipboard intelligence** | Clipboard monitoring/inspection and context-aware use in workflows |
| **Autostart** | Desktop launch helpers and OS-native autostart support where configured |

## Important architecture notes

### Cua Driver is the primary computer-use backend

The project contains a dedicated Cua Driver adapter under `computer/cua_backend.py`. J.A.R.V.I.S. attempts to route GUI actions through Cua Driver first when it is enabled/reachable. The local backend is used as a deliberate fallback rather than silently pretending Cua Driver is active.

The integration is CLI-based and expects `cua-driver` to be available on the machine:

```text
J.A.R.V.I.S.
   |
   +--> Cua Driver (primary GUI / computer use)
   |
   +--> Local backend (PyAutoGUI / PyWinAuto / Playwright / MSS fallback)
```

The project also records the backend, target, duration, result and diagnostics for computer actions.

### Existing browser sessions

The browser resolver checks whether a supported browser is already running and whether its local DevTools endpoint is reachable. Its intended order is:

1. **ATTACH** to the user's existing browser session when it is running and attachable.
2. **SECOND PROFILE** when the browser is already running but the active session is not attachable; this state is reported rather than silently substituted.
3. **LAUNCH** the user's real browser profile when the browser is not running.

This matters for signed-in sites: opening a completely separate automation profile can lose the user's tabs and logins.

For Chromium-family browsers, the project currently checks the conventional loopback CDP port `9222` when resolving an existing session. Cua Driver's existing-profile capability is permission-controlled.

## Compatibility

### Recommended baseline

| Component | Recommendation |
| --- | --- |
| **OS** | Windows 10/11, macOS, Linux |
| **Python** | Python 3.11+; Python 3.12 is a practical starting point |
| **Architecture** | 64-bit Python/OS strongly recommended |
| **RAM** | 8 GB can run the desktop app, but more RAM is recommended for local vision models, multiple browser tabs, Docker/OpenHands and local LLMs |
| **GPU** | Not required for the core app; GPU acceleration is optional and depends on the selected backend/model |
| **Microphone** | Required for voice input |
| **Speakers/headphones** | Required for voice output |
| **Camera** | Optional; required only for camera/vision features |
| **Internet** | Required for cloud AI/TTS/web features; not required for bundled Piper voice playback |

The original repository documentation targets Windows 10/11, macOS and Linux with Python 3.11/3.12. Because individual optional packages have different platform support, treat the table above as the project's target compatibility rather than a guarantee that every optional feature behaves identically on every OS.

## Download / install

### 1. Get the project

```bash
git clone https://github.com/Chivaspro/JARVIS2-OS.git
cd JARVIS2-OS
```

Or extract the project ZIP and open a terminal in the extracted folder.

### 2. Create a virtual environment (recommended)

#### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, you can install packages with the venv interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

#### Windows CMD

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

#### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Windows one-click dependency installer

The repository includes:

```text
install_dependencies.bat
```

Run it once after installing Python. It installs `requirements.txt` with the Python launcher when available.

## Cua Driver setup (recommended for full computer control)

Cua Driver supports Windows, macOS and Linux. The official Cua documentation currently provides a one-line installer for each platform and documents `cua-driver --version`, `cua-driver doctor`, `cua-driver status`, and `cua-driver call list_apps` as readiness checks.

Official docs:

- Install: https://cua.ai/docs/how-to-guides/driver/install
- CLI reference: https://cua.ai/docs/reference/cua-driver/cli-reference
- Connect an agent: https://cua.ai/docs/how-to-guides/driver/connect-your-agent
- Browser/profile attachment: https://cua.ai/docs/reference/cua-driver/browser-profile-attachment

### Windows

Open **PowerShell** and run the current official installer:

```powershell
irm https://cua.ai/driver/install.ps1 | iex
```

Then open a new terminal and verify:

```powershell
cua-driver --version
cua-driver doctor
cua-driver status
cua-driver call list_apps
```

If the daemon is not running in your interactive desktop session, start it with:

```powershell
cua-driver serve
```

For GUI control, keep the driver in the same interactive Windows session as the applications you want J.A.R.V.I.S. to control. Cua Driver documents special handling for Session 0/SSH because GUI APIs cannot see the user's interactive desktop from that non-interactive session.

For persistent Windows startup, Cua Driver also documents:

```powershell
cua-driver autostart enable
cua-driver autostart kick
```

### macOS

Cua Driver currently requires macOS 14 or later according to its installation documentation.

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
```

Start the driver and grant the required OS permissions:

```bash
open -n -g -a CuaDriver --args serve
cua-driver permissions grant
cua-driver permissions status
cua-driver doctor
cua-driver call list_apps
```

macOS requires Cua Driver to receive the appropriate **Accessibility** and **Screen Recording** permissions for desktop control.

### Linux

Install with:

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
```

Start it inside the interactive graphical desktop session:

```bash
cua-driver serve
```

Then from another terminal in the same desktop session:

```bash
cua-driver status
cua-driver doctor
cua-driver call list_apps
```

The daemon needs access to the same display and accessibility bus as the GUI applications you want it to control.

### Cua Driver permission modes

Cua Driver documents these modes:

| Mode | Purpose |
| --- | --- |
| `standard` | Normal interactive use; default permission mode |
| `bounded` | Restrict the agent to an explicit capability manifest |
| `unrestricted` | For disposable/fully trusted environments where you explicitly accept broad capabilities |

For this project, **standard mode is the recommended starting point**. Do not disable safeguards just to make a failing workflow appear to work.

### Existing signed-in browser access

Driving a browser that is already signed in can expose the same tabs, cookies and storage that the user has access to. Cua Driver therefore treats existing-profile attachment as a permission boundary. Only enable it for a session you intentionally want J.A.R.V.I.S. to control.

## Browser automation setup

The project includes Playwright as the browser automation layer and has additional browser/session logic for Chrome, Edge, Brave, Vivaldi, Opera and Firefox.

Install a local Playwright browser when you want a bundled Chromium runtime:

```powershell
python -m playwright install chromium
```

or on macOS/Linux:

```bash
python -m playwright install chromium
```

For the user's already-installed browser, the Cua Driver attachment path is preferred when it is available. Existing-browser control may require the browser to expose an attachable DevTools endpoint and the corresponding Cua permission grant.

## AI provider setup

The main provider hub supports:

- **Gemini** — text + Gemini Live voice
- **OpenAI** — text
- **Anthropic / Claude** — text
- **Groq** — text
- **Ollama** — local text models
- **Custom OpenAI-compatible** — self-hosted or compatible HTTP endpoints

Configure providers from the J.A.R.V.I.S. Settings UI or through the configuration files described below.

### Gemini

The project reads `GEMINI_API_KEY` from the environment and/or its configuration store. Gemini is also the provider used by the current native live-voice implementation.

PowerShell example:

```powershell
$env:GEMINI_API_KEY="YOUR_KEY_HERE"
python main.py
```

Prefer the persistent Settings UI/configuration over putting secrets directly into shell history.

### Ollama

Ollama can provide a local model without a cloud API key. The provider layer defaults to:

```text
http://localhost:11434/v1
```

Install Ollama separately, start the Ollama service, pull a model, and select Ollama in the AI settings. The legacy/local LLM client also supports OpenAI-compatible local servers such as LM Studio, Jan, LocalAI, llama.cpp server and vLLM.

## Voice / TTS compatibility

The Voice settings use provider-scoped configuration so one provider should not overwrite another provider's saved settings.

### Supported voice providers

| Provider | Engine | Key required | Notes |
| --- | --- | --- | --- |
| **Fish Audio** | `fish_audio` | Yes | Configurable endpoint, model, voice, format and latency |
| **ElevenLabs** | `elevenlabs` | Yes | Configurable model and voice |
| **Gemini** | `gemini` | Usually yes | Native Live voice and Gemini voice rendering |
| **Piper / ONNX** | `onnx` | No | Bundled `config/voices/jarvis-high.onnx`; local/offline |
| **Edge TTS** | `edgetts` | No API key | Internet-dependent speech service |
| **Kokoro** | `kokoro` | No cloud key | Optional local backend; install its package/model separately if used |
| **Windows SAPI** | `sapi` | No | Uses installed Windows voices via SAPI/pyttsx3 |

### Current Gemini voice choices in the UI

```text
Charon
Puck
Kore
Fenrir
Aoede
```

### Piper / ONNX

The project already contains a bundled Piper-format voice:

```text
config/voices/jarvis-high.onnx
config/voices/jarvis-high.json
```

The required packages are already in `requirements.txt`:

```text
onnxruntime
piper-tts
```

The default execution provider is CPU. GPU execution can be configured only when the matching ONNX Runtime provider/package and model/runtime combination is available on the target machine.

### Voice output modes

The current UI supports these Gemini/Piper combinations:

- **Gemini Native Audio**
- **Gemini → Piper ONNX**
- **Local Piper Only**

This makes it possible to keep the reasoning/live interaction path on Gemini while using local Piper rendering for offline CPU-based speech output.

## Optional voice and STT packages

Some advanced voice/vision components are intentionally optional and are not all installed by `requirements.txt`.

Examples:

```powershell
python -m pip install openwakeword onnxruntime
python -m pip install faster-whisper
```

`faster-whisper` is used for the optional local speech-to-text path. `openwakeword` enables the optional wake-word path.

## Vision setup

The camera/vision stack is OpenCV-based and can add optional detection backends.

### Base camera support

Included with the base requirements:

```text
opencv-python
numpy
mss
pillow
```

### Optional detectors

For YOLO/Ultralytics:

```powershell
python -m pip install ultralytics
```

For MediaPipe features:

```powershell
python -m pip install mediapipe
```

For optional InsightFace recognition:

```powershell
python -m pip install insightface onnxruntime
```

The project can use a bundled EfficientDet Lite0 model for its MediaPipe/object-detection path and can fall back to other detection strategies depending on configuration.

### Vision modes

The object detector exposes modes equivalent to:

```text
auto
ultralytics
mediapipe
opencv
ai
off
```

Vision processing is staged to reduce unnecessary camera workload. A master camera privacy toggle is available in the application.

## File and media processing dependencies

The project can inspect/process many common formats, but some parsers are optional.

### Documents

```powershell
python -m pip install pymupdf python-docx pandas openpyxl
```

Typical support includes:

- PDF
- DOCX
- TXT / Markdown / code files
- CSV
- XLSX and other spreadsheet formats supported by the installed libraries
- JSON / XML
- PPTX

### Media

For richer audio/video workflows, install **FFmpeg** and make sure `ffmpeg` and `ffprobe` are on `PATH`.

Typical media workflows include:

- inspect media metadata
- extract video frames
- extract audio
- trim/convert/compress
- transcribe supported media

## Coding agents

### Aider

J.A.R.V.I.S. includes an Aider adapter for focused/smaller edits.

Install:

```powershell
python -m pip install aider-chat
```

The executable can be overridden with:

```text
AIDER_BIN=aider
```

### OpenHands

The bundled OpenHands adapter is designed for larger autonomous/multi-file tasks and uses Docker.

Install Docker Desktop with WSL2 on Windows, start Docker, then pull the image used by the project:

```powershell
docker pull docker.all-hands.dev/all-hands-ai/openhands
```

Verify Docker:

```powershell
docker --version
docker info
```

If Docker is not installed or not running, the coding router can fall back to Aider for supported tasks.

## Optional backends

These features are not required for the basic desktop/voice experience.

| Feature | Configuration | Typical extra dependency/service |
| --- | --- | --- |
| Mem0 memory | `features.mem0_memory=true` | Mem0 package/service + `MEM0_API_KEY` |
| Qdrant knowledge | `features.qdrant_knowledge=true` | Qdrant + `QDRANT_URL` / key |
| LangGraph orchestration | `features.langgraph_orchestrator=true` | `langgraph` |
| Langfuse tracing | `features.langfuse_tracing=true` | `langfuse` + keys/host |
| Local STT | voice/local STT configuration | `faster-whisper` |
| Wake word | wake-word configuration | `openwakeword` |
| YOLO vision | object detector config | `ultralytics` |
| MediaPipe vision | object/face/hand config | `mediapipe` |
| InsightFace | face backend config | `insightface` + ONNX Runtime |
| OCR / extra document tools | workflow-dependent | Tesseract or document-specific packages |
| OpenHands | coding agent config | Docker Desktop/WSL2 |
| Aider | coding agent config | `aider-chat` |

When an optional service is missing, many project paths are designed to fall back to a local implementation rather than making the entire application unusable.

## Configuration

### `.env`

The repository includes `.env.example` as a template.

Copy it to `.env` and fill only the services you use:

```powershell
Copy-Item .env.example .env
```

Important variables include:

```text
GEMINI_API_KEY=
MEM0_API_KEY=
MEM0_USER_ID=default
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
CUA_BACKEND=auto
OPENHANDS_MODE=docker
OPENHANDS_REMOTE_URL=
AIDER_BIN=aider
JARVIS_LOG_LEVEL=INFO
JARVIS_DATA_DIR=data
```

### Main configuration store

The project also uses:

```text
config/api_keys.json
```

for persistent provider/settings state. The voice system stores provider-specific settings under the `voice` block so Fish Audio, ElevenLabs, Gemini, Piper, Edge TTS, Kokoro and SAPI settings can be kept independently.

### Feature flags

The central settings currently include switches for:

```text
langgraph_orchestrator
mem0_memory
cua_backend
langfuse_tracing
qdrant_knowledge
coding_agents
self_inspection
```

The CUA setting accepts:

```text
auto
on
off
```

`auto` is the recommended default because it lets the project detect whether Cua Driver is usable before deciding how to route GUI actions.

## Start J.A.R.V.I.S.

### Normal Windows launch

```powershell
python main.py
```

or:

```bat
run_jarvis.bat
```

### Windows background launch

The repository also includes:

```text
run_jarvis.vbs
```

which starts `main.pyw` using `pythonw.exe` without opening a normal console window.

### Startup helper

```powershell
python start_jarvis.py
```

`start_jarvis.py` can install missing declared Python dependencies with the same interpreter and writes a startup/dependency error log when startup fails.

## Useful keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `F4` | Mute/unmute microphone |
| `F6` | Push-to-talk |
| `F11` | Fullscreen |
| `Esc` | Interrupt current speech/action |
| `Ctrl+Shift+Space` | Toggle panels |
| `Shift+Home` | Close all J.A.R.V.I.S. UI windows |

The exact availability of a shortcut can depend on the active window/OS session.

## UI / dashboard

The HUD includes multiple independently useful areas, including:

- Command Deck / Quick Actions
- WebView
- Image / Content views
- World Monitor / system telemetry
- Video player
- Web Task queue
- Memory Core
- Activity Log
- Control Center
- Vision panel
- 3D model viewer
- Clipboard panel
- Settings / layout editor
- Remote dashboard

The UI is built with PyQt6 and PyQt6-WebEngine. Panels are designed around draggable HUD-style windows and persistent layout/settings data.

## Remote dashboard / service mode

The repository contains a dashboard server and an optional FastAPI service layer.

For the FastAPI service layer:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Then check:

```text
http://127.0.0.1:8010/health
```

The desktop UI also contains remote-dashboard/pairing support, including QR-code based connection flow and WebSocket transport.

For LAN exposure, review your firewall/network settings carefully and only expose the service to a trusted network.

## Diagnostics and troubleshooting

### Check Python

```powershell
python --version
python -c "import sys; print(sys.executable)"
```

Make sure you are installing packages into the same interpreter that launches J.A.R.V.I.S.

### Check PyQt6

```powershell
python -c "import PyQt6; print('PyQt6 OK')"
```

### Check Cua Driver

```powershell
cua-driver --version
cua-driver doctor
cua-driver status
cua-driver call list_apps
```

A version number alone confirms the binary exists; `status`, `doctor`, and `list_apps` are more useful for verifying desktop access.

### Check Playwright

```powershell
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
python -m playwright install chromium
```

### Check Docker / OpenHands

```powershell
docker --version
docker info
docker images
```

### Common Windows issues

**`python` / `py` not recognized**

Install Python 3.11+ and enable the PATH option. Restart the terminal afterward.

**J.A.R.V.I.S. says PyQt6 is missing**

Run:

```powershell
python -m pip install -r requirements.txt
```

using the same `python.exe` that launches the application.

**CUA is unavailable**

Run `cua-driver doctor`, make sure `cua-driver status` reports a running daemon, and confirm it is running in the interactive desktop session that contains the target application.

**Browser automation opens a second / signed-out profile**

This normally means the existing browser process is not attachable. The project intentionally reports that condition instead of silently claiming it has your signed-in session. Configure Cua Driver/browser attachment according to the official browser-profile guidance.

**OpenHands is unavailable**

Confirm Docker Desktop is installed, WSL2 is enabled where required, Docker is running, and the OpenHands image is available. The project can fall back to Aider for supported coding tasks.

**Vision is slow**

Use `auto` or a lighter detector, reduce camera processing load, disable unused detectors, or keep heavy local models off CPU when the machine is resource constrained.

**Voice is not working**

Check the selected microphone/output device, Windows/macOS/Linux audio permissions, provider credentials, and the configured voice provider. Piper/ONNX can be used as the simplest local/offline test path.

## Security and credential hygiene

Do **not** publish API keys or private credentials with the project.

The following files should be treated as secret or machine-local state:

```text
.env
config/api_keys.json
config/certs/*.key
*.bak
```

If a real API key has ever been placed in a copy of the repository that was shared publicly, revoke/rotate that key before publishing the repository again.

Computer-use and browser-control features can interact with real applications, files, accounts and signed-in websites. Use the least-privilege Cua Driver mode that fits your workflow, keep approval/confirmation gates enabled for sensitive actions, and test automation on non-critical data first.

## Project structure

```text
JARVIS-OS/
├── actions/              # Search, browser, files, messaging, system actions
├── app/                  # Provider, service and API layer
├── brain/                # Core assistant reasoning/orchestration
├── coding/               # Aider/OpenHands routing
├── computer/             # Cua Driver + local computer/browser backends
├── config/               # Settings, provider state, bundled voice/model assets
├── core/                 # Runtime, voice loop, scheduling and assistant lifecycle
├── dashboard/            # Remote dashboard/server assets
├── memory/               # Local long-term/session memory
├── knowledge/            # Knowledge retrieval/indexing
├── observability/        # Logs, tracing and diagnostics
├── plugins/              # Plugin infrastructure
├── security/             # Permissions, approvals, rules and undo
├── vision/               # Camera/screen vision and detection
├── voice/                # STT, TTS and provider-scoped voice configuration
├── tests/                # Test suite
├── OpenSpec/             # Specs/change workflows
├── main.py               # Main desktop launcher
├── main.pyw              # Windows GUI launcher target
├── start_jarvis.py       # Startup/dependency helper
├── install_dependencies.bat
├── run_jarvis.bat
├── run_jarvis.vbs
├── requirements.txt
└── .env.example
```

## Development / testing

Install the declared dependencies in a virtual environment, then run the test suite used by the project:

```powershell
python -m pytest
```

For a quick syntax/import smoke check:

```powershell
python -m compileall .
```

When changing computer-use, browser, security or voice-provider code, test both the normal path and the fallback/error path. In particular, verify which backend was actually used rather than assuming Cua Driver, Playwright or local fallback routing succeeded.

## Recommended first-time setup

For a full-featured Windows setup, this is the practical order:

1. Install **Python 3.12 64-bit**.
2. Create `.venv` and install `requirements.txt`.
3. Install **Cua Driver** and confirm `doctor`, `status` and `list_apps` pass.
4. Install Playwright Chromium with `python -m playwright install chromium`.
5. Configure **Gemini** in Settings for the main AI/Live voice path.
6. Test **Piper/ONNX** for a local voice path.
7. Install **Docker Desktop + WSL2** only if you want OpenHands.
8. Install optional vision/STT/document packages only for features you actually use.
9. Start with `run_jarvis.bat` and use the diagnostics/settings panels to verify devices, providers and backends.

## External components

JARVIS-OS can integrate with external software/services including Cua Driver, Playwright, Docker/OpenHands, Aider, Ollama, Qdrant, Mem0, LangGraph, Langfuse, FFmpeg, optional vision/STT packages, and cloud AI/TTS providers. Their own licenses, API limits, account requirements, model availability and platform support remain separate from this project.

### Official Cua Driver resources

- https://cua.ai/docs/how-to-guides/driver/install
- https://cua.ai/docs/reference/cua-driver/cli-reference
- https://cua.ai/docs/how-to-guides/driver/connect-your-agent
- https://cua.ai/docs/reference/cua-driver/browser-profile-attachment

## Notes

This README describes the capabilities and configuration paths present in the current JARVIS-OS codebase. Optional integrations may require additional downloads, credentials, models, drivers or OS permissions. Some cloud providers and model identifiers can change over time; always use the provider's current documentation when a service rejects a model or endpoint.

---

**JARVIS-OS / J.A.R.V.I.S.**  
Desktop AI • Voice • Computer Use • Vision • Browser Automation • Memory • Coding Agents
