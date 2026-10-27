# Music Assistant Widgets (`music-assistant-widgets`)

A unified collection of interactive **Music Assistant 2.11+** interfaces and widgets designed for:
1. **Corsair iCUE (`dashboard_lcd` / Xeneon Edge Touchscreen)**
2. **Home Assistant Add-on (App)** with Sidebar Ingress, Per-Screen Profiles & One-Click Sidebar Pinning
3. **Standalone Docker / Any Web Browser or Wall Tablet**

Every widget in this repository is built from a **single source of truth**: the exact same widget code runs natively inside Corsair iCUE and is served over HTTP by the built-in multi-widget server, which automatically parses each widget's `<meta name="x-icue-property">` schema to generate a live Web Settings Drawer.

---

## Available Widgets

| Widget | iCUE Package | Web / HA Route | Description |
| :--- | :--- | :--- | :--- |
| **[`MA-Roon`](./MA-Roon)** | [`ma-roon.icuewidget`](./ma-roon.icuewidget) | `/MA-Roon` | Roon-inspired audiophile interface featuring a 3D flip Album Art / Library Browser card, synchronized LRC lyrics stage with real-time dynamic drop shadows, Butterchurn WebGL2 MilkDrop audio visualizer, and a built-in Sendspin PC/Browser player. |

---

## Featured Widget: `MA-Roon`

`MA-Roon` brings a Roon-inspired dark editorial aesthetic to Music Assistant with full touchscreen optimization:

- **Dual Connection Engine**:
  - **LAN (`ws://` / `wss://`)**: Direct low-latency local WebSocket connection to your Music Assistant server (default port `8095`).
  - **WAN (`Remote ID`)**: Built-in WebRTC DataChannel support to connect to your Music Assistant server remotely using your Music Assistant Remote ID.
- **Built-In Local Player (`@music-assistant/sendspin`)**:
  - Turns your Xeneon Edge PC, desktop browser, or wall tablet into a native **Sendspin** playback target directly inside Music Assistant—no external companion app required.
  - Customizable player name (`Built-In PC Player Name`) so you can easily identify each device across all Music Assistant interfaces.
  - Tuned specifically for iCUE's `QtWebEngine` (`file://`) and standard browsers with deterministic X25519 client identity, zero-CPU local PCM streaming, and jitter-free audio scheduling.
- **3D Flip Album Art & Full Library Browser**:
  - Tap the album art card to flip 180° into a touch-friendly Library Browser (`Favorites`, `Artists`, `Albums`, `Tracks`, `Playlists`, `Radio`, `Search`, and `Browse`).
- **Synchronized Lyrics Stage & Real-Time Dynamic Drop Shadow Engine**:
  - Displays time-synced LRC or plain lyrics alongside the artwork, with a GPU `mix-blend-mode: multiply` dual pseudo-element shadow engine that samples moving WebGL background colors in real time.
- **Butterchurn WebGL2 MilkDrop Visualizer**:
  - Built-in MilkDrop preset engine with 3-tier audio reactivity (direct Web Audio `AnalyserNode` tap for local Sendspin playback, `/milkdrop_visualizer` binary WebSocket relay for network speakers, and procedural stereo fallback).
  - In-widget **MilkDrop Settings Modal** with preset browser, favorites (`★`), Blur (`0–30px`), Opacity (`10–100%`), and bidirectional **Background Brightness (`0–200%`)** control.
- **Multi-Room Speaker & Group Volume Control**:
  - Switch active players, link/unlink speaker group members (`set_members`), and scrub individual or group volumes with Xeneon Edge touch-optimized sliders.

---

## Installation Option 1: Corsair iCUE (Xeneon Edge)

1. Download **[`ma-roon.icuewidget`](./ma-roon.icuewidget)** from this repository.
2. Open **Corsair iCUE** $\rightarrow$ select your **Xeneon Edge** screen $\rightarrow$ import `ma-roon.icuewidget` (or copy the [`MA-Roon`](./MA-Roon) folder contents to `%APPDATA%\Corsair\CUE5\html_widgets\com\musicassistant\xeneonplayer`).
3. In the widget's **Connection Settings** inside iCUE:
   - Enter either your **LAN - Server URL** (e.g., `http://192.168.1.2:8095`) or **WAN - Remote ID**.
   - Paste a **Long-Lived Access Token** generated from **Music Assistant $\rightarrow$ Profile $\rightarrow$ Long-lived access tokens**.
   - *(Optional)* Enable **Built-In PC Player (Sendspin)** and give your PC speaker a custom name.

---

## Installation Option 2: Home Assistant Add-on (App)

You can install the **Music Assistant Widgets** server directly inside Home Assistant OS / Supervised from this GitHub repository:

1. In Home Assistant, navigate to **Settings $\rightarrow$ Add-ons $\rightarrow$ Add-on Store**.
2. Click the **$\vdots$ (top-right menu) $\rightarrow$ Repositories** and add:
   ```text
   https://github.com/rxritalin/music-assistant-widgets
   ```
3. Click **Music Assistant Widgets** in the store $\rightarrow$ click **Install**, enable **Show in sidebar**, and click **Start**.
4. Open **MA Widgets** from your Home Assistant sidebar:
   - Enter your **Music Assistant Server URL** and **Long-Lived Access Token** once under **Shared Music Assistant Connection Defaults**—every widget and screen profile inherits it automatically.
   - Create **Screen Profiles** (e.g., `Office`, `Kitchen`, `Living Room`) so different screens or tablets can target different speakers or visual themes (`/MA-Roon?profile=kitchen`).
   - Click **Pin to HA Sidebar** on any widget or profile to register a dedicated full-screen Sidebar entry in Home Assistant.

---

## Installation Option 3: Standalone Docker or Local Python

For users running Music Assistant in standalone Docker (without Home Assistant OS), the server runs with **zero external dependencies**:

### Via Docker Compose
```bash
git clone https://github.com/rxritalin/music-assistant-widgets.git
cd music-assistant-widgets
docker compose up -d
```
- **Launcher & Profile Manager**: `http://<host-ip>:8096/`
- **Direct `MA-Roon` URL**: `http://<host-ip>:8096/MA-Roon`
- **Named Screen Profile**: `http://<host-ip>:8096/MA-Roon?profile=office`

### Via Local Python 3 (Zero `pip` Dependencies)
```bash
python server/app.py
```

---

## Repository Structure

```text
music-assistant-widgets/
├── MA-Roon/                    # Source code for the MA-Roon widget (iCUE + Web single source of truth)
│   ├── index.html              # Main application, UI, Sendspin, MilkDrop & Lyrics engine
│   ├── manifest.json           # Corsair iCUE widget manifest
│   ├── translation.json        # iCUE localization strings
│   ├── styles/                 # Base widget stylesheet
│   ├── scripts/                # Local bundled Butterchurn & Sendspin libraries
│   └── resources/              # Icons and standby media assets
├── ma-roon.icuewidget          # Packaged iCUE widget archive (built via `icuewidget package MA-Roon`)
├── server/
│   └── app.py                  # Zero-dependency multi-widget web server, x-icue-property parser & HA bridge
├── ma-widgets-addon/           # Home Assistant Add-on Store configuration & documentation
│   ├── config.yaml
│   ├── Dockerfile
│   └── DOCS.md
├── repository.yaml             # Home Assistant Add-on Repository metadata
├── Dockerfile                  # Standalone Docker image build file
├── docker-compose.yml          # Standalone Docker Compose configuration
├── assets/                     # Reference media & showcase assets
└── .agents/                    # Antigravity development skills & architectural guardrails
```
