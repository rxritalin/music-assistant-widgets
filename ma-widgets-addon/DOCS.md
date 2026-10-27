# Music Assistant Widgets — Home Assistant Add-on Documentation

This Add-on hosts all **Music Assistant Widgets** (`MA-Roon` and future designs) from a single lightweight server (`~15MB` RAM), complete with:

- **Shared Music Assistant Connection Defaults**: Enter your Music Assistant Server URL (`http://<ip>:8095`) and Long-Lived Access Token once—all widgets and screen profiles inherit it automatically.
- **Per-Screen Profiles**: Create separate profiles for different screens around your home (e.g., `?profile=office`, `?profile=kitchen`, `?profile=wall-tablet`) so each screen can target a different default speaker, Sendspin PC/Tablet player name, or visual theme.
- **Auto-Generated Live Settings Drawer**: Click the top-right pill inside any widget to open its full Settings Drawer (colors, MilkDrop visualizer controls, blur, brightness, standby media, and Sendspin player toggle) generated directly from the widget's schema.
- **Pin Individual Widgets/Profiles to the Home Assistant Sidebar**: Click **Pin to HA Sidebar** on any widget or profile in the Launcher to register a dedicated full-screen Sidebar entry in Home Assistant.

## Accessing Your Widgets

1. **Via Home Assistant Sidebar (Ingress)**:
   - Enable **Show in sidebar** on the Add-on page and click **MA Widgets** in your Home Assistant sidebar.
   - Inside the Launcher, you can keep the Multi-Widget Launcher as your default sidebar view, or set **Default Home Assistant Sidebar View** to open a specific widget (like `MA-Roon`) immediately.
2. **Direct LAN URL (for Kiosks, Wall Tablets, or Desktop Browsers)**:
   - Hub / Launcher: `http://<home-assistant-ip>:8096/`
   - `MA-Roon` (Default Profile): `http://<home-assistant-ip>:8096/MA-Roon`
   - `MA-Roon` (Named Profile): `http://<home-assistant-ip>:8096/MA-Roon?profile=kitchen`
