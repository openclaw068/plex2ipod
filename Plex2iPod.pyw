"""Plex2iPod \u2014 sync Plex playlists and library to a Rockbox iPod.

Launcher. The implementation lives in the plex2ipod package next to this
file; see plex2ipod/app.py for the window itself.
"""

import os
import sys

# When frozen by PyInstaller the package is bundled alongside; from source
# it sits next to this script. Make sure that directory is importable even
# if the app is launched from another working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plex2ipod.app import App

if __name__ == "__main__":
    App().run()
