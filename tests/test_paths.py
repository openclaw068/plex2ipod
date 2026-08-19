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


class ReservedNameTests(unittest.TestCase):
    """Windows cannot create a file whose stem is a device name, extension
    or not. The failure arrives mid-sync as an opaque open() error."""

    def setUp(self):
        self.sanitize = app_module().sanitize_component

    def test_bare_device_names_are_escaped(self):
        for name in ("CON", "PRN", "AUX", "NUL", "COM1", "COM9",
                     "LPT1", "LPT9"):
            with self.subTest(name=name):
                self.assertEqual(self.sanitize(name), "_" + name)

    def test_matching_is_case_insensitive(self):
        self.assertEqual(self.sanitize("con"), "_con")
        self.assertEqual(self.sanitize("Com1"), "_Com1")

    def test_device_names_with_an_extension_are_escaped(self):
        # "CON.flac" is just as reserved as "CON".
        self.assertEqual(self.sanitize("CON.flac"), "_CON.flac")
        self.assertEqual(self.sanitize("nul.mp3"), "_nul.mp3")

    def test_names_merely_starting_with_a_device_name_are_left_alone(self):
        for name in ("CONCERT", "Console", "COM10", "LPT0", "AUXILIARY"):
            with self.subTest(name=name):
                self.assertEqual(self.sanitize(name), name)


class ComponentLengthTests(unittest.TestCase):
    """FAT32 caps one path component at 255 characters."""

    def setUp(self):
        self.p2i = app_module()
        self.sanitize = self.p2i.sanitize_component
        self.limit = self.p2i.MAX_COMPONENT_LEN

    def test_an_overlong_name_is_truncated(self):
        result = self.sanitize("x" * 400)
        self.assertLessEqual(len(result), self.limit)

    def test_truncation_keeps_the_extension(self):
        # Rockbox picks its decoder from the extension.
        result = self.sanitize("y" * 400 + ".flac")
        self.assertLessEqual(len(result), self.limit)
        self.assertTrue(result.endswith(".flac"))

    def test_a_name_at_the_limit_is_untouched(self):
        name = "z" * self.limit
        self.assertEqual(self.sanitize(name), name)

    def test_truncation_never_leaves_a_trailing_dot_or_space(self):
        for name in ("w" * 250 + "." + "v" * 40, "u" * 300 + "   "):
            with self.subTest(name=name[:20]):
                result = self.sanitize(name)
                self.assertFalse(result.endswith((".", " ")), result)

    def test_a_long_name_still_gets_reserved_escaping(self):
        result = self.sanitize("CON." + "a" * 400)
        self.assertTrue(result.startswith("_CON"))
        self.assertLessEqual(len(result), self.limit)


class PathCollisionTests(unittest.TestCase):
    """Two different Plex tracks can want the same file on the iPod."""

    def setUp(self):
        self.find = app_module().find_path_collisions

    def test_no_collisions_in_a_clean_set(self):
        tracks = [make_track(n) for n in range(5)]
        self.assertEqual(self.find(tracks), {})

    def test_the_same_track_listed_twice_is_not_a_collision(self):
        # A song appearing in two playlists dedupes; that is not a clash.
        track = make_track(1)
        self.assertEqual(self.find([track, dict(track)]), {})

    def test_two_distinct_parts_on_one_path_collide(self):
        a = make_track(1, filename="01 Track.flac")
        b = make_track(2, filename="01 Track.flac")
        collisions = self.find([a, b])
        self.assertEqual(list(collisions), ["artist/album/01 track.flac"])
        self.assertEqual(len(collisions["artist/album/01 track.flac"]), 2)

    def test_collision_detection_is_case_insensitive(self):
        a = make_track(1, artist="Radiohead", filename="01 A.flac")
        b = make_track(2, artist="radiohead", filename="01 a.flac")
        self.assertEqual(len(self.find([a, b])), 1)

    def test_same_album_title_from_different_releases_collides(self):
        a = make_track(1, album="Greatest Hits", filename="01 Song.flac")
        b = make_track(2, album="Greatest Hits", filename="01 Song.flac")
        self.assertEqual(len(self.find([a, b])), 1)

    def test_different_albums_do_not_collide(self):
        a = make_track(1, album="Kid A", filename="01 Song.flac")
        b = make_track(2, album="Amnesiac", filename="01 Song.flac")
        self.assertEqual(self.find([a, b]), {})

    def test_sanitizing_can_create_a_collision(self):
        # "AC/DC" and "AC_DC" both sanitize to AC_DC.
        a = make_track(1, artist="AC/DC", filename="01 Song.flac")
        b = make_track(2, artist="AC_DC", filename="01 Song.flac")
        self.assertEqual(len(self.find([a, b])), 1)


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
