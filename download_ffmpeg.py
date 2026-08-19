"""Fetch the ffmpeg/ffprobe binaries Plex2iPod needs for FLAC downsampling.

These are ~100 MB each, so they are NOT stored in git. Run this once after
cloning, before building the .exe:

    python download_ffmpeg.py

On Windows this downloads a release build and extracts ffmpeg.exe and
ffprobe.exe into the ffmpeg/ folder next to this script. On Linux and macOS
the app already falls back to a system-installed ffmpeg on PATH, so this
script just checks for one and tells you how to install it if missing.

Pure standard library — no pip install required.
"""

import argparse
import os
import shutil
import sys
import tempfile
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Gyan.dev publishes the canonical Windows builds that the Rockbox/ffmpeg
# community uses. "essentials" is the smaller build and includes both
# ffmpeg.exe and ffprobe.exe with the FLAC encoder and swr resampler.
WINDOWS_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG_DIR = os.path.join(HERE, "ffmpeg")

WANTED = ("ffmpeg.exe", "ffprobe.exe")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def already_present():
    """True if both binaries are already sitting in ffmpeg/."""
    return all(os.path.isfile(os.path.join(FFMPEG_DIR, n)) for n in WANTED)


def download(url, dest):
    """Stream a URL to a file with a simple progress line."""
    req = Request(url, headers={"User-Agent": "Plex2iPod-setup"})
    with urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(262144)  # 256 KB
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if total:
                    pct = got * 100 // total
                    print(f"\r  Downloading... {pct:3d}%  "
                          f"({human(got)} / {human(total)})", end="", flush=True)
                else:
                    print(f"\r  Downloading... {human(got)}", end="", flush=True)
    print()
    return got


def extract(zip_path):
    """Pull just ffmpeg.exe and ffprobe.exe out of the release zip.

    The archive nests them under a versioned folder, e.g.
    ffmpeg-7.1-essentials_build/bin/ffmpeg.exe — so match on the tail
    rather than a fixed path.
    """
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    found = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if base in WANTED and "/bin/" in name:
                dest = os.path.join(FFMPEG_DIR, base)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                size = os.path.getsize(dest)
                print(f"  Extracted {base}  ({human(size)})")
                found.append(base)
    missing = [n for n in WANTED if n not in found]
    if missing:
        raise RuntimeError(
            "the archive did not contain: " + ", ".join(missing))


def do_windows(force):
    if already_present() and not force:
        print("ffmpeg.exe and ffprobe.exe are already in ffmpeg/ — nothing "
              "to do.\nRe-run with --force to download them again.")
        return 0

    print(f"Fetching ffmpeg from:\n  {WINDOWS_ZIP_URL}\n")
    tmp_dir = tempfile.mkdtemp(prefix="plex2ipod-ffmpeg-")
    zip_path = os.path.join(tmp_dir, "ffmpeg.zip")
    try:
        download(WINDOWS_ZIP_URL, zip_path)
        print("  Extracting...")
        extract(zip_path)
    except (URLError, HTTPError, OSError, zipfile.BadZipFile,
            RuntimeError) as e:
        print(f"\nERROR: {e}\n", file=sys.stderr)
        print("Manual fallback:", file=sys.stderr)
        print("  1. Download ffmpeg-release-essentials.zip from",
              file=sys.stderr)
        print(f"     {WINDOWS_ZIP_URL}", file=sys.stderr)
        print("  2. Extract it.", file=sys.stderr)
        print("  3. Copy ffmpeg.exe and ffprobe.exe from its bin/ folder",
              file=sys.stderr)
        print(f"     into {FFMPEG_DIR}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\nDone. You can now run build_exe.bat to build Plex2iPod.exe.")
    return 0


def do_unix():
    """Linux/macOS don't need bundled binaries — the app finds ffmpeg on
    PATH. Just report whether it's there."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        print("ffmpeg and ffprobe are already installed:")
        print(f"  {ffmpeg}")
        print(f"  {ffprobe}")
        print("\nPlex2iPod picks these up from PATH automatically. "
              "Nothing to download.")
        return 0

    print("ffmpeg / ffprobe were not found on your PATH.\n")
    print("Plex2iPod uses them to downsample 24-bit FLACs to 16-bit.")
    print("Install them with your package manager:\n")
    print("  Debian / Ubuntu / Armbian:  sudo apt install ffmpeg")
    print("  Fedora:                     sudo dnf install ffmpeg")
    print("  Arch:                       sudo pacman -S ffmpeg")
    print("  macOS (Homebrew):           brew install ffmpeg")
    print("\nThe app still runs without them — the downsample features are "
          "just disabled.")
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="Download the ffmpeg binaries Plex2iPod needs.")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the binaries are already there")
    args = ap.parse_args()

    if os.name == "nt":
        return do_windows(args.force)
    return do_unix()


if __name__ == "__main__":
    sys.exit(main())
