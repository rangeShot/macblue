#!/bin/bash
# install.sh — build and install macblue on this Mac
# Usage: bash install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.macblue.app"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
APP_SRC="$SCRIPT_DIR/dist/macblue.app"
APP_DST="/Applications/macblue.app"

echo "=== macblue installer ==="
echo ""

# ── 1. Homebrew & blueutil ───────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  echo "ERROR: Homebrew not found. Install from https://brew.sh" >&2
  exit 1
fi

if ! /opt/homebrew/bin/blueutil --version &>/dev/null 2>&1 && \
   ! /usr/local/bin/blueutil --version &>/dev/null 2>&1; then
  echo "→ Installing blueutil..."
  brew install blueutil
else
  echo "✓ blueutil installed."
fi

# ── 2. Python venv & deps ─────────────────────────────────────────────────
VENV="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV" ]]; then
  echo "→ Creating virtual environment..."
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
echo "→ Installing Python dependencies..."
pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

# ── 3. Make scripts executable ──────────────────────────────────────────────
chmod +x "$SCRIPT_DIR/scripts/connect.sh" "$SCRIPT_DIR/scripts/disconnect.sh"

# ── 4. Prepare menu bar icons (22pt @1x + 22pt @2x retina) ───────────────
ICON_SRC="$SCRIPT_DIR/assets/icon.png"
ICON_1X="$SCRIPT_DIR/assets/icon_menubar.png"
ICON_2X="$SCRIPT_DIR/assets/icon_menubar@2x.png"

if [[ -f "$ICON_SRC" ]]; then
  sips -z 22 22 "$ICON_SRC" --out "$ICON_1X" &>/dev/null
  sips -s dpiWidth 72 -s dpiHeight 72 "$ICON_1X" &>/dev/null
  sips -z 44 44 "$ICON_SRC" --out "$ICON_2X" &>/dev/null
  sips -s dpiWidth 144 -s dpiHeight 144 "$ICON_2X" &>/dev/null
  echo "✓ Menu bar icons prepared."
else
  echo "WARNING: assets/icon.png not found — menu bar icon may be missing." >&2
fi

# ── 5. Build the .app bundle ────────────────────────────────────────────────
echo "→ Building macblue.app..."
cd "$SCRIPT_DIR"
rm -rf build dist
python3 setup.py py2app --dist-dir "$SCRIPT_DIR/dist" 2>&1 | tail -1
echo "✓ Built: $APP_SRC"

# ── 6. Install to /Applications ─────────────────────────────────────────────
echo "→ Installing to /Applications..."

# Stop old instance
launchctl unload "$PLIST_DST" 2>/dev/null || true
sleep 1

# Remove old .app and copy new one
rm -rf "$APP_DST"
cp -R "$APP_SRC" "$APP_DST"
echo "✓ Installed: $APP_DST"

# ── 7. Install LaunchAgent (auto-start at login) ───────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"

APP_EXECUTABLE="$APP_DST/Contents/MacOS/macblue"

cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_NAME</string>

  <key>ProgramArguments</key>
  <array>
    <string>$APP_EXECUTABLE</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>MACBLUE_DIR</key>
    <string>$SCRIPT_DIR</string>
  </dict>

  <key>WorkingDirectory</key>
  <string>$SCRIPT_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$SCRIPT_DIR/macblue.log</string>
  <key>StandardErrorPath</key>
  <string>$SCRIPT_DIR/macblue.err</string>

  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
</dict>
</plist>
EOF

launchctl load "$PLIST_DST"
echo "✓ LaunchAgent installed (auto-starts at login)."

# ── 8. Done ─────────────────────────────────────────────────────────────────
echo ""
echo "IMPORTANT: Grant Bluetooth permission if prompted."
echo "  → System Settings → Privacy & Security → Bluetooth"
echo ""
echo "=== Setup complete ==="
echo ""
echo "  macblue is now running in your menu bar."
echo "  It will auto-start every time you log in."
echo ""
echo "  The app is installed at: $APP_DST"
echo "  To reinstall: bash $SCRIPT_DIR/install.sh"
echo ""
