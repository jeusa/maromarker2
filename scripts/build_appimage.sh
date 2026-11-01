#!/usr/bin/env bash
set -euo pipefail

# Always work from the repository root.
cd "$(dirname "$0")/.."

APPDIR="build/MarOMarker2.AppDir"
DISTDIR="build/appimage/app_entry.dist"
OUTPUT="dist/MarOMarker2-x86_64.AppImage"

# Compile the application and bundle its dependencies.
python -m nuitka \
    --standalone \
    --enable-plugin=pyside6 \
    --include-package=maromarker2 \
    --include-package-data=maromarker2 \
    --show-scons \
    --assume-yes-for-downloads \
    --output-dir=build/appimage \
    --output-filename=maromarker2.bin \
    app_entry.py

# Assemble the AppDir from scratch.
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" tools dist

cp -a "$DISTDIR/." "$APPDIR/usr/bin/"

cp packaging/AppRun "$APPDIR/"
cp packaging/maromarker2.desktop "$APPDIR/"
cp packaging/maromarker2.png "$APPDIR/"

chmod +x "$APPDIR/AppRun"
ln -s maromarker2.png "$APPDIR/.DirIcon"

# Download the AppImage packaging tool.
curl -fL \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
    -o tools/appimagetool.AppImage

chmod +x tools/appimagetool.AppImage

# Extract the tool so the build does not require FUSE.
TOOL="$(pwd)/tools/appimagetool.AppImage"
(
    cd tools
    "$TOOL" --appimage-extract >/dev/null
)

# Convert the AppDir to an AppImage.
ARCH=x86_64 tools/squashfs-root/AppRun "$APPDIR" "$OUTPUT"

chmod +x "$OUTPUT"
test -s "$OUTPUT"

echo "Created $OUTPUT"