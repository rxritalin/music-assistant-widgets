---
trigger: always_on
---
# iCUE Widget & Music Assistant 2.11+ Development Guidelines

## 1. iCUE HTML Validator, Packaging & Layout Guardrails
- **Never use `<header>` or the substring `<head` after `</head>`**: The `icuewidget validate` CLI performs a raw substring search for `<head` after `</head>`. Using `<header>` elements or comments containing `<header` inside `<body>` triggers a `Multiple <head> tags are not allowed` error. Always use `<div class="header-bar">` instead.
- **No `<style>` or `--` Inside `<head>`**: iCUE parses `<head>` as strict XML where any `--` (such as CSS variables `var(--layout-unit)`) breaks XML parsing. Keep only `<meta>`, `<link>`, `<title>`, and `<script type="application/json" id="x-icue-groups">` in `<head>`. Put any inline `<style>` blocks at the top of `<body>` (which also bypasses QtWebEngine's aggressive `.css` disk cache).
- **Full-Height `.content` Container**: Inside `.main-content`, always set `.content { position: relative; width: 100%; height: 100%; flex: 1 1 auto; }` so the widget layout centers properly and does not shift upward.
- **Scrollable Lists Inside Modals**: Never use `flex: 1 1 0` (`flex-basis: 0px`) on a scrollable list inside an `auto`-height `.modal-card`, as it collapses the list to `0px` height. Use `flex: 1 1 auto; min-height: 0;` and set explicit `min-height` and `max-height` on modal list containers.
- **Descender Clipping Prevention**: Any text element using `overflow: hidden` or `-webkit-line-clamp` (such as `.track-title`) must use `line-height: 1.25` or higher and `padding-bottom: calc(var(--layout-unit) * 0.8)` so letters with descenders (`g`, `y`, `p`, `j`, `q`) are not clipped at the bottom.
- **In-Place `.icuewidget` Packaging (Never Use `-o`)**: Always package using `icuewidget package MA-Roon` (or `icuewidget package <WidgetFolder>`) without the `-o` flag so the CLI derives the filename from `manifest.json` (`"name": "MA-Roon"` -> `ma-roon.icuewidget`) and updates it in-place rather than creating duplicate `.icuewidget` archives.
- **Speaker Hardware Permission for Web Audio**: Any widget using Web Audio / `AudioContext` playback (such as `@music-assistant/sendspin`) must include `{ "type": "speaker" }` inside `manifest.json`'s `permissions` array.

## 2. Music Assistant 2.11+ API, Player Visibility & Artwork Resolution
- **Artwork Resolution Hierarchy**: Spotify tracks often have `track.image: null` and `track.metadata.images: null`, storing cover art on `track.album.metadata.images` and `QueueItem.image`. Always resolve media images in this exact fallback order:
  1. `item.streamdetails.stream_metadata.image_url`
  2. `item.image` / `item.metadata.images` (outer `QueueItem`)
  3. `mediaItem.image` / `mediaItem.metadata.images`
  4. `mediaItem.album.image` / `mediaItem.album.metadata.images`
  5. `mediaItem.artists[i].image` / `mediaItem.artists[i].metadata.images`
- **Image Proxy Endpoint & Allowed Sizes**: Music Assistant 2.11+ uses `/imageproxy/{proxy_id}?size=512` (or `/imageproxy?path=...&provider=...&size=512`) and strictly restricts `size` to `[80, 160, 256, 512, 1024]`. Never request arbitrary sizes like `500` or `600` (which return `HTTP 400`). Always include an `onerror` fallback that retries without `crossorigin="anonymous"` and falls back to `imgObj.path` if it is an `https://` URL.
- **Speaker Groups & Per-Speaker Volumes**:
  - Use `players/cmd/group_volume` for group players (`type === 'group'`) with fallback to `players/cmd/volume_set`, and `players/cmd/volume_set` for individual speakers.
  - Use `players/cmd/set_members` (`target_player`, `player_ids_to_add` / `player_ids_to_remove`) when `set_members` is supported, falling back to `players/cmd/group` and `players/cmd/ungroup`.
- **Player List Filtering (`universal_player` vs. `protocol` Legs)**:
  - Music Assistant 2.11+ wraps protocol players (Sendspin, AirPlay, Cast) inside a `universal_player` (`player_id: "up..."`, `type: "player"`) that lists its underlying protocol IDs in `p.output_protocols[].output_protocol_id`, while also returning the raw protocol player (`type: "protocol"`).
  - Always filter out raw protocol legs (`p.type === 'protocol'`), players with `p.hide_in_ui === true`, and players whose `p.hide_player_in_ui` array includes `'always'` or (`'when_unavailable'` when `p.available === false`).

## 3. Card Flip & Menu Transition Animations
- **3D Flip Only Between Front & Back**: Only use 3D `rotateY(180deg)` when flipping between the Album Art front face and the Library Browser back face.
- **In-Menu Category Transitions**: When switching between menu categories or drilling into playlists while the card is already flipped, keep the card stationary at `rotateY(180deg)` and animate the inner `.browser-list` (e.g., directional vertical slide + cross-fade).

## 4. Smooth Touch Sliders on Xeneon Edge (`dashboard_lcd`)
- **Full-Bleed Native `<input type="range">` Overlay**: Xeneon Edge's `DashLCDWindow` (`WS_EX_NOACTIVATE`) uses `WebEngineFocusController.qml`, which whitelists `'range'` inputs and blurs plain `<div>` touches. Every horizontal or vertical slider (volume, seek/progress, blur, opacity, brightness) MUST include a full-bleed transparent `<input type="range" class="native-slider-input">` (`position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; z-index: 10; touch-action: none;`). For vertical bottom-to-top sliders, add `.is-vertical { writing-mode: vertical-lr; direction: rtl; }` and make the interactive track `width: 100%` of the popup pill with a visual rail inside.
- **Non-Cancelling `attachSliderScrubbing` Pattern**:
  - Bind `input` and `change` on the `<input type="range">` element **plus** `touchstart`/`pointerdown` on the container and `touchmove`/`pointermove`/`mousemove` + `touchend`/`pointerup`/`mouseup` on `window`.
  - **NEVER** call `evt.preventDefault()` on `pointerdown` or `touchstart` (which blocks Chromium's native C++ `SliderThumbElement` drag handler).
  - **NEVER** call `setPointerCapture` on the outer `<div>` container (which steals capture from `<input type="range">`).
  - **NEVER** abort drags on `window.blur`, `pointercancel`, or `evt.buttons === 0` (which fire immediately on Xeneon Edge's non-activating touchscreen window).
- **In-Place DOM Updates & 1200ms Echo Holdoff**:
  - Never run `list.innerHTML = ''` on a list containing sliders while a slider is being dragged or updated via WebSocket/API events. Always update list cards in-place (`card._updatePlayer(data)`) when item IDs match, and ignore server echo updates for `1200ms` after the last user drag timestamp.

## 5. Real-Time Dynamic Text Drop Shadow Engine (Static & Moving WebGL Backgrounds)
- **Un-Isolated GPU `mix-blend-mode: multiply` Pseudo-Element Split (`::before` + `::after`)**:
  - To allow a text drop shadow to dynamically darken moving background pixels underneath it without darkening the white foreground text:
    1. Keep `.widget-background`, `.main-content`, and the text stage (`.lyrics-stage`) free of stacking-context isolation (`z-index: auto; isolation: auto; transform: none;` when open).
    2. Store the line text in `data-text` and set the host element (`.lyric-line.is-current`) to `color: transparent; text-shadow: none; overflow: visible;`.
    3. Render `::before` with `content: attr(data-text); color: transparent; mix-blend-mode: multiply;` and layered `text-shadow` using `--lyric-shadow-color` and `--lyric-shadow-glow`.
    4. Render `::after` with `content: attr(data-text); color: var(--media-text-color); mix-blend-mode: normal;` and a tight `text-shadow` using `--lyric-shadow-deep` so the glyph fill stays crisp white.
- **Synchronous Post-Render WebGL Framebuffer Color Sampling (`sampleLiveBackdropForLyricShadow`)**:
  - Because WebGL clears its drawing buffer after compositing unless `preserveDrawingBuffer` is enabled, always call `sampleLiveBackdropForLyricShadow(canvas)` **synchronously right after `visualizer.render(...)` inside the `requestAnimationFrame` callback**.
  - Draw the canvas sub-region behind the text (`srcX = width * 0.42`, `srcY = height * 0.25`, `srcW = width * 0.52`, `srcH = height * 0.48`) onto a `12×12` 2D canvas at ~10–12Hz, boost saturation (`1.65×`), and smoothly lerp (`0.28`) the RGB state into `--lyric-shadow-deep`, `--lyric-shadow-color`, and `--lyric-shadow-glow`.

## 6. MilkDrop Visualizer (Butterchurn WebGL2) & Background Brightness Architecture
- **Zero-Permission Local Bundle**: Load `butterchurn.min.js`, `butterchurnPresets.min.js`, and `butterchurnPresetsExtra.min.js` locally from `scripts/`. Pass `audioContext = null` to `butterchurn.createVisualizer(null, canvas, { width, height, pixelRatio: 1, textureRatio: 1 })` and supply `{ audioLevels: { timeByteArray, timeByteArrayL, timeByteArrayR } }` to `visualizer.render(...)` so no `AudioContext` or microphone permissions are needed.
- **Three-Tier Audio Feed (Local Sendspin Web Audio `AnalyserNode` + `/milkdrop_visualizer` WebSocket Relay + Procedural Stereo Fallback)**:
  1. **Direct Web Audio Tap (`AnalyserNode`)**: When the built-in Sendspin PC player is the active speaker (`sendspinController.matchesPlayer(activePlayer)`), attach an `AnalyserNode` (`fftSize = 1024`) to `player.scheduler.gainNode` and read `getByteTimeDomainData` on every frame for zero-latency waveform reactivity and real-time bass-energy beat detection.
  2. **MA Server WebSocket Relay (`/milkdrop_visualizer`)**: When playing on external network speakers via Local Server URL (`state.connectionMode === 'ws'`), connect to `${wsBase}/milkdrop_visualizer?player=${encodeURIComponent(playerId)}`, authenticate with `{ type: 'auth', token }`, synchronize clock offset via `client/time` ↔ `server/time`, and consume binary `0x01` waveform frames and `0x02` beat/downbeat preset triggers.
  3. **Procedural Stereo Fallback**: When in WebRTC Remote ID mode, Preview/Demo mode, or if no live audio frame has arrived within `360ms`, seamlessly feed `generateSyntheticAudioFrame(now)` so presets always animate smoothly.
- **Scrim Z-Index Layering & Bidirectional Background Brightness (`0%` – `200%`)**:
  - Keep `.ambient-scrim` at `z-index: 1` (**below** `.milkdrop-layer` at `z-index: 2`) so the static album-art scrim (`--dynamic-scrim`) does not double-darken the MilkDrop canvas.
  - Expose **Background Brightness (`0%` – `200%`)** inside `#milkdrop-modal` alongside `Enabled`, `Blur (0–30px)`, `Opacity (10–100%)`, and the `★` Favorite / Preset picker. Sync it bidirectionally with iCUE's `bgBrightness` property (`--bg-brightness`) and progressively reduce `.milkdrop-scrim` alpha toward `0` when brightness is raised above `100%`.

## 7. iCUE Settings Persistence & Personalization Color Tokens
- **Never Use `tr()` in `data-default`**: iCUE evaluates `data-default` before `<body>` scripts execute. Calling `tr('...')` inside `data-default` throws `ReferenceError: tr is not defined`. Always use plain quoted literals (e.g., `data-default="''"`).
- **Three-Way Text/Icon Color Split**: Separate color properties into `controlsColor` (`--controls-color` for buttons/icons), `textColor` (`--media-text-color` for primary readout/track text), and `menuTextColor` (`--menu-text-color` for menus/modals/lists).

## 8. Built-In PC Player (`@music-assistant/sendspin`) in iCUE `QtWebEngine` (`file://`)
- **Deterministic X25519 Client Identity Across iCUE Restarts**:
  - iCUE's `QtWebEngine` clears `file://` `localStorage` on application restart while persisting `x-icue-property` values. Always pre-seed `localStorage.setItem('sendspin-identity-sk', ...)` from a fixed deterministic 32-byte seed before calling `SendspinPlayer.connect()` so `clientId` remains identical across restarts and custom player renames (`sendspinName`).
  - Match the local Sendspin player against Music Assistant's player list by checking both `p.player_id === clientId` and `p.output_protocols[].output_protocol_id === clientId` (excluding `p.type === 'protocol'`). Never inject a synthetic `state.players[clientId]` entry when connected to a live server, as it duplicates the server's `universal_player` (`up...`).
- **Stutter-Free & Cutover-Free Web Audio Scheduling**:
  - Windows `QtWebEngine` reports jittery `AudioContext.getOutputTimestamp()` values that cause `SendspinPlayer` to repeatedly oscillate between `estimated` and `timestamp` clock domains (`performGuardedCutover`), flushing and recreating all scheduled `AudioBufferSourceNode`s ~2–3 seconds into playback.
  - Immediately after creating `new SendspinPlayer(...)`, call `player.scheduler.clockSource.disableTimestampPromotion()`, pass `correctionMode: 'quality-local'` and `useOutputLatencyCompensation: false`, cap `player.scheduler.getTargetScheduledHorizonSec = () => 2.0`, and set `bufferCapacity: 384000` (~2 seconds of PCM) so the server streams smoothly instead of bursting 30 seconds (~1,000 audio nodes) at once.
- **Codec Priority on `file://` Origins (`pcm` First on Local WebSocket)**:
  - Because `file://` is not a Chromium `[SecureContext]`, `window.AudioDecoder` (WebCodecs) is unavailable and `@music-assistant/sendspin` falls back to synchronous main-thread Emscripten `asm.js` decoding (`OggOpusDecoder`) for Opus.
  - Always pass `codecs: ['pcm', 'flac', 'opus']` when connected via Local Server URL (`state.connectionMode === 'ws'`) for zero-CPU native PCM playback, and `codecs: ['flac', 'opus', 'pcm']` over WebRTC Remote ID data channels.
- **Immediate Disconnect Buffer Cleanup & Multi-Webview Contention Guard**:
  - Pass `reconnect: { maxAttempts: 0 }` and override `player.scheduleDisconnectPlaybackReset` to immediately invoke `resetPlaybackStateAfterDisconnect()` / `scheduler.clearBuffers()` on socket close so the 20-second disconnect playback runway never overlaps with a reconnecting session.
  - Before auto-connecting in `syncWithAppState()`, check if the server already shows the local Sendspin player as `available: true` without an active local session (`isAlreadyConnectedElsewhereOnServer()`). If another widget webview (such as the iCUE desktop configuration preview alongside the Xeneon Edge LCD) already holds the active Sendspin session, yield instead of fighting over the same `clientId` unless the user explicitly clicks the speaker in that view.
