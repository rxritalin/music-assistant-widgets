# Music Assistant Widgets (`music-assistant-widgets`)

A collection of interactive **Music Assistant 2.11+** interfaces and widgets designed for **Corsair iCUE (Xeneon Edge Touchscreen LCD)**, **Home Assistant**, and **Standalone Docker** displays.

---

## Available Widgets

| Widget | Package | Target Platforms | Description |
| :--- | :--- | :--- | :--- |
| **[`MA-Roon`](./MA-Roon)** | [`ma-roon.icuewidget`](./ma-roon.icuewidget) | Corsair iCUE (`dashboard_lcd` / Xeneon Edge)<br>Home Assistant / Docker *(coming in Phase 2)* | Roon-inspired audiophile interface featuring a 3D flip Album Art / Library Browser card, synchronized LRC lyrics stage with real-time dynamic drop shadows, Butterchurn WebGL2 MilkDrop audio visualizer, and a built-in Sendspin PC player. |

---

## Featured Widget: `MA-Roon`

`MA-Roon` brings a Roon-inspired dark editorial aesthetic to Music Assistant with full touchscreen optimization:

- **Dual Connection Engine**:
  - **LAN (`ws://` / `wss://`)**: Direct low-latency local WebSocket connection to your Music Assistant server (default port `8095`).
  - **WAN (`Remote ID`)**: Built-in WebRTC DataChannel support to connect to your Music Assistant server remotely using your Music Assistant Remote ID.
- **Built-In PC Player (`@music-assistant/sendspin`)**:
  - Turns your Xeneon Edge / PC into a native **Sendspin** playback target directly inside Music Assistant—no external companion app required.
  - Customizable player name (`Built-In PC Player Name`) so you can easily identify the PC across all Music Assistant interfaces.
  - Tuned specifically for iCUE's `QtWebEngine` (`file://`) with deterministic X25519 client identity, zero-CPU local PCM streaming, and jitter-free audio scheduling.
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

## Installation (Corsair iCUE / Xeneon Edge)

1. Download **[`ma-roon.icuewidget`](./ma-roon.icuewidget)** from this repository.
2. Open **Corsair iCUE** $\rightarrow$ select your **Xeneon Edge** screen $\rightarrow$ import `ma-roon.icuewidget` (or copy the [`MA-Roon`](./MA-Roon) folder contents to `%APPDATA%\Corsair\CUE5\html_widgets\com\musicassistant\xeneonplayer`).
3. In the widget's **Connection Settings** inside iCUE:
   - Enter either your **LAN - Server URL** (e.g., `http://192.168.1.2:8095`) or **WAN - Remote ID**.
   - Paste a **Long-Lived Access Token** generated from **Music Assistant $\rightarrow$ Profile $\rightarrow$ Long-lived access tokens**.
   - *(Optional)* Enable **Built-In PC Player (Sendspin)** and give your PC speaker a custom name.

---

## Repository Structure

```text
music-assistant-widgets/
├── MA-Roon/                    # Source code for the MA-Roon widget
│   ├── index.html              # Main application, UI, Sendspin, MilkDrop & Lyrics engine
│   ├── manifest.json           # Corsair iCUE widget manifest
│   ├── translation.json        # iCUE localization strings
│   ├── styles/                 # Base widget stylesheet
│   ├── scripts/                # Local bundled Butterchurn & Sendspin libraries
│   └── resources/              # Icons and standby media assets
├── ma-roon.icuewidget          # Packaged iCUE widget archive (built via `icuewidget package MA-Roon`)
├── assets/                     # Reference media & showcase assets
└── .agents/                    # Antigravity development skills & architectural guardrails
```
