"""Where the app looks for its config file and its bundled resources.

Both differ between running from source and running as a PyInstaller
one-file build, and both must resolve relative to the project, not to
this package directory.
"""

import os
import sys

# The directory holding Plex2iPod.pyw, i.e. one level above this package.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    """Folder to store config.json in \u2014 next to the .exe when frozen,
    otherwise the project root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return PROJECT_ROOT


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
