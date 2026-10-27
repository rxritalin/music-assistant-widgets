#!/usr/bin/env python3
"""
Music Assistant Widgets — Dual-Mode Home Assistant Add-on & Standalone Docker Server.
100% Python Standard Library (zero external pip dependencies required).

Features:
- Auto-discovers all iCUE widgets (e.g. MA-Roon, MA-*) in the repository root.
- Parses <meta name="x-icue-property"> and <script id="x-icue-groups"> directly from each
  widget's index.html so every widget's Settings UI is generated automatically from a
  single source of truth.
- Injects a lightweight platform bridge (__ma_platform/bridge.js) when serving a widget over
  HTTP so window.<property> and icueEvents work seamlessly in any browser, wall tablet, or
  Home Assistant Ingress iframe without modifying the widget's iCUE source files.
- Supports Global Connection Defaults (enter Music Assistant URL & Token once) + Per-Screen
  Profiles (e.g., ?profile=office, ?profile=kitchen) so different displays can run different
  widgets or speaker targets from a single container.
- Integrates with Home Assistant Supervisor/Core WebSocket API to pin/unpin individual
  widgets or screen profiles directly to the Home Assistant Sidebar.
"""

import base64
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import ssl
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ma_widgets_server")

ROOT_DIR = Path(os.environ.get("MA_WIDGETS_ROOT", Path(__file__).resolve().parent.parent))
DATA_DIR = Path(os.environ.get("MA_WIDGETS_DATA", ROOT_DIR / ".data"))
UPLOADS_DIR = DATA_DIR / "uploads"
CONFIG_FILE = DATA_DIR / "ma_widgets_config.json"
HA_OPTIONS_FILE = Path("/data/options.json")
PORT = int(os.environ.get("PORT", "8096"))

META_TAG_RE = re.compile(r"<meta\s+[^>]*name=[\"']x-icue-property[\"'][^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"([a-zA-Z0-9_-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
GROUPS_SCRIPT_RE = re.compile(
    r"<script[^>]*id=[\"']x-icue-groups[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
TR_WRAP_RE = re.compile(r"^tr\(\s*['\"](.*)['\"]\s*\)$", re.DOTALL)


def strip_tr(val: str) -> str:
    """Unwrap iCUE tr('...') localization wrappers and surrounding backticks/quotes."""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    m = TR_WRAP_RE.match(s)
    if m:
        s = m.group(1)
    if len(s) >= 2 and (
        (s[0] == "'" and s[-1] == "'")
        or (s[0] == '"' and s[-1] == '"')
        or (s[0] == "`" and s[-1] == "`")
    ):
        s = s[1:-1]
    return s.replace("\\n", "\n")


def parse_default_literal(raw_default: Optional[str], prop_type: str) -> Any:
    """Convert iCUE data-default literal string into native Python value."""
    if raw_default is None:
        if prop_type == "switch":
            return False
        if prop_type == "slider":
            return 0
        return ""
    s = strip_tr(raw_default.strip())
    if prop_type == "switch":
        return s.lower() == "true"
    if prop_type == "slider":
        try:
            return float(s) if "." in s else int(s)
        except ValueError:
            return 0
    return s


def parse_widget_schema(widget_dir: Path) -> Dict[str, Any]:
    """Parse manifest.json, translation.json, and x-icue-property tags from index.html."""
    manifest_path = widget_dir / "manifest.json"
    index_path = widget_dir / "index.html"
    translation_path = widget_dir / "translation.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    html_text = index_path.read_text(encoding="utf-8")

    translations: Dict[str, str] = {}
    if translation_path.exists():
        try:
            tr_data = json.loads(translation_path.read_text(encoding="utf-8"))
            translations = tr_data.get("en", {}).get("translation", {})
        except Exception as exc:
            logger.warning("Failed to parse translation.json in %s: %s", widget_dir.name, exc)

    def translate(label: str) -> str:
        clean = strip_tr(label)
        return translations.get(clean, clean)

    properties: Dict[str, Dict[str, Any]] = {}
    prop_order: List[str] = []

    for match in META_TAG_RE.finditer(html_text):
        tag = match.group(0)
        attrs: Dict[str, str] = {}
        for attr_match in ATTR_RE.finditer(tag):
            key = attr_match.group(1).lower()
            val = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
            attrs[key] = html.unescape(val or "")

        prop_name = attrs.get("content", "").strip()
        if not prop_name:
            continue

        prop_type = attrs.get("data-type", "textfield").strip().lower()
        label = translate(attrs.get("data-label", prop_name))
        default_val = parse_default_literal(attrs.get("data-default"), prop_type)

        prop_info: Dict[str, Any] = {
            "name": prop_name,
            "label": label,
            "type": prop_type,
            "default": default_val,
        }
        if prop_type == "slider":
            try:
                prop_info["min"] = float(attrs.get("data-min", "0"))
                prop_info["max"] = float(attrs.get("data-max", "100"))
                prop_info["step"] = float(attrs.get("data-step", "1"))
            except ValueError:
                prop_info["min"], prop_info["max"], prop_info["step"] = 0, 100, 1
            prop_info["unit"] = strip_tr(attrs.get("data-unit-label", ""))
        elif prop_type in ("combobox", "tab-buttons"):
            raw_values = attrs.get("data-values", "")
            prop_info["options"] = [
                strip_tr(x)
                for pair in re.findall(r"'([^']*)'|\"([^\"]*)\"", raw_values)
                for x in pair
                if x
            ]

        properties[prop_name] = prop_info
        prop_order.append(prop_name)

    groups: List[Dict[str, Any]] = []
    grouped_props = set()
    groups_match = GROUPS_SCRIPT_RE.search(html_text)
    if groups_match:
        try:
            raw_groups = json.loads(groups_match.group(1))
            if isinstance(raw_groups, list):
                for grp in raw_groups:
                    grp_props = [p for p in grp.get("properties", []) if p in properties]
                    grouped_props.update(grp_props)
                    groups.append(
                        {
                            "title": translate(grp.get("title", "Settings")),
                            "description": strip_tr(grp.get("description", "")),
                            "properties": grp_props,
                        }
                    )
        except Exception as exc:
            logger.warning("Failed to parse x-icue-groups in %s: %s", widget_dir.name, exc)

    ungrouped = [p for p in prop_order if p not in grouped_props]
    if ungrouped:
        groups.append(
            {
                "title": "Other Settings",
                "description": "",
                "properties": ungrouped,
            }
        )

    return {
        "folder": widget_dir.name,
        "id": manifest.get("id", widget_dir.name),
        "name": manifest.get("name", widget_dir.name),
        "author": manifest.get("author", "rxritalin"),
        "version": manifest.get("version", "1.0.0"),
        "description": manifest.get("description", ""),
        "preview_icon": f"/{widget_dir.name}/{manifest.get('preview_icon', 'resources/icon.svg').lstrip('/')}",
        "properties": properties,
        "groups": groups,
    }


def discover_widgets() -> Dict[str, Dict[str, Any]]:
    """Scan ROOT_DIR for valid iCUE widget folders containing manifest.json + index.html."""
    widgets: Dict[str, Dict[str, Any]] = {}
    if not ROOT_DIR.exists():
        return widgets

    for item in sorted(ROOT_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir() or item.name.startswith(".") or item.name in ("server", "assets", "ma-widgets-addon", "node_modules"):
            continue
        if (item / "manifest.json").is_file() and (item / "index.html").is_file():
            try:
                schema = parse_widget_schema(item)
                widgets[item.name] = schema
            except Exception as exc:
                logger.error("Error loading widget %s: %s", item.name, exc)
    return widgets


class ConfigStore:
    """Manages persistent global settings, per-screen widget profiles, and HA sidebar pins."""

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, Any] = {
            "schemaVersion": 2,
            "global": {
                "localServerUrl": "",
                "remoteId": "",
                "accessToken": "",
                "targetSpeaker": "",
                "defaultIngressTarget": "launcher",
                "externalBaseUrl": "",
            },
            "profiles": {},
            "haPins": {},
        }
        self.load()
        self.sync_ha_addon_options()

    def load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data["schemaVersion"] = loaded.get("schemaVersion", 1)
                    if isinstance(loaded.get("global"), dict):
                        self.data["global"].update(loaded["global"])
                    if isinstance(loaded.get("profiles"), dict):
                        self.data["profiles"] = loaded["profiles"]
                    if isinstance(loaded.get("haPins"), dict):
                        self.data["haPins"] = loaded["haPins"]
            except Exception as exc:
                logger.warning("Failed to load %s: %s", CONFIG_FILE, exc)

    def save(self) -> None:
        try:
            CONFIG_FILE.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to save %s: %s", CONFIG_FILE, exc)

    def sync_ha_addon_options(self) -> None:
        """If running inside Home Assistant Supervisor, seed empty global defaults from /data/options.json."""
        if not HA_OPTIONS_FILE.exists():
            return
        try:
            opts = json.loads(HA_OPTIONS_FILE.read_text(encoding="utf-8"))
            changed = False
            mapping = {
                "local_server_url": "localServerUrl",
                "remote_id": "remoteId",
                "access_token": "accessToken",
                "default_speaker": "targetSpeaker",
                "default_ingress_target": "defaultIngressTarget",
                "external_base_url": "externalBaseUrl",
            }
            for ha_key, global_key in mapping.items():
                val = str(opts.get(ha_key, "") or "").strip()
                if val and not self.data["global"].get(global_key):
                    self.data["global"][global_key] = val
                    changed = True
            if changed:
                self.save()
        except Exception as exc:
            logger.warning("Failed to read HA options.json: %s", exc)

    def ensure_widget_profiles(self, widgets: Dict[str, Dict[str, Any]]) -> None:
        changed = False
        needs_v2_migration = self.data.get("schemaVersion", 1) < 2
        for folder, schema in widgets.items():
            if folder not in self.data["profiles"] or not isinstance(self.data["profiles"][folder], dict):
                self.data["profiles"][folder] = {}
                changed = True
            if "default" not in self.data["profiles"][folder]:
                defaults = {k: v["default"] for k, v in schema["properties"].items()}
                self.data["profiles"][folder]["default"] = {
                    "id": "default",
                    "name": "Default Profile",
                    "values": defaults,
                }
                changed = True
            if needs_v2_migration:
                for prof_obj in self.data["profiles"][folder].values():
                    vals = prof_obj.get("values")
                    if isinstance(vals, dict):
                        if vals.get("bgBrightness") in (None, 0, 100):
                            vals["bgBrightness"] = schema["properties"].get("bgBrightness", {}).get("default", 80)
                            changed = True
                        if vals.get("transparency") in (None, 0, 100):
                            vals["transparency"] = schema["properties"].get("transparency", {}).get("default", 40)
                            changed = True
        if needs_v2_migration:
            self.data["schemaVersion"] = 2
            changed = True
        if changed:
            self.save()

    def get_effective_values(self, widget_schema: Dict[str, Any], profile_id: str) -> Dict[str, Any]:
        folder = widget_schema["folder"]
        self.ensure_widget_profiles({folder: widget_schema})
        widget_profiles = self.data["profiles"].get(folder, {})
        prof = widget_profiles.get(profile_id) or widget_profiles.get("default") or {}
        saved_vals = prof.get("values", {})

        effective: Dict[str, Any] = {}
        for prop_name, prop_meta in widget_schema["properties"].items():
            val = saved_vals.get(prop_name, prop_meta["default"])
            if prop_name == "bgBrightness" and (val is None or val == "" or val == 0):
                val = prop_meta["default"]
            if prop_name == "transparency" and (val is None or val == ""):
                val = prop_meta["default"]
            if prop_name in ("localServerUrl", "remoteId", "accessToken", "targetSpeaker") and not val:
                global_val = self.data["global"].get(prop_name, "")
                if global_val:
                    val = global_val
            effective[prop_name] = val
        return effective


STORE = ConfigStore()


# =====================================================================
# Minimal RFC 6455 WebSocket Client for Home Assistant Supervisor API
# =====================================================================
def _ws_send_json(sock: socket.socket, obj: Dict[str, Any]) -> None:
    payload = json.dumps(obj).encode("utf-8")
    mask_key = os.urandom(4)
    header = bytearray([0x81])  # FIN + text frame
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask_key)
    masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + masked)


def _ws_recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("WebSocket closed unexpectedly")
        buf.extend(chunk)
    return bytes(buf)


def _ws_recv_json(sock: socket.socket) -> Dict[str, Any]:
    while True:
        b1, b2 = _ws_recv_exact(sock, 2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", _ws_recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _ws_recv_exact(sock, 8))[0]
        mask_key = _ws_recv_exact(sock, 4) if masked else b""
        data = _ws_recv_exact(sock, length)
        if masked:
            data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:
            raise ConnectionError("WebSocket closed by server")
        if opcode == 0x9:  # Ping -> Pong
            continue
        if opcode == 0x1:
            return json.loads(data.decode("utf-8"))


def ha_ws_call_sync(commands: List[Dict[str, Any]]) -> List[Any]:
    """Execute commands against Home Assistant Core WebSocket API using SUPERVISOR_TOKEN."""
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or os.environ.get("HA_TOKEN")
    ws_url = os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket")
    if not token:
        raise RuntimeError(
            "Home Assistant Supervisor token not detected. When running inside Home Assistant OS/Supervised, "
            "this works automatically. For standalone Docker, you can either set HA_WS_URL and HA_TOKEN "
            "environment variables, or add the URL in Home Assistant under Settings -> Dashboards -> Add Dashboard -> Webpage."
        )

    parsed = urlparse(ws_url)
    use_ssl = parsed.scheme == "wss"
    host = parsed.hostname or "supervisor"
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/core/websocket"

    raw_sock = socket.create_connection((host, port), timeout=10)
    sock: socket.socket = (
        ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host) if use_ssl else raw_sock
    )
    try:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode("ascii"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(1024)
            if not chunk:
                raise RuntimeError("Failed WebSocket handshake with Home Assistant")
            resp += chunk

        greeting = _ws_recv_json(sock)
        if greeting.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected HA WS greeting: {greeting}")
        _ws_send_json(sock, {"type": "auth", "access_token": token})
        auth_res = _ws_recv_json(sock)
        if auth_res.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant WS auth failed: {auth_res}")

        results: List[Any] = []
        msg_id = 1
        for cmd in commands:
            payload = dict(cmd)
            payload["id"] = msg_id
            _ws_send_json(sock, payload)
            while True:
                msg = _ws_recv_json(sock)
                if msg.get("id") == msg_id:
                    if not msg.get("success", False):
                        raise RuntimeError(msg.get("error", {}).get("message", "HA command failed"))
                    results.append(msg.get("result"))
                    break
            msg_id += 1
        return results
    finally:
        try:
            sock.close()
        except Exception:
            pass


# =====================================================================
# Client-Side Platform Bridge (/__ma_platform/bridge.js)
# =====================================================================
BRIDGE_JS_TEMPLATE = r"""
(function() {
  if (window.__maPlatformBridgeLoaded) return;
  window.__maPlatformBridgeLoaded = true;

  const INITIAL_STATE = __INITIAL_STATE_JSON__;
  const widgetFolder = INITIAL_STATE.widget.folder;
  let activeProfileId = INITIAL_STATE.profileId || 'default';
  let widgetSchema = INITIAL_STATE.widget;
  let profilesMap = INITIAL_STATE.profiles || {};
  let globalConfig = INITIAL_STATE.global || {};
  let effectiveValues = Object.assign({}, INITIAL_STATE.effectiveValues || {});

  // 1. Seed window.<propertyName> BEFORE widget body scripts execute
  function applyValuesToWindow(values) {
    Object.keys(values).forEach(function(k) {
      window[k] = values[k];
    });
  }
  applyValuesToWindow(effectiveValues);

  // Polyfill iCUE's MediaViewer for standard browsers (supports both images and .mp4/.webm videos)
  if (typeof window.MediaViewer === 'undefined') {
    window.MediaViewer = function(opts) {
      this.container = opts && opts.container;
      this.onMediaLoaded = opts && opts.onMediaLoaded;
      this.onMediaError = opts && opts.onMediaError;
    };
    window.MediaViewer.prototype.clear = function() {
      if (!this.container) return;
      this.container.innerHTML = '';
      this.container.style.backgroundImage = '';
    };
    window.MediaViewer.prototype.loadMedia = function(config) {
      if (!this.container) return;
      const rawPath = (config && typeof config === 'object') ? (config.path || config.pathToAsset || '') : String(config || '');
      if (!rawPath) {
        this.clear();
        if (this.onMediaError) this.onMediaError(new Error('Empty media path'));
        return;
      }
      this.clear();
      this.container.style.visibility = 'visible';
      const isVideo = /\.(mp4|webm|ogg)(\?.*)?$/i.test(rawPath);
      if (isVideo) {
        const vid = document.createElement('video');
        vid.src = rawPath;
        vid.autoplay = true;
        vid.loop = true;
        vid.muted = true;
        vid.playsInline = true;
        vid.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
        vid.onloadeddata = () => { if (this.onMediaLoaded) this.onMediaLoaded(); };
        vid.onerror = (e) => { if (this.onMediaError) this.onMediaError(e); };
        this.container.appendChild(vid);
      } else {
        const img = document.createElement('img');
        img.src = rawPath;
        img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
        img.onload = () => { if (this.onMediaLoaded) this.onMediaLoaded(); };
        img.onerror = (e) => { if (this.onMediaError) this.onMediaError(e); };
        this.container.appendChild(img);
      }
    };
  }

  // Clean up <title>tr('...')</title> for standard web browsers
  function cleanDocumentTitle() {
    if (document.title && document.title.indexOf("tr(") !== -1) {
      const m = document.title.match(/tr\(\s*['"](.*)['"]\s*\)/);
      if (m && m[1]) {
        const profileSuffix = activeProfileId !== 'default' && profilesMap[activeProfileId]
          ? ' — ' + profilesMap[activeProfileId].name
          : '';
        document.title = m[1] + profileSuffix;
      }
    }
  }
  cleanDocumentTitle();
  document.addEventListener('DOMContentLoaded', cleanDocumentTitle);

  function notifyWidgetUpdated() {
    try {
      if (typeof window.icueEvents === 'object' && window.icueEvents) {
        if (typeof window.icueEvents.onDataUpdated === 'function') {
          window.icueEvents.onDataUpdated();
        }
      } else if (typeof window.onIcueDataUpdated === 'function') {
        window.onIcueDataUpdated();
      }
    } catch (err) {
      console.warn('[MA Platform Bridge] Error notifying widget:', err);
    }
  }

  function getPlatformBasePath() {
    const path = window.location.pathname;
    const idx = path.indexOf('/' + widgetFolder);
    if (idx !== -1) {
      return path.slice(0, idx) || '';
    }
    return '';
  }

  async function apiCall(endpoint, method, body) {
    const base = getPlatformBasePath();
    const res = await fetch(base + '/__ma_platform/api/' + endpoint, {
      method: method || 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || ('HTTP ' + res.status));
    }
    return data;
  }

  function mountPlatformUI() {
    if (document.getElementById('ma-platform-root')) return;

    const style = document.createElement('style');
    style.textContent = `
      :root {
        --layout-unit: min(1vmin, 0.42vw) !important;
      }
      html, body {
        background-color: var(--bg-color, #12091c) !important;
      }
      #ma-platform-root {
        position: fixed;
        z-index: 9000;
        bottom: 14px;
        right: 14px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        color: #f8fafc;
        user-select: none;
        pointer-events: none;
      }
      .ma-plat-pill {
        pointer-events: auto;
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 12px;
        border-radius: 999px;
        background: rgba(15, 10, 25, 0.78);
        border: 1px solid rgba(255, 255, 255, 0.16);
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
        cursor: pointer;
        transition: opacity 0.35s ease, transform 0.25s ease, background 0.2s ease;
        opacity: 0.88;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: #f8fafc;
      }
      .ma-plat-pill.is-idle {
        opacity: 0.16;
      }
      .ma-plat-pill.is-drawer-open {
        opacity: 0 !important;
        pointer-events: none !important;
        transform: translateY(6px);
      }
      .ma-plat-pill:hover, .ma-plat-pill:focus-within {
        opacity: 1 !important;
        background: rgba(24, 16, 40, 0.92);
        transform: translateY(-1px);
      }
      .ma-plat-pill svg {
        width: 15px;
        height: 15px;
        fill: currentColor;
      }
      .ma-plat-badge {
        padding: 2px 7px;
        border-radius: 999px;
        background: rgba(192, 132, 252, 0.22);
        color: #e9d5ff;
        font-size: 11px;
      }
      .ma-plat-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(6, 4, 12, 0.62);
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.25s ease;
        z-index: 99998;
      }
      .ma-plat-backdrop.is-open {
        opacity: 1;
        pointer-events: auto;
      }
      .ma-plat-drawer {
        position: fixed;
        top: 0;
        right: 0;
        bottom: 0;
        width: min(460px, 92vw);
        background: rgba(16, 12, 26, 0.97);
        border-left: 1px solid rgba(255, 255, 255, 0.14);
        box-shadow: -16px 0 48px rgba(0, 0, 0, 0.65);
        transform: translateX(104%);
        transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
        z-index: 99999;
        display: flex;
        flex-direction: column;
        pointer-events: auto;
        color: #f1f5f9;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .ma-plat-drawer.is-open {
        transform: translateX(0);
      }
      .ma-plat-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 20px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      }
      .ma-plat-title {
        font-size: 16px;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .ma-plat-actions {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .ma-plat-btn {
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.14);
        color: #f8fafc;
        padding: 6px 12px;
        border-radius: 8px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        text-decoration: none;
        display: inline-flex;
        align-items: center;
        gap: 5px;
      }
      .ma-plat-btn:hover {
        background: rgba(255, 255, 255, 0.15);
      }
      .ma-plat-btn.is-primary {
        background: #9333ea;
        border-color: #a855f7;
      }
      .ma-plat-btn.is-primary:hover {
        background: #a855f7;
      }
      .ma-plat-body {
        flex: 1 1 auto;
        overflow-y: auto;
        padding: 16px 20px 28px;
        display: flex;
        flex-direction: column;
        gap: 18px;
      }
      .ma-plat-group {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 14px 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .ma-plat-group-title {
        font-size: 13px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #c084fc;
      }
      .ma-plat-group-desc {
        font-size: 11.5px;
        line-height: 1.45;
        color: rgba(241, 245, 249, 0.65);
        white-space: pre-line;
        background: rgba(0, 0, 0, 0.25);
        padding: 8px 10px;
        border-radius: 8px;
      }
      .ma-plat-field {
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .ma-plat-field.is-row {
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
      }
      .ma-plat-label {
        font-size: 13px;
        font-weight: 500;
        color: #e2e8f0;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .ma-plat-input, .ma-plat-select {
        width: 100%;
        box-sizing: border-box;
        background: rgba(0, 0, 0, 0.35);
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 8px;
        padding: 8px 10px;
        color: #fff;
        font-size: 13px;
      }
      .ma-plat-color-row {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .ma-plat-color {
        width: 36px;
        height: 32px;
        border: none;
        border-radius: 6px;
        background: transparent;
        cursor: pointer;
      }
      .ma-plat-slider {
        width: 100%;
        accent-color: #c084fc;
      }
      .ma-plat-switch {
        width: 40px;
        height: 22px;
        accent-color: #c084fc;
        cursor: pointer;
      }
      .ma-plat-media-row {
        display: flex;
        gap: 6px;
      }
      .ma-plat-status {
        font-size: 12px;
        color: #4ade80;
        min-height: 16px;
      }
    `;
    document.head.appendChild(style);

    const root = document.createElement('div');
    root.id = 'ma-platform-root';

    const pill = document.createElement('button');
    pill.className = 'ma-plat-pill';
    pill.title = 'Open Widget & Profile Settings';
    pill.innerHTML = `
      <svg viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
      <span>${widgetSchema.name}</span>
      <span class="ma-plat-badge" id="ma-plat-pill-profile">${(profilesMap[activeProfileId] && profilesMap[activeProfileId].name) || activeProfileId}</span>
    `;

    const backdrop = document.createElement('div');
    backdrop.className = 'ma-plat-backdrop';

    const drawer = document.createElement('div');
    drawer.className = 'ma-plat-drawer';

    const base = getPlatformBasePath();
    const launcherHref = (base || '') + '/?launcher=1';

    drawer.innerHTML = `
      <div class="ma-plat-header">
        <div class="ma-plat-title">
          <span>${widgetSchema.name} Settings</span>
        </div>
        <div class="ma-plat-actions">
          <button class="ma-plat-btn" id="ma-plat-fullscreen" title="Toggle Fullscreen Mode">⛶ Fullscreen</button>
          <button class="ma-plat-btn" id="ma-plat-install-pwa" title="Install as Standalone Web App">Install App</button>
          <a class="ma-plat-btn" href="${launcherHref}" title="Open All Widgets & HA Sidebar Launcher">All Widgets</a>
          <button class="ma-plat-btn" id="ma-plat-close">Close</button>
        </div>
      </div>
      <div class="ma-plat-body" id="ma-plat-body"></div>
    `;

    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);
    root.appendChild(pill);
    document.body.appendChild(root);

    let deferredInstallPrompt = null;
    window.addEventListener('beforeinstallprompt', function(e) {
      e.preventDefault();
      deferredInstallPrompt = e;
    });

    if ('serviceWorker' in navigator && (window.location.protocol === 'https:' || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')) {
      navigator.serviceWorker.register('./__ma_platform/sw.js').catch(function() {});
    }

    drawer.querySelector('#ma-plat-fullscreen').addEventListener('click', function() {
      if (!document.fullscreenElement) {
        if (document.documentElement.requestFullscreen) {
          document.documentElement.requestFullscreen().catch(function() {});
        }
      } else if (document.exitFullscreen) {
        document.exitFullscreen().catch(function() {});
      }
    });

    drawer.querySelector('#ma-plat-install-pwa').addEventListener('click', async function() {
      if (deferredInstallPrompt) {
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        return;
      }
      window.alert(
        'To install this widget as a full-screen standalone Web App:\n\n' +
        '• Android (Firefox / Chrome): Open the browser menu (⋮) and tap "Install" or "Add app to Home screen".\n' +
        '• iOS / iPadOS (Safari): Tap the Share button and select "Add to Home Screen".\n\n' +
        'Note: On modern Android, if Chrome blocks unencrypted LAN ports (8096), open this URL in Firefox for Android and tap ⋮ → "Add app to Home screen".'
      );
    });

    let idleTimer = null;
    function wakePill() {
      pill.classList.remove('is-idle');
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(function() {
        if (!drawer.classList.contains('is-open')) {
          pill.classList.add('is-idle');
        }
      }, 4500);
    }
    window.addEventListener('pointermove', wakePill, { passive: true });
    window.addEventListener('touchstart', wakePill, { passive: true });
    wakePill();

    function openDrawer() {
      renderDrawerBody();
      backdrop.classList.add('is-open');
      drawer.classList.add('is-open');
      pill.classList.remove('is-idle');
      pill.classList.add('is-drawer-open');
    }

    function closeDrawer() {
      backdrop.classList.remove('is-open');
      drawer.classList.remove('is-open');
      pill.classList.remove('is-drawer-open');
      wakePill();
    }

    pill.addEventListener('click', openDrawer);
    backdrop.addEventListener('click', closeDrawer);
    drawer.querySelector('#ma-plat-close').addEventListener('click', closeDrawer);

    let saveDebounce = null;
    function queueProfileSave() {
      if (saveDebounce) clearTimeout(saveDebounce);
      saveDebounce = setTimeout(async function() {
        try {
          const profName = (profilesMap[activeProfileId] && profilesMap[activeProfileId].name) || activeProfileId;
          await apiCall('profile', 'POST', {
            widget: widgetFolder,
            profileId: activeProfileId,
            name: profName,
            values: effectiveValues
          });
          const st = document.getElementById('ma-plat-save-status');
          if (st) {
            st.textContent = '✓ Saved live to profile "' + profName + '"';
            setTimeout(function() { if (st) st.textContent = ''; }, 2200);
          }
        } catch (err) {
          console.error('Failed saving profile:', err);
        }
      }, 280);
    }

    window.__maPlatformUpdateProperty = function(propName, val) {
      effectiveValues[propName] = val;
      window[propName] = val;
      queueProfileSave();
    };

    function renderDrawerBody() {
      const body = drawer.querySelector('#ma-plat-body');
      body.innerHTML = '';

      const profGroup = document.createElement('div');
      profGroup.className = 'ma-plat-group';
      const profileOptions = Object.keys(profilesMap).map(function(pid) {
        const p = profilesMap[pid];
        const sel = pid === activeProfileId ? 'selected' : '';
        return '<option value="' + pid + '" ' + sel + '>' + (p.name || pid) + ' (?profile=' + pid + ')</option>';
      }).join('');

      profGroup.innerHTML = `
        <div class="ma-plat-group-title">Screen Profile</div>
        <div class="ma-plat-field">
          <label class="ma-plat-label">Active Profile for This Screen</label>
          <div style="display:flex;gap:8px;">
            <select class="ma-plat-select" id="ma-plat-prof-select">${profileOptions}</select>
            <button class="ma-plat-btn is-primary" id="ma-plat-new-prof">+ New</button>
          </div>
        </div>
        <div class="ma-plat-status" id="ma-plat-save-status"></div>
      `;
      body.appendChild(profGroup);

      profGroup.querySelector('#ma-plat-prof-select').addEventListener('change', function(e) {
        const nextPid = e.target.value;
        const url = new URL(window.location.href);
        if (nextPid === 'default') url.searchParams.delete('profile');
        else url.searchParams.set('profile', nextPid);
        window.location.href = url.toString();
      });

      profGroup.querySelector('#ma-plat-new-prof').addEventListener('click', async function() {
        const name = window.prompt('Enter a name for the new screen profile (e.g. Office, Kitchen Tablet, Bedroom):');
        if (!name || !name.trim()) return;
        const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || ('screen-' + Date.now());
        await apiCall('profile', 'POST', {
          widget: widgetFolder,
          profileId: slug,
          name: name.trim(),
          values: effectiveValues
        });
        const url = new URL(window.location.href);
        url.searchParams.set('profile', slug);
        window.location.href = url.toString();
      });

      (widgetSchema.groups || []).forEach(function(grp) {
        const gEl = document.createElement('div');
        gEl.className = 'ma-plat-group';

        const titleEl = document.createElement('div');
        titleEl.className = 'ma-plat-group-title';
        titleEl.textContent = grp.title;
        gEl.appendChild(titleEl);

        if (grp.description) {
          const descEl = document.createElement('div');
          descEl.className = 'ma-plat-group-desc';
          descEl.textContent = grp.description;
          gEl.appendChild(descEl);
        }

        (grp.properties || []).forEach(function(propName) {
          const meta = widgetSchema.properties[propName];
          if (!meta) return;

          const fEl = document.createElement('div');
          fEl.className = 'ma-plat-field' + (meta.type === 'switch' ? ' is-row' : '');

          const curVal = effectiveValues[propName] !== undefined ? effectiveValues[propName] : meta.default;

          if (meta.type === 'switch') {
            fEl.innerHTML = `
              <label class="ma-plat-label">${meta.label}</label>
              <input type="checkbox" class="ma-plat-switch" ${curVal ? 'checked' : ''} />
            `;
            fEl.querySelector('input').addEventListener('change', function(e) {
              effectiveValues[propName] = !!e.target.checked;
              window[propName] = effectiveValues[propName];
              notifyWidgetUpdated();
              queueProfileSave();
            });
          } else if (meta.type === 'color') {
            fEl.innerHTML = `
              <label class="ma-plat-label">${meta.label}</label>
              <div class="ma-plat-color-row">
                <input type="color" class="ma-plat-color" value="${curVal || '#ffffff'}" />
                <input type="text" class="ma-plat-input" value="${curVal || '#ffffff'}" />
              </div>
            `;
            const cIn = fEl.querySelector('input[type="color"]');
            const tIn = fEl.querySelector('input[type="text"]');
            function onColor(val) {
              effectiveValues[propName] = val;
              window[propName] = val;
              cIn.value = val;
              tIn.value = val;
              notifyWidgetUpdated();
              queueProfileSave();
            }
            cIn.addEventListener('input', function(e) { onColor(e.target.value); });
            tIn.addEventListener('change', function(e) { onColor(e.target.value.trim()); });
          } else if (meta.type === 'slider') {
            const unit = meta.unit || '';
            fEl.innerHTML = `
              <label class="ma-plat-label">
                <span>${meta.label}</span>
                <span class="ma-plat-val">${curVal}${unit}</span>
              </label>
              <input type="range" class="ma-plat-slider" min="${meta.min}" max="${meta.max}" step="${meta.step}" value="${curVal}" />
            `;
            const sIn = fEl.querySelector('input[type="range"]');
            const vSpan = fEl.querySelector('.ma-plat-val');
            sIn.addEventListener('input', function(e) {
              const num = Number(e.target.value);
              vSpan.textContent = num + unit;
              effectiveValues[propName] = num;
              window[propName] = num;
              notifyWidgetUpdated();
              queueProfileSave();
            });
          } else if (meta.type === 'media-selector') {
            const pathVal = typeof curVal === 'object' && curVal ? (curVal.path || '') : (curVal || '');
            fEl.innerHTML = `
              <label class="ma-plat-label">${meta.label}</label>
              <div class="ma-plat-media-row">
                <input type="text" class="ma-plat-input" placeholder="Image/Video URL or uploaded path" value="${pathVal}" />
                <label class="ma-plat-btn" style="cursor:pointer;">
                  Upload
                  <input type="file" accept="image/*,video/*" style="display:none;" />
                </label>
              </div>
            `;
            const txtIn = fEl.querySelector('input[type="text"]');
            const fileIn = fEl.querySelector('input[type="file"]');
            txtIn.addEventListener('change', function(e) {
              effectiveValues[propName] = e.target.value.trim();
              window[propName] = effectiveValues[propName];
              notifyWidgetUpdated();
              queueProfileSave();
            });
            fileIn.addEventListener('change', async function(e) {
              const file = e.target.files && e.target.files[0];
              if (!file) return;
              const base = getPlatformBasePath();
              const res = await fetch(base + '/__ma_platform/api/upload?filename=' + encodeURIComponent(file.name), {
                method: 'POST',
                body: file
              });
              const data = await res.json();
              if (data.url) {
                txtIn.value = data.url;
                effectiveValues[propName] = data.url;
                window[propName] = data.url;
                notifyWidgetUpdated();
                queueProfileSave();
              }
            });
          } else {
            const isSecret = propName.toLowerCase().indexOf('token') !== -1;
            fEl.innerHTML = `
              <label class="ma-plat-label">${meta.label}</label>
              <input type="${isSecret ? 'password' : 'text'}" class="ma-plat-input" value="${curVal !== undefined ? curVal : ''}" placeholder="${meta.default || ''}" />
            `;
            const txtIn = fEl.querySelector('input');
            txtIn.addEventListener('focus', function() { if (isSecret) txtIn.type = 'text'; });
            txtIn.addEventListener('blur', function() { if (isSecret) txtIn.type = 'password'; });
            txtIn.addEventListener('change', function(e) {
              effectiveValues[propName] = e.target.value;
              window[propName] = effectiveValues[propName];
              notifyWidgetUpdated();
              queueProfileSave();
            });
          }

          gEl.appendChild(fEl);
        });

        body.appendChild(gEl);
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountPlatformUI);
  } else {
    mountPlatformUI();
  }
})();
"""


# =====================================================================
# Launcher & Profile Manager UI (GET /)
# =====================================================================
LAUNCHER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Music Assistant Widgets — Launcher & Home Assistant Manager</title>
  <style>
    :root {
      --bg: #0b0813;
      --card: rgba(24, 18, 38, 0.85);
      --border: rgba(255, 255, 255, 0.11);
      --accent: #c084fc;
      --accent-strong: #9333ea;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: radial-gradient(circle at 20% 0%, #24123d 0%, var(--bg) 60%);
      color: var(--text);
      padding: 32px 24px 64px;
    }
    .container {
      max-width: 1120px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 28px;
    }
    .hero {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 24px 28px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      backdrop-filter: blur(16px);
    }
    .hero h1 {
      margin: 0 0 6px;
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .hero p {
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 20px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .card-header {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .card-icon {
      width: 48px;
      height: 48px;
      border-radius: 12px;
      background: rgba(192, 132, 252, 0.14);
      border: 1px solid rgba(192, 132, 252, 0.3);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 8px;
    }
    .card-icon img {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
    .card-title {
      font-size: 18px;
      font-weight: 700;
      margin: 0;
    }
    .card-sub {
      font-size: 12px;
      color: var(--muted);
    }
    .card-desc {
      font-size: 13.5px;
      line-height: 1.5;
      color: #cbd5e1;
      margin: 0;
    }
    .prof-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 4px;
    }
    .prof-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 10px;
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid rgba(255, 255, 255, 0.07);
    }
    .prof-name {
      font-size: 13px;
      font-weight: 600;
    }
    .prof-url {
      font-size: 11px;
      color: var(--muted);
      font-family: monospace;
    }
    .btn-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .btn {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.14);
      color: var(--text);
      padding: 7px 12px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .btn:hover { background: rgba(255, 255, 255, 0.15); }
    .btn.primary { background: var(--accent-strong); border-color: #a855f7; }
    .btn.primary:hover { background: #a855f7; }
    .btn.pin-active { background: rgba(74, 222, 128, 0.18); border-color: #4ade80; color: #86efac; }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .field label {
      font-size: 12px;
      font-weight: 600;
      color: #cbd5e1;
    }
    .field input, .field select {
      background: rgba(0, 0, 0, 0.35);
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 8px;
      padding: 9px 12px;
      color: #fff;
      font-size: 13px;
    }
    .toast {
      font-size: 13px;
      color: #4ade80;
      min-height: 18px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="hero">
      <div>
        <h1>Music Assistant Widgets Hub</h1>
        <p>Multi-widget server, per-screen profile manager, and Home Assistant Sidebar integration</p>
      </div>
      <div class="toast" id="status-toast"></div>
    </div>

    <div class="card">
      <h2 class="card-title">Shared Music Assistant Connection & Ingress Defaults</h2>
      <p class="card-desc">
        Configure your Music Assistant connection once here—every widget and screen profile inherits these defaults automatically unless overridden in a specific profile.
      </p>
      <div class="form-grid">
        <div class="field">
          <label>LAN - Music Assistant Server URL</label>
          <input type="text" id="g-localServerUrl" placeholder="http://192.168.1.2:8095" />
        </div>
        <div class="field">
          <label>WAN - Remote ID (Optional)</label>
          <input type="text" id="g-remoteId" placeholder="Music Assistant Remote ID" />
        </div>
        <div class="field">
          <label>Long-Lived Access Token</label>
          <input type="password" id="g-accessToken" placeholder="Paste MA Access Token" />
        </div>
        <div class="field">
          <label>Default Speaker (Optional)</label>
          <input type="text" id="g-targetSpeaker" placeholder="Speaker Name or Player ID" />
        </div>
        <div class="field">
          <label>Default Home Assistant Sidebar View</label>
          <select id="g-defaultIngressTarget"></select>
        </div>
        <div class="field">
          <label>Browser Base URL for Pinned HA Sidebar Panels</label>
          <input type="text" id="g-externalBaseUrl" placeholder="Auto-detected (e.g. http://192.168.1.2:8096)" />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn primary" id="save-global-btn">Save Shared Defaults</button>
      </div>
    </div>

    <div class="grid" id="widgets-grid"></div>
  </div>

  <script>
    let appState = null;

    function showToast(msg, isErr) {
      const el = document.getElementById('status-toast');
      el.style.color = isErr ? '#f87171' : '#4ade80';
      el.textContent = msg;
      setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 4000);
    }

    async function loadState() {
      const res = await fetch('./__ma_platform/api/state');
      appState = await res.json();
      render();
    }

    function render() {
      const g = appState.global || {};
      document.getElementById('g-localServerUrl').value = g.localServerUrl || '';
      document.getElementById('g-remoteId').value = g.remoteId || '';
      document.getElementById('g-accessToken').value = g.accessToken || '';
      document.getElementById('g-targetSpeaker').value = g.targetSpeaker || '';
      document.getElementById('g-externalBaseUrl').value = g.externalBaseUrl || '';

      const targetSelect = document.getElementById('g-defaultIngressTarget');
      targetSelect.innerHTML = '<option value="launcher">Show This Multi-Widget Launcher</option>';
      Object.values(appState.widgets).forEach(w => {
        const profs = appState.profiles[w.folder] || {};
        Object.values(profs).forEach(p => {
          const val = w.folder + ':' + p.id;
          const opt = document.createElement('option');
          opt.value = val;
          opt.textContent = w.name + ' — ' + p.name;
          if (g.defaultIngressTarget === val) opt.selected = true;
          targetSelect.appendChild(opt);
        });
      });

      const grid = document.getElementById('widgets-grid');
      grid.innerHTML = '';

      Object.values(appState.widgets).forEach(w => {
        const card = document.createElement('div');
        card.className = 'card';
        const profs = appState.profiles[w.folder] || {};

        let profRows = '';
        Object.values(profs).forEach(p => {
          const relUrl = './' + w.folder + '/' + (p.id === 'default' ? '' : ('?profile=' + encodeURIComponent(p.id)));
          const pinKey = ('ma-' + w.folder + '-' + p.id).toLowerCase().replace(/[^a-z0-9-]/g, '-');
          const isPinned = !!(appState.haPins && appState.haPins[pinKey]);

          profRows += `
            <div class="prof-item">
              <div>
                <div class="prof-name">${p.name}</div>
                <div class="prof-url">/${w.folder}/${p.id === 'default' ? '' : ('?profile=' + p.id)}</div>
              </div>
              <div class="btn-row">
                <a class="btn primary" href="${relUrl}">Launch</a>
                <button class="btn ${isPinned ? 'pin-active' : ''}" data-pin="${pinKey}" data-widget="${w.folder}" data-prof="${p.id}" data-name="${w.name} (${p.name})">
                  ${isPinned ? '★ Pinned to HA Sidebar' : 'Pin to HA Sidebar'}
                </button>
              </div>
            </div>
          `;
        });

        card.innerHTML = `
          <div class="card-header">
            <div class="card-icon"><img src=".${w.preview_icon}" alt="${w.name}" /></div>
            <div>
              <h3 class="card-title">${w.name}</h3>
              <div class="card-sub">v${w.version} • Folder: /${w.folder}</div>
            </div>
          </div>
          <p class="card-desc">${w.description}</p>
          <div class="prof-list">${profRows}</div>
          <div class="btn-row">
            <button class="btn" data-new-prof="${w.folder}">+ Add Screen Profile</button>
          </div>
        `;

        card.querySelector('[data-new-prof]').addEventListener('click', async () => {
          const name = prompt('Enter a name for the new screen profile (e.g. Office, Kitchen, Living Room):');
          if (!name || !name.trim()) return;
          const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || ('screen-' + Date.now());
          await fetch('./__ma_platform/api/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ widget: w.folder, profileId: slug, name: name.trim(), values: {} })
          });
          showToast('Created profile "' + name.trim() + '"');
          loadState();
        });

        card.querySelectorAll('[data-pin]').forEach(btn => {
          btn.addEventListener('click', async () => {
            const urlPath = btn.getAttribute('data-pin');
            const widget = btn.getAttribute('data-widget');
            const profileId = btn.getAttribute('data-prof');
            const title = btn.getAttribute('data-name');
            const baseUrl = (document.getElementById('g-externalBaseUrl').value || window.location.origin).replace(/\/+$/, '');
            try {
              const res = await fetch('./__ma_platform/api/ha/pin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ urlPath, widget, profileId, title, baseUrl })
              });
              const data = await res.json();
              if (!res.ok || data.error) throw new Error(data.error || 'Failed to pin');
              showToast(data.pinned ? ('Pinned "' + title + '" to Home Assistant Sidebar!') : ('Unpinned "' + title + '" from Home Assistant Sidebar.'));
              loadState();
            } catch (err) {
              showToast(err.message, true);
            }
          });
        });

        grid.appendChild(card);
      });
    }

    document.getElementById('save-global-btn').addEventListener('click', async () => {
      const payload = {
        localServerUrl: document.getElementById('g-localServerUrl').value.trim(),
        remoteId: document.getElementById('g-remoteId').value.trim(),
        accessToken: document.getElementById('g-accessToken').value.trim(),
        targetSpeaker: document.getElementById('g-targetSpeaker').value.trim(),
        defaultIngressTarget: document.getElementById('g-defaultIngressTarget').value,
        externalBaseUrl: document.getElementById('g-externalBaseUrl').value.trim()
      };
      await fetch('./__ma_platform/api/global', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      showToast('✓ Saved shared defaults!');
      loadState();
    });

    loadState();
  </script>
</body>
</html>
"""


# =====================================================================
# Standard Library HTTP Handler
# =====================================================================
class WidgetRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Connection", "close")
        self.close_connection = True

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._send_common_headers()
        self.end_headers()
        if getattr(self, "command", "GET") != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status=status)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._send_common_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        req_path = unquote(parsed.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

        widgets = discover_widgets()
        STORE.ensure_widget_profiles(widgets)

        # 1. Root / Launcher
        if req_path == "/" or req_path == "":
            force_launcher = query.get("launcher") == "1"
            default_target = STORE.data["global"].get("defaultIngressTarget", "launcher")
            if not force_launcher and default_target and default_target != "launcher" and ":" in default_target:
                folder, prof_id = default_target.split(":", 1)
                if folder in widgets:
                    suffix = f"?profile={prof_id}" if prof_id and prof_id != "default" else ""
                    self.send_response(302)
                    self.send_header("Location", f"./{folder}/{suffix}")
                    self.send_header("Content-Length", "0")
                    self._send_common_headers()
                    self.end_headers()
                    return
            self._send_bytes(LAUNCHER_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        # 2. Platform API State
        if req_path.endswith("/__ma_platform/api/state"):
            self._send_json(
                {
                    "widgets": widgets,
                    "global": STORE.data["global"],
                    "profiles": STORE.data["profiles"],
                    "haPins": STORE.data["haPins"],
                }
            )
            return

        # 3. Platform Bridge JS
        if req_path.endswith("/__ma_platform/bridge.js"):
            parts = [p for p in req_path.split("/") if p]
            widget_folder = query.get("widget") or (parts[0] if parts and parts[0] in widgets else "MA-Roon")
            profile_id = query.get("profile") or "default"
            if widget_folder not in widgets:
                self._send_bytes(b"// Unknown widget", "application/javascript; charset=utf-8")
                return
            schema = widgets[widget_folder]
            effective = STORE.get_effective_values(schema, profile_id)
            initial_state = {
                "widget": schema,
                "profileId": profile_id,
                "profiles": STORE.data["profiles"].get(widget_folder, {}),
                "global": STORE.data["global"],
                "effectiveValues": effective,
            }
            js_code = BRIDGE_JS_TEMPLATE.replace("__INITIAL_STATE_JSON__", json.dumps(initial_state))
            self._send_bytes(js_code.encode("utf-8"), "application/javascript; charset=utf-8")
            return

        # 3b. Dynamic Web App Manifest & Service Worker (PWA / Standalone Home Screen App)
        if req_path.endswith("/__ma_platform/manifest.webmanifest"):
            parts = [p for p in req_path.split("/") if p]
            widget_folder = query.get("widget") or (parts[0] if parts and parts[0] in widgets else "MA-Roon")
            profile_id = query.get("profile") or "default"
            schema = widgets.get(widget_folder) or (next(iter(widgets.values())) if widgets else {})
            w_name = schema.get("name", widget_folder)
            prof_map = STORE.data["profiles"].get(widget_folder, {})
            prof_name = prof_map.get(profile_id, {}).get("name", profile_id)
            display_name = f"{w_name} ({prof_name})" if profile_id != "default" else w_name
            effective = STORE.get_effective_values(schema, profile_id) if schema else {}
            bg_color = effective.get("backgroundColor") or "#12091c"
            start_suffix = f"?profile={profile_id}" if profile_id != "default" else ""
            manifest_data = {
                "id": f"/{widget_folder}/{start_suffix}",
                "name": display_name,
                "short_name": w_name,
                "description": schema.get("description") or "Music Assistant Touchscreen Player",
                "start_url": f"./{start_suffix}" if start_suffix else "./",
                "display": "standalone",
                "orientation": "any",
                "background_color": bg_color,
                "theme_color": bg_color,
                "icons": [
                    {
                        "src": "./resources/icon.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                        "purpose": "any maskable",
                    }
                ],
            }
            self._send_bytes(
                json.dumps(manifest_data, indent=2).encode("utf-8"),
                "application/manifest+json; charset=utf-8",
            )
            return

        if req_path.endswith("/__ma_platform/sw.js"):
            sw_code = (
                "self.addEventListener('install', () => self.skipWaiting());\n"
                "self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));\n"
                "self.addEventListener('fetch', () => {});\n"
            )
            self._send_bytes(sw_code.encode("utf-8"), "application/javascript; charset=utf-8")
            return

        # 4. Uploaded Media Assets
        if "/__ma_platform/uploads/" in req_path:
            fname = Path(req_path.split("/__ma_platform/uploads/", 1)[1]).name
            target = (UPLOADS_DIR / fname).resolve()
            if target.is_file() and str(target).startswith(str(UPLOADS_DIR.resolve())):
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self._send_bytes(target.read_bytes(), ctype)
                return
            self._send_bytes(b"Not found", "text/plain", status=404)
            return

        # 5. Widget Route: /<WidgetName> or /<WidgetName>/<subpath>
        parts = [p for p in req_path.split("/") if p]
        if not parts:
            self._send_bytes(b"Not found", "text/plain", status=404)
            return

        if parts[0] in widgets:
            widget_folder = parts[0]
            subpath = "/".join(parts[1:])
        elif parts[0] in ("styles", "scripts", "resources") and widgets:
            # Fallback if a browser requests a relative asset without the widget prefix
            referer = self.headers.get("Referer", "")
            matched_folder = next((w for w in widgets if f"/{w}" in referer), next(iter(widgets.keys())))
            widget_folder = matched_folder
            subpath = "/".join(parts)
        else:
            self._send_bytes(b"Not found", "text/plain", status=404)
            return

        widget_dir = ROOT_DIR / widget_folder

        if not subpath or subpath == "index.html":
            profile_id = query.get("profile", "default")
            raw_html = (widget_dir / "index.html").read_text(encoding="utf-8")
            base_tag = f'<base href="./{widget_folder}/" />' if not req_path.endswith("/") and not subpath else ""
            bridge_src = f"./__ma_platform/bridge.js?widget={widget_folder}&profile={profile_id}"
            manifest_href = f"./__ma_platform/manifest.webmanifest?widget={widget_folder}&profile={profile_id}"
            pwa_tags = (
                f'<link rel="manifest" href="{manifest_href}" />\n'
                f'  <meta name="mobile-web-app-capable" content="yes" />\n'
                f'  <meta name="apple-mobile-web-app-capable" content="yes" />\n'
                f'  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />\n'
                f'  <meta name="apple-mobile-web-app-title" content="{widget_folder}" />\n'
                f'  <meta name="theme-color" content="#12091c" />'
            )
            injection = f"\n  {base_tag}\n  {pwa_tags}\n  <script src=\"{bridge_src}\"></script>"
            # Inject immediately after <head> so <base> applies to all <link rel="stylesheet"> tags in <head>
            if re.search(r"<head[^>]*>", raw_html, flags=re.IGNORECASE):
                modified_html = re.sub(
                    r"(<head[^>]*>)",
                    r"\1" + injection,
                    raw_html,
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                modified_html = injection + raw_html
            self._send_bytes(modified_html.encode("utf-8"), "text/html; charset=utf-8")
            return

        target_file = (widget_dir / subpath).resolve()
        if not target_file.is_file() or not str(target_file).startswith(str(widget_dir.resolve())):
            self._send_bytes(b"Not found", "text/plain", status=404)
            return

        ctype = mimetypes.guess_type(str(target_file))[0] or "application/octet-stream"
        self._send_bytes(target_file.read_bytes(), ctype)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        req_path = unquote(parsed.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

        widgets = discover_widgets()
        STORE.ensure_widget_profiles(widgets)

        if req_path.endswith("/__ma_platform/api/global"):
            body = json.loads(self._read_body().decode("utf-8") or "{}")
            for key in ("localServerUrl", "remoteId", "accessToken", "targetSpeaker", "defaultIngressTarget", "externalBaseUrl"):
                if key in body:
                    STORE.data["global"][key] = body[key]
            STORE.save()
            self._send_json({"ok": True, "global": STORE.data["global"]})
            return

        if req_path.endswith("/__ma_platform/api/profile"):
            body = json.loads(self._read_body().decode("utf-8") or "{}")
            widget_folder = body.get("widget", "")
            profile_id = body.get("profileId", "default")
            name = body.get("name", profile_id)
            values = body.get("values", {})
            if widget_folder not in widgets:
                self._send_json({"error": f"Unknown widget: {widget_folder}"}, status=404)
                return
            schema_defaults = {k: v["default"] for k, v in widgets[widget_folder]["properties"].items()}
            existing = STORE.data["profiles"][widget_folder].get(profile_id, {})
            merged_values = dict(schema_defaults)
            merged_values.update(existing.get("values", {}))
            if isinstance(values, dict):
                merged_values.update(values)
            STORE.data["profiles"][widget_folder][profile_id] = {
                "id": profile_id,
                "name": name,
                "values": merged_values,
            }
            STORE.save()
            self._send_json({"ok": True, "profile": STORE.data["profiles"][widget_folder][profile_id]})
            return

        if req_path.endswith("/__ma_platform/api/upload"):
            raw_bytes = self._read_body()
            orig_name = Path(query.get("filename", "upload.bin")).name
            ext = Path(orig_name).suffix.lower() or ".bin"
            safe_name = f"media_{secrets.token_hex(6)}{ext}"
            dest = UPLOADS_DIR / safe_name
            dest.write_bytes(raw_bytes)
            self._send_json({"ok": True, "url": f"/__ma_platform/uploads/{safe_name}"})
            return

        if req_path.endswith("/__ma_platform/api/ha/pin"):
            body = json.loads(self._read_body().decode("utf-8") or "{}")
            url_path = re.sub(r"[^a-z0-9-]", "-", str(body.get("urlPath", "")).lower()).strip("-")
            if not url_path or "-" not in url_path:
                url_path = f"ma-{url_path or 'widget'}"

            widget_folder = body.get("widget", "MA-Roon")
            profile_id = body.get("profileId", "default")
            title = body.get("title", widget_folder)
            base_url = (body.get("baseUrl") or STORE.data["global"].get("externalBaseUrl") or "").rstrip("/")

            if url_path in STORE.data["haPins"]:
                try:
                    dashboards = ha_ws_call_sync([{"type": "lovelace/dashboards/list"}])[0] or []
                    for d in dashboards:
                        if d.get("url_path") == url_path:
                            ha_ws_call_sync([{"type": "lovelace/dashboards/delete", "dashboard_id": d["id"]}])
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                STORE.data["haPins"].pop(url_path, None)
                STORE.save()
                self._send_json({"ok": True, "pinned": False})
                return

            target_iframe_url = f"{base_url}/{widget_folder}" + (f"?profile={profile_id}" if profile_id != "default" else "")
            try:
                ha_ws_call_sync(
                    [
                        {
                            "type": "lovelace/dashboards/create",
                            "url_path": url_path,
                            "title": title,
                            "icon": "mdi:music-circle-outline",
                            "show_in_sidebar": True,
                            "require_admin": False,
                        },
                        {
                            "type": "lovelace/config/save",
                            "url_path": url_path,
                            "config": {
                                "views": [
                                    {
                                        "title": title,
                                        "path": "default_view",
                                        "panel": True,
                                        "cards": [
                                            {
                                                "type": "iframe",
                                                "url": target_iframe_url,
                                                "aspect_ratio": "100%",
                                            }
                                        ],
                                    }
                                ]
                            },
                        },
                    ]
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            STORE.data["haPins"][url_path] = {
                "widget": widget_folder,
                "profileId": profile_id,
                "title": title,
                "url": target_iframe_url,
            }
            STORE.save()
            self._send_json({"ok": True, "pinned": True})
            return

        self._send_json({"error": "Not found"}, status=404)


class WidgetHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server() -> None:
    discovered = discover_widgets()
    logger.info(
        "Starting Music Assistant Widgets Server on port %d (discovered widgets: %s)",
        PORT,
        ", ".join(discovered.keys()) or "none",
    )
    server = WidgetHTTPServer(("0.0.0.0", PORT), WidgetRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
