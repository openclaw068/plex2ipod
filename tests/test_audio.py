"""AudioConverter binary discovery.

The bundled ffmpeg binaries are Windows .exe files. A Linux checkout can
easily end up holding them — a shared folder, a synced directory, or a
clone made on Windows. Selecting those on Linux makes AudioConverter
report itself available while every call fails at spawn time, which in
turn makes Verify & Repair consider the entire library broken.
"""

import os
import stat
import tempfile
import unittest

from helpers import app_module

WINDOWS = os.name == "nt"


class RunnableTests(unittest.TestCase):
    def setUp(self):
        self.p2i = app_module()
        self.dir = tempfile.mkdtemp(prefix="plex2ipod-bin-")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def make_file(self, name, executable=False):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(b"MZ\x90\x00")          # DOS header, like a real .exe
        if executable:
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_a_missing_path_is_not_runnable(self):
        self.assertFalse(
            self.p2i.AudioConverter._runnable(
                os.path.join(self.dir, "nope")))

    def test_a_directory_is_not_runnable(self):
        self.assertFalse(self.p2i.AudioConverter._runnable(self.dir))

    @unittest.skipIf(WINDOWS, "the executable bit is a POSIX concept")
    def test_a_non_executable_file_is_not_runnable(self):
        path = self.make_file("ffprobe.exe", executable=False)
        self.assertFalse(self.p2i.AudioConverter._runnable(path))

    @unittest.skipIf(WINDOWS, "the executable bit is a POSIX concept")
    def test_an_executable_file_is_runnable(self):
        path = self.make_file("ffprobe", executable=True)
        self.assertTrue(self.p2i.AudioConverter._runnable(path))


class LocateTests(unittest.TestCase):
    """_locate resolves relative to the app file, so these tests move the
    module's __file__ into a throwaway directory holding a fake bundle."""

    def setUp(self):
        self.p2i = app_module()
        self.dir = tempfile.mkdtemp(prefix="plex2ipod-bundle-")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.bundle = os.path.join(self.dir, "ffmpeg")
        os.makedirs(self.bundle)
        self._real_file = self.p2i.__file__
        self.p2i.__file__ = os.path.join(self.dir, "Plex2iPod.pyw")
        self.addCleanup(self._restore)

    def _restore(self):
        self.p2i.__file__ = self._real_file

    def drop(self, name, executable=False):
        path = os.path.join(self.bundle, name)
        with open(path, "wb") as fh:
            fh.write(b"MZ\x90\x00")
        if executable:
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    @unittest.skipIf(WINDOWS, "on Windows the .exe is the correct choice")
    def test_bundled_windows_exe_is_ignored_on_linux(self):
        """The regression this was written for."""
        self.drop("ffprobe.exe")
        self.drop("ffmpeg.exe")
        located = self.p2i.AudioConverter._locate("ffprobe")
        self.assertNotEqual(located, os.path.join(self.bundle, "ffprobe.exe"))
        if located is not None:
            self.assertFalse(located.endswith(".exe"))

    @unittest.skipIf(WINDOWS, "on Windows the .exe is the correct choice")
    def test_constructor_ignores_a_windows_only_bundle_on_linux(self):
        """The exact failure path: a Linux checkout carrying ffmpeg.exe and
        ffprobe.exe used to report available=True and then fail every call
        with 'Permission denied'."""
        self.drop("ffprobe.exe")
        self.drop("ffmpeg.exe")
        audio = self.p2i.AudioConverter()

        for path in (audio.ffmpeg_path, audio.ffprobe_path):
            if path is not None:
                self.assertFalse(
                    path.endswith(".exe"),
                    "picked a Windows binary on this platform: %s" % path)

        if audio.available:
            import subprocess
            result = subprocess.run([audio.ffprobe_path, "-version"],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0,
                             "reported available but does not run")

    @unittest.skipIf(WINDOWS, "posix-only naming")
    def test_a_bundled_posix_binary_is_used(self):
        expected = self.drop("ffprobe", executable=True)
        self.assertEqual(self.p2i.AudioConverter._locate("ffprobe"), expected)

    @unittest.skipIf(WINDOWS, "posix-only naming")
    def test_a_non_executable_bundled_binary_is_skipped(self):
        self.drop("ffprobe", executable=False)
        located = self.p2i.AudioConverter._locate("ffprobe")
        self.assertNotEqual(located, os.path.join(self.bundle, "ffprobe"))

    def test_falls_back_to_path_when_the_bundle_is_empty(self):
        import shutil
        on_path = shutil.which("ffprobe.exe" if WINDOWS else "ffprobe")
        located = self.p2i.AudioConverter._locate("ffprobe")
        self.assertEqual(located, on_path)

    def test_returns_none_when_nothing_is_available(self):
        import shutil
        real_which = shutil.which
        shutil.which = lambda *a, **k: None
        try:
            self.assertIsNone(self.p2i.AudioConverter._locate("ffprobe"))
        finally:
            shutil.which = real_which


class AvailabilityTests(unittest.TestCase):
    """`available` must mean the binaries can actually be executed."""

    def setUp(self):
        self.p2i = app_module()

    def test_available_implies_both_paths_are_runnable(self):
        audio = self.p2i.AudioConverter()
        if not audio.available:
            self.skipTest("no ffmpeg on this machine")
        for path in (audio.ffmpeg_path, audio.ffprobe_path):
            self.assertTrue(self.p2i.AudioConverter._runnable(path),
                            "%s is reported available but is not runnable"
                            % path)

    def test_available_binaries_actually_run(self):
        import subprocess
        audio = self.p2i.AudioConverter()
        if not audio.available:
            self.skipTest("no ffmpeg on this machine")
        result = subprocess.run([audio.ffprobe_path, "-version"],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
