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

# Zip with ditto: it preserves the bundle structure and symlinks, which a
# plain `zip` mangles.
echo
echo "=== Packing for delivery ==="
ditto -c -k --sequesterRsrc --keepParent dist/VideoLabeler.app VideoLabeler-macos.zip

echo
echo "=== Done ==="
echo "App:  $(pwd)/dist/VideoLabeler.app"
echo "Zip:  $(pwd)/VideoLabeler-macos.zip"
echo "Annotations will be saved to an \"annotations\" folder next to the app."
