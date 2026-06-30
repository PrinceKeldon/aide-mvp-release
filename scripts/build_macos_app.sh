#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"
APP_NAME="Vera"
DMG_PATH="dist/${APP_NAME}.dmg"

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller is not installed in this venv."
  echo "Install it with: $PYTHON_BIN -m pip install pyinstaller"
  exit 1
fi

rm -rf "build/${APP_NAME}" "dist/${APP_NAME}" "dist/${APP_NAME}.app" "$DMG_PATH"

"$PYTHON_BIN" -m PyInstaller --noconfirm --clean Vera.spec

if command -v create-dmg >/dev/null 2>&1; then
  create-dmg \
    --volname "$APP_NAME" \
    --window-pos 200 120 \
    --window-size 640 420 \
    --icon-size 100 \
    --app-drop-link 480 190 \
    "$DMG_PATH" \
    "dist/${APP_NAME}.app"
  echo "Built $DMG_PATH"
else
  echo "Built dist/${APP_NAME}.app"
  echo "Install create-dmg to generate a drag-to-Applications DMG:"
  echo "  brew install create-dmg"
fi
