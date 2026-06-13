#!/usr/bin/env bash
# Build a .deb package. Usage: ./build-deb.sh [VERSION]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
VERSION="${1:-1.1.0}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

install -Dm755 "$ROOT/wg-gtk-client.py" \
    "$STAGE/usr/bin/wg-gtk-client"
install -Dm644 "$HERE/wg-gtk-client.desktop" \
    "$STAGE/usr/share/applications/wg-gtk-client.desktop"
install -Dm644 "$HERE/wg-gtk-client.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/wg-gtk-client.svg"
install -Dm644 "$HERE/com.thern.wg-gtk-client.policy" \
    "$STAGE/usr/share/polkit-1/actions/com.thern.wg-gtk-client.policy"

mkdir -p "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/" "$HERE/debian/control" > "$STAGE/DEBIAN/control"

OUT="$ROOT/wg-gtk-client_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "Built $OUT"
echo "Install with: sudo apt install $OUT"
