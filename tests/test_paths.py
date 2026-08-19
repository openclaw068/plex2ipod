"""Path and naming helpers: sanitize_component, ipod_rel_path, sort_key,
music_folder_name.

These run everywhere — no Tk, no network, no ffmpeg.
"""

import os
import unittest

from helpers import FakeIPod, app_module, make_track


class SanitizeComponentTests(unittest.TestCase):
    def setUp(self):
        self.sanitize = app_module().sanitize_component

    def test_replaces_every_fat_illegal_character(self):
        for char in '<>:"/\\|?*':
            with self.subTest(char=char):
                self.assertEqual(self.sanitize("a%sb" % char), "a_b")

    def test_replaces_control_characters(self):
        self.assertEqual(self.sanitize("a\x01b\x1fc"), "a_b_c")

    def test_strips_trailing_dots_and_spaces(self):
        # Windows silently drops these, which would make the written path
        # and the path in the .m3u disagree.
        self.assertEqual(self.sanitize("Album. "), "Album")
        self.assertEqual(self.sanitize("Name..."), "Name")
        self.assertEqual(self.sanitize("Name   "), "Name")

    def test_keeps_ordinary_and_unicode_names(self):
        self.assertEqual(self.sanitize("Kid A"), "Kid A")
        self.assertEqual(self.sanitize("Can’t C Me"), "Can’t C Me")
        self.assertEqual(self.sanitize("Sigur Rós"), "Sigur Rós")

    def test_empty_and_fully_stripped_names_fall_back(self):
        self.assertEqual(self.sanitize(""), "Unknown")
        self.assertEqual(self.sanitize(None), "Unknown")
        self.assertEqual(self.sanitize("   "), "Unknown")
        self.assertEqual(self.sanitize("..."), "Unknown")

    def test_result_never_contains_a_separator(self):
        # A component that grew a separator would silently create a new
        # directory level on the iPod.
        for name in ("AC/DC", "a\\b", "x/y/z"):
            with self.subTest(name=name):
                self.assertNotIn("/", self.sanitize(name))
                self.assertNotIn("\\", self.sanitize(name))


class IpodRelPathTests(unittest.TestCase):
    def setUp(self):
        self.rel = app_module().ipod_rel_path

    def test_builds_artist_album_filename(self):
        track = make_track(1, artist="Radiohead", album="Kid A",
                           filename="01 Everything.flac")
        self.assertEqual(self.rel(track),
                         "Radiohead/Kid A/01 Everything.flac")

    def test_uses_forward_slashes_regardless_of_platform(self):
        track = make_track(1)
        self.assertIn("/", self.rel(track))
        self.assertNotIn("\\", self.rel(track))

    def test_missing_artist_and_album_get_placeholders(self):
        track = make_track(1, artist="", album="")
        self.assertEqual(self.rel(track),
                         "Unknown Artist/Unknown Album/01 Track.flac")

    def test_missing_filename_is_synthesized_from_title_and_container(self):
        track = make_track(1, filename="")
        track["title"] = "Idioteque"
        track["container"] = "flac"
        self.assertEqual(self.rel(track), "Artist/Album/Idioteque.flac")

    def test_synthesized_filename_defaults_to_flac(self):
        track = make_track(1, filename="")
        track["title"] = "Untitled"
        track["container"] = ""
        self.assertTrue(self.rel(track).endswith("Untitled.flac"))

    def test_illegal_characters_are_sanitized_in_every_component(self):
        track = make_track(1, artist="AC/DC", album='Back:Black',
                           filename="01 He*ll.flac")
        self.assertEqual(self.rel(track), "AC_DC/Back_Black/01 He_ll.flac")


class SortKeyTests(unittest.TestCase):
    def setUp(self):
        self.key = app_module().sort_key

    def test_is_case_insensitive(self):
        self.assertEqual(self.key("RADIOHEAD"), self.key("radiohead"))

    def test_ignores_leading_the(self):
        self.assertEqual(self.key("The Beatles"), "beatles")
        self.assertEqual(self.key("the beatles"), "beatles")

    def test_only_strips_the_as_a_whole_word(self):
        self.assertEqual(self.key("Therapy?"), "therapy?")

    def test_handles_empty_and_none(self):
        self.assertEqual(self.key(""), "")
        self.assertEqual(self.key(None), "")

    def test_sorts_as_a_listener_would_expect(self):
        names = ["The Zombies", "amiina", "The Beatles", "Björk"]
        self.assertEqual(sorted(names, key=self.key),
                         ["amiina", "The Beatles", "Björk", "The Zombies"])


class MusicFolderNameTests(unittest.TestCase):
    def test_detects_capital_music(self):
        ipod = FakeIPod(music_folder="Music")
        self.addCleanup(ipod.cleanup)
        self.assertEqual(app_module().music_folder_name(ipod.path), "Music")

    def test_detects_lowercase_music(self):
        # Rockbox playlist entries can be case sensitive, so the real
        # on-disk casing has to win.
        ipod = FakeIPod(music_folder="music")
        self.addCleanup(ipod.cleanup)
        self.assertEqual(app_module().music_folder_name(ipod.path), "music")

    def test_falls_back_to_music_when_unreadable(self):
        self.assertEqual(
            app_module().music_folder_name("/nonexistent/path/xyzzy"), "Music")

    def test_ignores_a_file_named_music(self):
        ipod = FakeIPod(music_folder="Music")
        self.addCleanup(ipod.cleanup)
        with open(os.path.join(ipod.path, "MUSIC"), "wb") as fh:
            fh.write(b"not a directory")
        self.assertEqual(app_module().music_folder_name(ipod.path), "Music")


class LooksLikeIpodTests(unittest.TestCase):
    def setUp(self):
        self.looks = app_module().looks_like_ipod

    def test_recognizes_a_rockbox_volume(self):
        ipod = FakeIPod()
        self.addCleanup(ipod.cleanup)
        self.assertTrue(self.looks(ipod.path))

    def test_recognizes_a_music_folder_without_rockbox(self):
        ipod = FakeIPod(rockbox=False)
        self.addCleanup(ipod.cleanup)
        self.assertTrue(self.looks(ipod.path))

    def test_rejects_an_unrelated_directory(self):
        import tempfile
        empty = tempfile.mkdtemp(prefix="plex2ipod-empty-")
        self.addCleanup(lambda: __import__("shutil").rmtree(empty, True))
        self.assertFalse(self.looks(empty))

    def test_rejects_an_unreadable_path(self):
        self.assertFalse(self.looks("/nonexistent/path/xyzzy"))


if __name__ == "__main__":
    unittest.main()
