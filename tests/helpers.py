"""Shared helpers for the Plex2iPod test suite.

Everything here is standard library only, matching the app itself.
"""

import importlib
import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# PLEX2IPOD_ROOT points the suite at a different checkout, which is how to
# check that these tests actually fail against a known-bad version.
CHECKOUT_ROOT = os.environ.get("PLEX2IPOD_ROOT") or PROJECT_ROOT

_module_cache = None


def app_module():
    """Import the plex2ipod package and return it.

    The package re-exports the names tests read. For *patching*, reach for
    the submodule where the name is actually looked up — the app binds its
    collaborators at import time, so replacing an attribute on the package
    would not affect it. See app_attr() below.
    """
    global _module_cache
    if _module_cache is None:
        if CHECKOUT_ROOT not in sys.path:
            sys.path.insert(0, CHECKOUT_ROOT)
        _module_cache = importlib.import_module("plex2ipod")
    return _module_cache


# ---------------------------------------------------------------------------
# Tk availability
# ---------------------------------------------------------------------------

_tk_state = None


def tk_available():
    """True if a Tk window can actually be created.

    Importing tkinter succeeds on a headless box; creating a root does not.
    Only the second tells us whether the GUI tests can run.
    """
    global _tk_state
    if _tk_state is None:
        if os.environ.get("PLEX2IPOD_SKIP_GUI"):
            _tk_state = False
        else:
            try:
                import tkinter
                root = tkinter.Tk()
                root.destroy()
                _tk_state = True
            except Exception:
                _tk_state = False
    return _tk_state


requires_tk = unittest.skipUnless(
    tk_available(), "no usable Tk display (headless environment)")

# Park test windows far off-screen. They must be *mapped* for
# event_generate to deliver anything, so withdraw() is not an option.
OFFSCREEN = "+4000+4000"


def destroy_tk(root):
    """Tear down a Tk root, cancelling queued after() callbacks first.

    The app's iPod heartbeat reschedules itself every few seconds. Any
    callback still queued when the interpreter goes away makes Tk print
    'invalid command name ..._poll_ipod' to stderr during teardown.
    """
    if root is None:
        return
    try:
        for handle in root.tk.call("after", "info"):
            try:
                root.after_cancel(str(handle))
            except Exception:
                pass
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


requires_ffprobe = unittest.skipUnless(
    bool(shutil.which("ffprobe")), "ffprobe not installed")

requires_ffmpeg = unittest.skipUnless(
    bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe")),
    "ffmpeg/ffprobe not installed")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeRoot:
    """Stand-in for the Tk root that runs after() callbacks inline.

    The app's worker threads marshal results back with root.after(0, fn),
    so running those synchronously makes the workers deterministic without
    needing a real event loop.
    """

    def __init__(self):
        self.delays = []

    def after(self, delay, func=None, *args):
        self.delays.append(delay)
        if func is not None:
            func(*args)


class RecordingRoot:
    """Root stand-in that records after() calls without running them.

    Needed wherever the scheduled callback re-enters the code under test.
    The iPod heartbeat reschedules itself, so running callbacks inline
    (as FakeRoot does) recurses until the stack blows.
    """

    def __init__(self):
        self.delays = []
        self.calls = []

    def after(self, delay, func=None, *args):
        self.delays.append(delay)
        self.calls.append((func, args))


class FakeVar:
    """Minimal stand-in for tk.StringVar / tk.BooleanVar."""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


def make_track(n=0, artist="Artist", album="Album", filename=None, **extra):
    """A track dict shaped like PlexClient._parse_track returns."""
    track = {
        "title": "Track %d" % n,
        "artist": artist,
        "album": album,
        "duration_ms": 100000 + n,
        "part_key": "/library/parts/%d/file.flac" % n,
        "filename": filename if filename is not None else "%02d Track.flac" % n,
        "container": "flac",
        "size": 4096,
    }
    track.update(extra)
    return track


class StubPlex:
    """A PlexClient that serves canned tracks and writes fake downloads.

    fail_keys      part_keys whose download should report failure
    cancel_after   flip app._cancel once this many downloads have succeeded,
                   simulating the user pressing Cancel mid-sync
    """

    def __init__(self, tracks=(), fail_keys=(), cancel_after=None, app=None,
                 playlists=None, section_id="3", artists=(), albums=None,
                 album_tracks=None):
        self.tracks = list(tracks)
        self.fail_keys = set(fail_keys)
        self.cancel_after = cancel_after
        self.app = app
        self.downloads = 0
        self._playlists = playlists if playlists is not None else []
        self._section_id = section_id
        self._artists = list(artists)
        # {artist_key: [album, ...]} and {album_key: [track, ...]}
        self._albums = albums or {}
        self._album_tracks = album_tracks or {}

    # -- playlist / library API --
    def get_playlists(self):
        return list(self._playlists)

    def get_music_section_id(self):
        return self._section_id

    def get_artists(self, section_id):
        return list(self._artists)

    def get_artist_albums(self, artist_key):
        return list(self._albums.get(artist_key, []))

    def get_album_tracks(self, album_key):
        return list(self._album_tracks.get(album_key, []))

    def get_playlist_tracks(self, playlist_id):
        return list(self.tracks)

    # -- media transfer --
    def download_part(self, part_key, dest_path, cancel_check=None):
        if cancel_check is not None and cancel_check():
            return False, "cancelled"
        if part_key in self.fail_keys:
            return False, "HTTP 500"
        self.downloads += 1
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp = dest_path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(b"\0" * 4096)
        if self.cancel_after and self.downloads >= self.cancel_after:
            self.app._cancel = True
        return True, tmp


class NoAudio:
    """AudioConverter stand-in reporting no ffmpeg, so downsampling is off."""

    available = False
    ffmpeg_path = None
    ffprobe_path = None

    def probe(self, path):
        return None

    def downsample(self, src, dst, timeout=300):
        return False, "ffmpeg not available"


class FailingAudio(NoAudio):
    """Reports every FLAC as 24-bit, then fails to convert it."""

    available = True

    def probe(self, path):
        return {"bit_depth": 24, "sample_rate": 96000}

    def downsample(self, src, dst, timeout=300):
        return False, "simulated ffmpeg failure"


# ---------------------------------------------------------------------------
# Fake iPod volume
# ---------------------------------------------------------------------------

class FakeIPod:
    """A temp directory shaped like a Rockbox iPod volume."""

    def __init__(self, music_folder="Music", rockbox=True):
        self.path = tempfile.mkdtemp(prefix="plex2ipod-test-")
        self.music_folder = music_folder
        os.makedirs(os.path.join(self.path, music_folder), exist_ok=True)
        if rockbox:
            os.makedirs(os.path.join(self.path, ".rockbox"), exist_ok=True)

    # -- locations --
    @property
    def music_dir(self):
        return os.path.join(self.path, self.music_folder)

    @property
    def playlist_dir(self):
        return os.path.join(self.path, "Playlists")

    # -- content --
    def add_track(self, artist="Artist", album="Album",
                  filename="00 Track.flac", size=4096):
        folder = os.path.join(self.music_dir, artist, album)
        os.makedirs(folder, exist_ok=True)
        full = os.path.join(folder, filename)
        with open(full, "wb") as fh:
            fh.write(b"\0" * size)
        return full

    def add_tracks(self, tracks):
        """Materialize a list of track dicts on the volume."""
        return [self.add_track(t["artist"], t["album"], t["filename"])
                for t in tracks]

    def write_m3u(self, name, rel_paths):
        os.makedirs(self.playlist_dir, exist_ok=True)
        full = os.path.join(self.playlist_dir, name + ".m3u")
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")
            for rel in rel_paths:
                fh.write("#EXTINF:100,entry\n%s\n" % rel)
        return full

    # -- inspection --
    def audio_files(self):
        """Every audio file on the volume, as paths relative to music_dir."""
        found = []
        for root, _dirs, files in os.walk(self.music_dir):
            for name in files:
                if name.lower().endswith((".flac", ".mp3", ".m4a")):
                    rel = os.path.relpath(os.path.join(root, name),
                                          self.music_dir)
                    found.append(rel.replace(os.sep, "/"))
        return sorted(found)

    def basenames(self):
        return sorted(os.path.basename(p) for p in self.audio_files())

    def m3u_entries(self, name):
        """The path lines of a playlist, or None if it does not exist."""
        full = os.path.join(self.playlist_dir, name + ".m3u")
        if not os.path.exists(full):
            return None
        with open(full, encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.startswith("/")]

    def m3u_raw(self, name):
        full = os.path.join(self.playlist_dir, name + ".m3u")
        if not os.path.exists(full):
            return None
        with open(full, "rb") as fh:
            return fh.read()

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


class IPodTestCase(unittest.TestCase):
    """Base class that gives each test a fresh fake iPod and the module."""

    music_folder = "Music"

    def setUp(self):
        self.p2i = app_module()
        self.ipod = FakeIPod(music_folder=self.music_folder)
        self.addCleanup(self.ipod.cleanup)

    def bare_app(self, **attrs):
        """An App with __init__ skipped and the common collaborators stubbed.

        App.__init__ builds the entire GUI, which most tests neither need
        nor want. Everything the code under test touches is injected here.
        """
        app = self.p2i.App.__new__(self.p2i.App)
        app.root = FakeRoot()
        app._cancel = False
        app._busy = False
        app._syncing = False
        app.audio = NoAudio()
        app.plex = None
        app.sync_engine = None
        app.logs = []
        app._log_msg = app.logs.append
        app._clear_log = lambda: app.logs.clear()
        app._set_progress = lambda value: None
        app._ipod_root_var = FakeVar(self.ipod.path)
        app._ipod_status_var = FakeVar("")
        app._manage_status_var = FakeVar("")
        app._update_m3u_var = FakeVar(True)
        # Capacity and pre-check state, normally set up in App.__init__.
        app._playlist_vars = {}
        app._playlist_track_cache = {}
        app._playlist_fetching = set()
        app._ipod_index = None
        app._indexed_root = None
        app._capacity = None
        app._capacity_after = None
        app._tree_partial = set()
        app._tree_pending_precheck = set()
        app._baseline_paths = set()
        app._baseline_playlists = set()
        app._prechecked = False
        for key, value in attrs.items():
            setattr(app, key, value)
        return app


class TempAppDir:
    """Redirect the app's config directory so tests never touch the real
    config.json in the project root.

    ConfigManager imported app_dir into plex2ipod.config, so that is the
    binding to replace — patching plex2ipod.app_dir would have no effect.
    """

    def __init__(self, module):
        self.config_module = module.config
        self.dir = tempfile.mkdtemp(prefix="plex2ipod-cfg-")
        self._original = self.config_module.app_dir

    def __enter__(self):
        self.config_module.app_dir = lambda: self.dir
        return self.dir

    def __exit__(self, *exc):
        self.config_module.app_dir = self._original
        shutil.rmtree(self.dir, ignore_errors=True)
        return False
