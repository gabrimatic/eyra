#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="${EYRA_MENU_BAR_PACKAGE_DIR:-$ROOT_DIR/apps/EyraMenuBar}"
OUTPUT_DIR="${EYRA_MENU_BAR_OUTPUT_DIR:-$ROOT_DIR/dist}"
APP_NAME="${EYRA_MENU_BAR_APP_NAME:-Eyra}"
APP_DIR="$OUTPUT_DIR/$APP_NAME.app"
BUNDLE_ID="${EYRA_MENU_BAR_BUNDLE_ID:-info.gabrimatic.eyra}"
MIN_MACOS="${EYRA_MENU_BAR_MIN_MACOS:-13.0}"

if [[ ! -f "$PACKAGE_DIR/Package.swift" ]]; then
    echo "Menu bar Swift package not found: $PACKAGE_DIR" >&2
    exit 1
fi

if [[ -n "${EYRA_MENU_BAR_VERSION:-}" ]]; then
    VERSION="$EYRA_MENU_BAR_VERSION"
else
    VERSION="$(python3 - "$ROOT_DIR/pyproject.toml" <<'PY'
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

path = sys.argv[1]
try:
    text = open(path, encoding="utf-8").read() if path else ""
except OSError:
    text = ""
if tomllib:
    print(tomllib.loads(text).get("project", {}).get("version", "0.0.0"))
else:
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    print(match.group(1) if match else "0.0.0")
PY
)"
fi

rm -rf "$PACKAGE_DIR/.build"
swift build --package-path "$PACKAGE_DIR" -c release
BIN_DIR="$(swift build --package-path "$PACKAGE_DIR" -c release --show-bin-path)"
BINARY="$BIN_DIR/EyraMenuBar"
[[ -x "$BINARY" ]] || { echo "Built menu bar binary not found: $BINARY" >&2; exit 1; }

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BINARY" "$APP_DIR/Contents/MacOS/EyraMenuBar"
chmod +x "$APP_DIR/Contents/MacOS/EyraMenuBar"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Eyra</string>
  <key>CFBundleDisplayName</key>
  <string>Eyra</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key>
  <string>EyraMenuBar</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>$MIN_MACOS</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHumanReadableCopyright</key>
  <string>Copyright © Soroush Yousefpour. Licensed under PolyForm Noncommercial 1.0.0.</string>
</dict>
</plist>
PLIST

plutil -lint "$APP_DIR/Contents/Info.plist" >/dev/null

if [[ "${EYRA_MENU_BAR_SKIP_CODESIGN:-false}" != "true" ]] && command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$APP_DIR" >/dev/null
fi

echo "$APP_DIR"
