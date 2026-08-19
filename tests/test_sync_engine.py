"""SyncEngine: destination layout, .m3u path form, sync planning, and the
music-folder-name cache that keeps a large sync off the volume root.
"""

import os
import unittest

from helpers import FakeIPod, IPodTestCase, app_module, make_track


class DestinationLayoutTests(IPodTestCase):
    def setUp(self):
        super().setUp()
        self.engine = self.p2i.SyncEngine(self.ipod.path)

    def test_dest_path_is_music_artist_album_file(self):
        track = make_track(1, artist="Radiohead", album="Kid A",
                           filename="01 Everything.flac")
        self.assertEqual(
            self.engine.dest_path(track),
            os.path.join(self.ipod.path, "Music", "Radiohead", "Kid A",
                         "01 Everything.flac"))

    def test_dest_path_uses_native_separators(self):
        track = make_track(1)
        self.assertNotIn("/" if os.sep == "\\" else "\\",
                         self.engine.dest_path(track))

    def test_m3u_path_is_absolute_from_the_volume_root(self):
        # Rockbox resolves playlist entries from the root of the volume,
        # always with forward slashes.
        track = make_track(1, artist="Radiohead", album="Kid A",
                           filename="01 Everything.flac")
        self.assertEqual(self.engine.m3u_path(track),
                         "/Music/Radiohead/Kid A/01 Everything.flac")
        self.assertNotIn("\\", self.engine.m3u_path(track))

    def test_playlist_dir_is_at_the_volume_root(self):
        self.assertEqual(self.engine.ipod_playlist_dir(),
                         os.path.join(self.ipod.path, "Playlists"))


class LowercaseMusicFolderTests(IPodTestCase):
    """A volume whose folder is 'music' must not be addressed as 'Music'."""

    music_folder = "music"

    def test_dest_and_m3u_paths_follow_the_real_casing(self):
        engine = self.p2i.SyncEngine(self.ipod.path)
        track = make_track(1)
        self.assertEqual(engine.music_folder, "music")
        self.assertEqual(engine.m3u_path(track),
                         "/music/Artist/Album/01 Track.flac")
        self.assertTrue(
            engine.dest_path(track).startswith(
                os.path.join(self.ipod.path, "music") + os.sep))


class MusicFolderCacheTests(IPodTestCase):
    """The folder casing is resolved once, not per track.

    music_folder_name() lists the volume root. Calling it from dest_path()
    and m3u_path() meant thousands of listings per sync on a slow USB
    device, and kept a hard-drive iPod spinning.
    """

    def _count_root_listings(self, func):
        real_listdir = os.listdir
        root = os.path.normpath(self.ipod.path)
        calls = []

        def counting(path="."):
            try:
                if os.path.normpath(path) == root:
                    calls.append(path)
            except (TypeError, ValueError):
                pass
            return real_listdir(path)

        os.listdir = counting
        try:
            func()
        finally:
            os.listdir = real_listdir
        return len(calls)

    def test_construction_lists_the_root_once(self):
        count = self._count_root_listings(
            lambda: self.p2i.SyncEngine(self.ipod.path))
        self.assertEqual(count, 1)

    def test_path_building_never_lists_the_root(self):
        engine = self.p2i.SyncEngine(self.ipod.path)
        tracks = [make_track(n) for n in range(200)]

        def build():
            for track in tracks:
                engine.dest_path(track)
                engine.m3u_path(track)

        self.assertEqual(self._count_root_listings(build), 0)

    def test_generating_a_playlist_never_lists_the_root(self):
        engine = self.p2i.SyncEngine(self.ipod.path)
        tracks = [make_track(n) for n in range(100)]
        count = self._count_root_listings(
            lambda: engine.generate_m3u("Big", tracks))
        self.assertEqual(count, 0)


class SyncPlanTests(IPodTestCase):
    def test_splits_tracks_into_to_copy_and_already_present(self):
        engine = self.p2i.SyncEngine(self.ipod.path)
        tracks = [make_track(n) for n in range(4)]
        self.ipod.add_tracks(tracks[:2])

        to_copy, already = engine.build_sync_plan(tracks)

        self.assertEqual([t["filename"] for t in already],
                         ["00 Track.flac", "01 Track.flac"])
        self.assertEqual([t["filename"] for t in to_copy],
                         ["02 Track.flac", "03 Track.flac"])

    def test_everything_is_to_copy_on_an_empty_volume(self):
        engine = self.p2i.SyncEngine(self.ipod.path)
        tracks = [make_track(n) for n in range(3)]
        to_copy, already = engine.build_sync_plan(tracks)
        self.assertEqual(len(to_copy), 3)
        self.assertEqual(already, [])


class GenerateM3uTests(IPodTestCase):
    def setUp(self):
        super().setUp()
        self.engine = self.p2i.SyncEngine(self.ipod.path)

    def test_writes_extm3u_header_and_one_pair_per_track(self):
        tracks = [make_track(n) for n in range(3)]
        self.engine.generate_m3u("Mix", tracks)
        with open(os.path.join(self.ipod.playlist_dir, "Mix.m3u"),
                  encoding="utf-8") as fh:
            lines = [l.rstrip("\n") for l in fh]
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(len(lines), 1 + 2 * 3)
        self.assertTrue(lines[1].startswith("#EXTINF:"))
        self.assertEqual(lines[2], "/Music/Artist/Album/00 Track.flac")

    def test_extinf_carries_seconds_and_artist_title(self):
        track = make_track(1, artist="Radiohead")
        track["duration_ms"] = 254000
        track["title"] = "Idioteque"
        self.engine.generate_m3u("Mix", [track])
        entries = [l.strip() for l in
                   open(os.path.join(self.ipod.playlist_dir, "Mix.m3u"),
                        encoding="utf-8")]
        self.assertIn("#EXTINF:254,Radiohead - Idioteque", entries)

    def test_label_falls_back_to_title_when_artist_is_blank(self):
        track = make_track(1, artist="")
        track["title"] = "Untitled"
        self.engine.generate_m3u("Mix", [track])
        text = open(os.path.join(self.ipod.playlist_dir, "Mix.m3u"),
                    encoding="utf-8").read()
        self.assertIn("#EXTINF:100,Untitled", text)

    def test_creates_the_playlist_directory_if_absent(self):
        self.assertFalse(os.path.exists(self.ipod.playlist_dir))
        self.engine.generate_m3u("Mix", [make_track(0)])
        self.assertTrue(os.path.isdir(self.ipod.playlist_dir))

    def test_playlist_name_is_sanitized(self):
        self.engine.generate_m3u('Rock/Metal: "best"', [make_track(0)])
        written = os.listdir(self.ipod.playlist_dir)
        self.assertEqual(written, ["Rock_Metal_ _best_.m3u"])

    def test_is_written_as_utf8(self):
        track = make_track(1, artist="Sigur Rós", album="Ágætis byrjun")
        self.engine.generate_m3u("Mix", [track])
        raw = self.ipod.m3u_raw("Mix")
        self.assertIn("Sigur Rós".encode("utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
