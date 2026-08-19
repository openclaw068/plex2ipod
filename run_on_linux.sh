#!/usr/bin/env bash
# Plex2iPod launcher for Linux (Armbian / Ubuntu, incl. ARM64 boards like
# the Orange Pi 5 Plus). Run it with:   bash run_on_linux.sh
#
# It checks that the few system packages the app needs are installed, then
# launches the app from source (no build step needed on Linux).

cd "$(dirname "$0")" || exit 1

missing=()
python3 -c "import tkinter" 2>/dev/null || missing+=(python3-tk)
command -v ffmpeg    >/dev/null 2>&1 || missing+=(ffmpeg)
command -v ffprobe   >/dev/null 2>&1 || missing+=(ffmpeg)
command -v udisksctl >/dev/null 2>&1 || missing+=(udisks2)

# De-duplicate (ffmpeg may be added twice)
if [ ${#missing[@]} -ne 0 ]; then
    uniq_missing=$(printf '%s\n' "${missing[@]}" | sort -u | tr '\n' ' ')
    echo "Plex2iPod needs these packages first:"
    echo "    $uniq_missing"
    echo
    echo "Install them with:"
    echo "    sudo apt install $uniq_missing"
    exit 1
fi

exec python3 Plex2iPod.pyw
