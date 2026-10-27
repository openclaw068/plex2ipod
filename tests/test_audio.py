"""AudioConverter binary discovery.

The bundled ffmpeg binaries are Windows .exe files. A Linux checkout can
easily end up holding them — a shared folder, a synced directory, or a
clone made on Windows. Selecting those on Linux makes AudioConverter
report itself available while every call fails at spawn time, which in
turn makes Verify & Repair consider the entire library broken.
"""

import os
import shutil
import stat
import subprocess
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
    """_locate searches the folders resource_dirs() reports, so these tests
    point that at a throwaway directory holding a fake bundle."""

    def setUp(self):
        self.p2i = app_module()
        self.dir = tempfile.mkdtemp(prefix="plex2ipod-bundle-")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.bundle = os.path.join(self.dir, "ffmpeg")
        os.makedirs(self.bundle)
        # audio.py imported resource_dirs by name, so that is the binding
        # to replace.
        self._real_dirs = self.p2i.audio.resource_dirs
        self.p2i.audio.resource_dirs = lambda: [self.dir]
        self.addCleanup(self._restore)

    def _restore(self):
        self.p2i.audio.resource_dirs = self._real_dirs

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


class IpodTargetTests(unittest.TestCase):
    """What gets converted, and to what. The iPod plays at most 48 kHz."""

    def setUp(self):
        self.audio = app_module().audio

    def test_sample_rates_drop_within_their_family(self):
        rate = self.audio.ipod_sample_rate
        self.assertIsNone(rate(44100))
        self.assertIsNone(rate(48000))
        self.assertEqual(rate(88200), 44100)
        self.assertEqual(rate(176400), 44100)
        self.assertEqual(rate(352800), 44100)
        self.assertEqual(rate(96000), 48000)
        self.assertEqual(rate(192000), 48000)
        self.assertIsNone(rate(None))

    def test_what_needs_converting(self):
        needs = self.audio.needs_conversion
        self.assertFalse(needs({"bit_depth": 16, "sample_rate": 44100}))
        self.assertFalse(needs({"bit_depth": 16, "sample_rate": 48000}))
        self.assertTrue(needs({"bit_depth": 24, "sample_rate": 44100}))
        self.assertTrue(needs({"bit_depth": 16, "sample_rate": 96000}))
        self.assertFalse(needs(None))

    def test_labels(self):
        info = {"bit_depth": 24, "sample_rate": 96000}
        self.assertEqual(self.audio.describe(info), "24-bit/96 kHz")
        self.assertEqual(self.audio.describe(self.audio.converted_info(info)),
                         "16-bit/48 kHz")
        self.assertEqual(self.audio.describe(self.audio.converted_info(
            {"bit_depth": 24, "sample_rate": 44100})), "16-bit/44.1 kHz")


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class RealResampleTests(unittest.TestCase):
    def setUp(self):
        self.p2i = app_module()
        self.dir = tempfile.mkdtemp(prefix="plex2ipod-rs-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def make(self, rate, fmt="s32", bits="24"):
        path = os.path.join(self.dir, "src-%d.flac" % rate)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=1000:duration=5", "-ar", str(rate),
                        "-sample_fmt", fmt, "-bits_per_raw_sample", bits,
                        path], check=True)
        return path

    def convert(self, src, rate):
        audio = self.p2i.AudioConverter()
        dst = os.path.join(self.dir, "out.flac")
        ok, err = audio.downsample(src, dst, sample_rate=rate)
        self.assertTrue(ok, err)
        return audio, dst

    def test_24_bit_96k_becomes_16_bit_48k_and_much_smaller(self):
        src = self.make(96000)
        audio, dst = self.convert(src, 48000)
        self.assertEqual(audio.probe(dst),
                         {"bit_depth": 16, "sample_rate": 48000})
        self.assertLess(os.path.getsize(dst), os.path.getsize(src) / 2)

    def test_the_length_is_kept(self):
        src = self.make(88200)
        _audio, dst = self.convert(src, 44100)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", dst],
                             capture_output=True, text=True).stdout
        self.assertAlmostEqual(float(out), 5.0, delta=0.05)

    def test_without_a_rate_the_rate_is_kept(self):
        src = self.make(44100)
        audio, dst = self.convert(src, None)
        self.assertEqual(audio.probe(dst),
                         {"bit_depth": 16, "sample_rate": 44100})
