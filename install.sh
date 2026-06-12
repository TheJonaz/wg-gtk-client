#!/usr/bin/env bash
# Install wg-gtk-client into the current user's ~/.local (no root required).
# Uninstall with: ./install.sh --uninstall
set -euo pipefail

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

uninstall() {
    rm -f "$BIN_DIR/wg-gtk-client" "$APP_DIR/wg-gtk-client.desktop"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "Removed wg-gtk-client."
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

mkdir -p "$BIN_DIR" "$APP_DIR"
install -m 0755 "$SRC_DIR/wg-gtk-client.py" "$BIN_DIR/wg-gtk-client"
install -m 0644 "$SRC_DIR/data/wg-gtk-client.desktop" "$APP_DIR/wg-gtk-client.desktop"
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APP_DIR" 2>/dev/null || true

echo "Installed:"
echo "  $BIN_DIR/wg-gtk-client"
echo "  $APP_DIR/wg-gtk-client.desktop"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "NOTE: $BIN_DIR is not on your PATH — add it to use 'wg-gtk-client' from a shell." ;;
esac
echo "Done. Launch it from your application menu or run: wg-gtk-client"
