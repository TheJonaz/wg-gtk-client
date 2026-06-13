#!/usr/bin/env bash
# Install wg-gtk-client system-wide: binary on PATH, desktop entry, icon and
# (optionally) a PolicyKit action. Re-run to update. Use ./install.sh --uninstall
# to remove. Needs sudo for the system paths.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(dirname "$HERE")/wg-gtk-client.py"

BIN=/usr/local/bin/wg-gtk-client
DESKTOP=/usr/share/applications/wg-gtk-client.desktop
ICON=/usr/share/icons/hicolor/scalable/apps/wg-gtk-client.svg
POLICY=/usr/share/polkit-1/actions/com.thern.wg-gtk-client.policy

uninstall() {
    echo "Removing wg-gtk-client …"
    sudo rm -f "$BIN" "$DESKTOP" "$ICON" "$POLICY"
    # remove the old broken launcher if present
    rm -f "$HOME/.local/share/applications/vpn-thern.desktop"
    sudo gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
    sudo update-desktop-database -q 2>/dev/null || true
    echo "Done."
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

echo "Installing wg-gtk-client …"
sudo install -Dm755 "$SRC" "$BIN"
sudo install -Dm644 "$HERE/wg-gtk-client.desktop" "$DESKTOP"
sudo install -Dm644 "$HERE/wg-gtk-client.svg" "$ICON"

read -r -p "Install optional PolicyKit action (passwordless control)? [y/N] " yn
if [ "${yn,,}" = "y" ]; then
    sudo install -Dm644 "$HERE/com.thern.wg-gtk-client.policy" "$POLICY"
    echo "  Policy installed (edit allow_active to 'yes' for fully passwordless)."
fi

# replace the old, broken menu launcher that pointed at ~/vpn-client
rm -f "$HOME/.local/share/applications/vpn-thern.desktop"

sudo gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
sudo update-desktop-database -q 2>/dev/null || true

echo "Done. Launch from your app menu (\"WireGuard VPN\") or run: wg-gtk-client"
