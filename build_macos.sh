#!/usr/bin/env bash
# Build VideoLabeler.app on macOS. Needs Python 3.9+ (python.org or Homebrew).
# Run it from a Terminal in this folder:   bash build_macos.sh
# Result: dist/VideoLabeler.app  — plus VideoLabeler-macos.zip to hand over.

set -euo pipefail
cd "$(dirname "$0")"

echo
echo "=== Creating a clean build environment ==="
python3 -m venv build-env
source build-env/bin/activate

echo
echo "=== Installing dependencies (PyQt6 via pip: this is what gives audio) ==="
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

echo
echo "=== Checking that audio support is present ==="
python -c "from PyQt6.QtMultimedia import QMediaPlayer; print('audio OK')"

echo
echo "=== Building ==="
rm -rf build dist
pyinstaller videolabeler.spec --noconfirm

# An ad-hoc signature stops macOS from killing the app outright on Apple
# Silicon. It is NOT notarization: see PACKAGING.md for what other Macs will
# have to do the first time they open it.
echo
echo "=== Ad-hoc signing ==="
codesign --force --deep --sign - dist/VideoLabeler.app
codesign --verify --verbose dist/VideoLabeler.app || true

# Pack what the end users receive: the app plus their instructions, and
# nothing else — no source, no build scripts.
#
# ditto, not zip: a .app is a folder, and a plain `zip` mangles its symlinks
# and extended attributes, leaving an app that will not open.
echo
echo "=== Packing for delivery ==="
DELIVERY="dist/VideoLabeler-macos"
rm -rf "$DELIVERY" VideoLabeler-macos.zip
mkdir -p "$DELIVERY"
ditto dist/VideoLabeler.app "$DELIVERY/VideoLabeler.app"
[ -f README_USERS.txt ] && cp README_USERS.txt "$DELIVERY/"
ditto -c -k --sequesterRsrc --keepParent "$DELIVERY" VideoLabeler-macos.zip

echo
echo "=== Done ==="
echo "Send this file to the annotators, and nothing else:"
echo "    $(pwd)/VideoLabeler-macos.zip"
echo
echo "It contains the app and a short usage guide. Tell them to"
echo "unzip it, keep the app somewhere writable (Desktop, not /Applications),"
echo "and open it the FIRST time with right-click -> Open."
