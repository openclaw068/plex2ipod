#!/usr/bin/env bash
# Add Plex2iPod to the Linux app menu, so it can be pinned to the dock or
# taskbar and opened like any other app. Per-user; no root needed.
#
#     bash install_linux_launcher.sh            install or update
#     bash install_linux_launcher.sh --remove   take it out again
#
# Re-run it if you move this folder: the launcher points here.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
entry="$apps/plex2ipod.desktop"
icon="$icons/plex2ipod.png"

refresh() {
    # Let the menu and dock notice the change without logging out.
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$apps" || true
    command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "${icons%/256x256/apps}" 2>/dev/null || true
}

if [ "${1:-}" = "--remove" ]; then
    rm -f "$entry" "$icon"
    refresh
    echo "Removed the Plex2iPod launcher."
    exit 0
fi

python="$(command -v python3)" || { echo "python3 not found" >&2; exit 1; }
"$python" -c "import tkinter" 2>/dev/null || {
    echo "Python's Tkinter is missing (Fedora: sudo dnf install python3-tkinter)" >&2
    exit 1
}

mkdir -p "$apps" "$icons"
install -m 644 "$here/Plex2iPod_logo.png" "$icon"

# StartupWMClass must match App.WM_CLASS in plex2ipod/app.py, or the dock
# shows the running window as a separate, generic icon.
cat > "$entry" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Plex2iPod
GenericName=iPod Sync
Comment=Sync Plex playlists and albums to a Rockbox iPod
Exec="$python" "$here/Plex2iPod.pyw"
Path=$here
Icon=plex2ipod
Terminal=false
Categories=AudioVideo;Audio;
Keywords=plex;ipod;rockbox;music;sync;
StartupWMClass=Plex2ipod
StartupNotify=true
DESKTOP
chmod 644 "$entry"

command -v desktop-file-validate >/dev/null && desktop-file-validate "$entry"
refresh
echo "Installed: $entry"
echo "Open Plex2iPod from the app menu, then right-click its icon in the"
echo "taskbar or dock and choose \"Pin to Task Manager\" (or \"Pin\")."
