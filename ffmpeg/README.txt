This folder holds the ffmpeg binaries Plex2iPod uses to downsample
24-bit FLACs to 16-bit with triangular high-pass dither.

(Sample rate is preserved as-is — the converter does not resample.)


WHAT IS AND ISN'T IN GIT
------------------------
Committed:      this README.txt only.
NOT committed:  ffmpeg.exe and ffprobe.exe (~100 MB each).

They are listed in .gitignore. A fresh clone will not have them, so you
need to fetch them once before building the .exe.


HOW TO GET THEM
---------------
Automatic (recommended) — from the project root:

    python download_ffmpeg.py

On Windows that downloads a release build and drops ffmpeg.exe and
ffprobe.exe into this folder. On Linux/macOS it just checks that ffmpeg
is installed via your package manager, which is all the app needs there.

Manual, if you'd rather do it yourself:

  1. Download "ffmpeg-release-essentials.zip" from:
        https://www.gyan.dev/ffmpeg/builds/
  2. Extract the zip.
  3. Copy ffmpeg.exe and ffprobe.exe from its 'bin' folder into THIS
     folder.


HOW THE APP FINDS THEM
----------------------
Plex2iPod resolves the paths at runtime, first match wins:

  - Running from source:  <project root>/ffmpeg/ffmpeg.exe
  - Frozen into the .exe: sys._MEIPASS/ffmpeg/ffmpeg.exe
                          (PyInstaller unpacks bundled binaries there)
  - Fallback:             a system-installed ffmpeg on PATH

That last fallback is why Linux and macOS don't need anything in this
folder — 'sudo apt install ffmpeg' (or your platform's equivalent) is
enough.

If neither is found, the app still launches; the downsample checkbox and
the "Downsample 24-bit Tracks" / "Verify & Repair" buttons are disabled.


BUILDING
--------
build_exe.bat and Plex2iPod.spec package these binaries into the final
Plex2iPod.exe via --add-binary, so the distributed .exe is self-contained
and needs no ffmpeg install on the target machine. The build will stop
with an error if the binaries are missing — run download_ffmpeg.py first.
