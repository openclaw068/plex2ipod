"""Where the app looks for its config file and its bundled resources.

Both differ between running from source and running as a PyInstaller
one-file build, and both must resolve relative to the project, not to
this package directory.
"""

import os
import sys
import tempfile

# The directory holding Plex2iPod.pyw, i.e. one level above this package.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    """Folder to store config.json in \u2014 next to the .exe when frozen,
    otherwise the project root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return PROJECT_ROOT


def staging_root():
    """Scratch folder for downloads and conversions, on disk.

    Not the system temp folder: on many Linux systems, Fedora included,
    /tmp lives in RAM, and a hi-res FLAC parked there competes with every
    other program for memory. Files here last only until they are on the
    iPod.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = (os.environ.get("XDG_CACHE_HOME")
                or os.path.expanduser("~/.cache"))
    return os.path.join(base, "plex2ipod")


def resource_dirs():
    """Base folders to search for bundled files (ffmpeg, the icon).

    PyInstaller unpacks bundled data into sys._MEIPASS at launch, so that
    is checked first, then the folder holding the executable.
    """
    dirs = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        dirs.append(PROJECT_ROOT)
    return dirs
