#!/bin/bash
#
# Build Strip Bay.app and a disk image.
#
#   ./packaging/build.sh                 build the app and a DMG
#   ./packaging/build.sh --app-only      just the bundle
#   ./packaging/build.sh --sign "ID"     sign with a Developer ID certificate
#
# Runs on macOS (uses hdiutil, produces a compressed UDZO image) and on Linux
# (uses genisoimage, produces an ISO9660 image with Rock Ridge metadata, which
# macOS mounts). Building on macOS is preferable if you have the choice --
# it is the only way to sign, and signing is what stops Gatekeeper complaining.

set -euo pipefail

VERSION="1.1"
NAME="Strip Bay"
IDENTIFIER="com.example.stripbay"   # change this before signing

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
BUNDLE="$DIST/$NAME.app"
STAGE="$DIST/dmg"

APP_ONLY=0
SIGN_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --app-only) APP_ONLY=1 ;;
        --sign) SIGN_ID="${2:-}"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

echo "==> cleaning"
rm -rf "$DIST"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources/app"

echo "==> payload"
for item in server.py registry.py nnumber.py build_registry.py static; do
    cp -R "$ROOT/$item" "$BUNDLE/Contents/Resources/app/"
done
find "$BUNDLE/Contents/Resources/app" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

cp "$ROOT/packaging/launcher.sh" "$BUNDLE/Contents/MacOS/StripBay"
chmod 755 "$BUNDLE/Contents/MacOS/StripBay"

if [ -f "$ROOT/packaging/StripBay.icns" ]; then
    cp "$ROOT/packaging/StripBay.icns" "$BUNDLE/Contents/Resources/"
else
    echo "    no icon found; run 'python3 make_icon.py' for one" >&2
fi

printf 'APPL????' > "$BUNDLE/Contents/PkgInfo"

cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                  <string>$NAME</string>
    <key>CFBundleDisplayName</key>           <string>$NAME</string>
    <key>CFBundleIdentifier</key>            <string>$IDENTIFIER</string>
    <key>CFBundleVersion</key>               <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>    <string>$VERSION</string>
    <key>CFBundleExecutable</key>            <string>StripBay</string>
    <key>CFBundleIconFile</key>              <string>StripBay</string>
    <key>CFBundlePackageType</key>           <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>CFBundleDevelopmentRegion</key>     <string>en</string>
    <key>LSMinimumSystemVersion</key>        <string>11.0</string>
    <key>LSApplicationCategoryType</key>     <string>public.app-category.utilities</string>
    <key>NSHighResolutionCapable</key>       <true/>
    <key>NSLocalNetworkUsageDescription</key>
    <string>Strip Bay reads the aircraft feed from your PiAware receiver on the local network.</string>
</dict>
</plist>
PLIST

echo "==> built $BUNDLE"

if [ -n "$SIGN_ID" ]; then
    if ! command -v codesign >/dev/null 2>&1; then
        echo "    codesign is only available on macOS" >&2; exit 1
    fi
    echo "==> signing as $SIGN_ID"
    codesign --force --deep --options runtime --timestamp \
             --sign "$SIGN_ID" "$BUNDLE"
    codesign --verify --strict --verbose=2 "$BUNDLE"
fi

[ "$APP_ONLY" -eq 1 ] && { echo "done"; exit 0; }

echo "==> staging disk image"
mkdir -p "$STAGE"
cp -R "$BUNDLE" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

DMG="$DIST/StripBay-$VERSION.dmg"

if command -v hdiutil >/dev/null 2>&1; then
    hdiutil create -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
elif command -v genisoimage >/dev/null 2>&1; then
    # -R keeps the executable bit on the launcher, which the app will not
    # start without. -apple adds the Apple ISO9660 extensions.
    genisoimage -quiet -V "$NAME" -D -R -apple -no-pad -o "$DMG" "$STAGE"
else
    echo "no hdiutil or genisoimage available; the bundle is still in $DIST" >&2
    exit 1
fi

# A zip as well, because an unsigned DMG occasionally needs coaxing and this
# always works: unzip it and drag the app across.
if command -v zip >/dev/null 2>&1; then
    (cd "$DIST" && zip -qry "StripBay-$VERSION.zip" "$NAME.app")
fi

rm -rf "$STAGE"
echo "==> $DMG"
ls -lh "$DIST" | tail -n +2
