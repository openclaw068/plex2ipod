"""Manage-iPod tab: removal with busy guards and Cancel, playlist cleanup,
and empty-folder tidying.
"""

import os
import unittest

from helpers import (IPodTestCase, make_track, requires_ffmpeg,
                     requires_ffprobe)


class RemovalTestCase(IPodTestCase):
    def make_removal_app(self):
        app = self.bare_app()
        # _remove_finished rescans the Manage tree, which needs widgets.
        app.scans = []
        app._scan_ipod = lambda: app.scans.append(True)
        return app

    def populated(self, count=6):
        tracks = [make_track(n) for n in range(count)]
        paths = self.ipod.add_tracks(tracks)
        self.ipod.write_m3u(
            "Mix", ["/Music/Artist/Album/%s" % t["filename"] for t in tracks])
        return tracks, paths


class RemoveWorkerTests(RemovalTestCase):
    def test_deletes_the_requested_files(self):
        _tracks, paths = self.populated(4)
        app = self.make_removal_app()
        app._remove_worker(set(paths[:2]), False)

        self.assertEqual(self.ipod.basenames(),
                         ["02 Track.flac", "03 Track.flac"])

    def test_clears_busy_and_rescans_when_finished(self):
        _tracks, paths = self.populated(3)
        app = self.make_removal_app()
        app._busy = True
        app._remove_worker(set(paths), False)

        self.assertFalse(app._busy)
        self.assertEqual(len(app.scans), 1)

    def test_busy_is_cleared_even_if_the_worker_raises(self):
        app = self.make_removal_app()
        app._busy = True
        app._ipod_music_root = lambda: (_ for _ in ()).throw(
            RuntimeError("boom"))
        app._remove_worker({"/nonexistent/file.flac"}, False)

        self.assertFalse(app._busy)
        self.assertTrue(any("Removal error" in line for line in app.logs))

    def test_a_failed_delete_is_counted_but_does_not_abort(self):
        _tracks, paths = self.populated(3)
        app = self.make_removal_app()
        # os.remove cannot delete a directory. It holds a file so the
        # empty-folder cleanup that runs afterwards leaves it in place.
        stubborn = os.path.join(self.ipod.music_dir, "Artist", "Album",
                                "stubborn.flac")
        os.makedirs(stubborn)
        with open(os.path.join(stubborn, "keep.txt"), "wb") as fh:
            fh.write(b"x")

        app._remove_worker(set(paths) | {stubborn}, False)

        self.assertTrue(os.path.isdir(stubborn))
        self.assertTrue(any("Failed" in line for line in app.logs), app.logs)
        self.assertTrue(any("3 deleted, 1 failed" in line
                            for line in app.logs), app.logs)


class CancelTests(RemovalTestCase):
    def test_cancel_before_starting_deletes_nothing(self):
        _tracks, paths = self.populated(4)
        app = self.make_removal_app()
        app._cancel = True
        app._remove_worker(sorted(paths), True)

        self.assertEqual(len(self.ipod.basenames()), 4)
        self.assertTrue(any("cancelled" in l.lower() for l in app.logs))

    def test_cancel_midway_stops_the_loop(self):
        _tracks, paths = self.populated(6)
        app = self.make_removal_app()
        real_remove = os.remove
        state = {"n": 0}

        def cancelling_remove(path):
            real_remove(path)
            state["n"] += 1
            if state["n"] == 3:
                app._cancel = True

        os.remove = cancelling_remove
        try:
            app._remove_worker(sorted(paths), True)
        finally:
            os.remove = real_remove

        self.assertEqual(state["n"], 3)
        self.assertEqual(len(self.ipod.basenames()), 3)

    def test_cancelled_removal_keeps_surviving_tracks_in_the_playlist(self):
        _tracks, paths = self.populated(6)
        app = self.make_removal_app()
        real_remove = os.remove
        state = {"n": 0}

        def cancelling_remove(path):
            real_remove(path)
            state["n"] += 1
            if state["n"] == 3:
                app._cancel = True

        os.remove = cancelling_remove
        try:
            app._remove_worker(sorted(paths), True)
        finally:
            os.remove = real_remove

        entries = self.ipod.m3u_entries("Mix")
        remaining = self.ipod.basenames()
        self.assertEqual(len(entries), len(remaining))
        for entry in entries:
            self.assertIn(os.path.basename(entry), remaining)


class PlaylistUpdateTests(RemovalTestCase):
    def test_deleted_tracks_are_stripped_from_the_playlist(self):
        _tracks, paths = self.populated(4)
        app = self.make_removal_app()
        app._remove_worker({paths[1]}, True)

        entries = [os.path.basename(e) for e in self.ipod.m3u_entries("Mix")]
        self.assertNotIn("01 Track.flac", entries)
        self.assertEqual(len(entries), 3)

    def test_files_that_could_not_be_deleted_stay_in_the_playlist(self):
        # Stripping an entry for a file still sitting on the iPod would
        # orphan the track.
        _tracks, paths = self.populated(4)
        app = self.make_removal_app()
        real_remove = os.remove

        def flaky_remove(path):
            if path.endswith("01 Track.flac"):
                raise OSError(16, "Device or resource busy")
            real_remove(path)

        os.remove = flaky_remove
        try:
            app._remove_worker(sorted(paths), True)
        finally:
            os.remove = real_remove

        self.assertEqual(self.ipod.basenames(), ["01 Track.flac"])
        entries = [os.path.basename(e) for e in self.ipod.m3u_entries("Mix")]
        self.assertEqual(entries, ["01 Track.flac"])

    def test_playlists_are_untouched_when_the_option_is_off(self):
        _tracks, paths = self.populated(4)
        before = self.ipod.m3u_raw("Mix")
        app = self.make_removal_app()
        app._remove_worker({paths[0]}, False)
        self.assertEqual(self.ipod.m3u_raw("Mix"), before)

    def test_update_m3u_files_leaves_unrelated_entries_alone(self):
        tracks = [make_track(n) for n in range(3)]
        paths = self.ipod.add_tracks(tracks)
        self.ipod.write_m3u("Mix", [
            "/Music/Artist/Album/00 Track.flac",
            "/Music/Other/Album/keep.flac",
        ])
        app = self.bare_app()
        updated = app._update_m3u_files([paths[0]])

        self.assertEqual(updated, 1)
        self.assertEqual(self.ipod.m3u_entries("Mix"),
                         ["/Music/Other/Album/keep.flac"])

    def test_update_m3u_files_is_case_insensitive(self):
        tracks = [make_track(0)]
        paths = self.ipod.add_tracks(tracks)
        self.ipod.write_m3u("Mix", ["/music/artist/album/00 track.flac"])
        app = self.bare_app()
        app._update_m3u_files(paths)
        self.assertEqual(self.ipod.m3u_entries("Mix"), [])

    def test_update_m3u_files_handles_a_missing_playlist_dir(self):
        app = self.bare_app()
        self.assertEqual(app._update_m3u_files(["/whatever.flac"]), 0)


class EmptyFolderCleanupTests(IPodTestCase):
    def test_emptied_album_and_artist_folders_are_removed(self):
        track = make_track(0)
        path = self.ipod.add_track(track["artist"], track["album"],
                                   track["filename"])
        os.remove(path)
        app = self.bare_app()
        removed = app._cleanup_empty_dirs(self.ipod.music_dir)

        self.assertEqual(removed, 2)   # Album then Artist
        self.assertEqual(os.listdir(self.ipod.music_dir), [])

    def test_the_music_root_itself_is_never_removed(self):
        app = self.bare_app()
        app._cleanup_empty_dirs(self.ipod.music_dir)
        self.assertTrue(os.path.isdir(self.ipod.music_dir))

    def test_folders_that_still_hold_files_are_kept(self):
        self.ipod.add_track("Artist", "Album", "keep.flac")
        app = self.bare_app()
        self.assertEqual(app._cleanup_empty_dirs(self.ipod.music_dir), 0)

    def test_a_missing_root_is_handled(self):
        app = self.bare_app()
        self.assertEqual(app._cleanup_empty_dirs("/nonexistent/xyzzy"), 0)


class HumanSizeTests(IPodTestCase):
    def test_formats_each_unit(self):
        app = self.bare_app()
        self.assertEqual(app._human_size(512), "512 B")
        self.assertEqual(app._human_size(1536), "1.5 KB")
        self.assertEqual(app._human_size(1024 * 1024 * 3), "3.0 MB")
        self.assertTrue(app._human_size(1024 ** 4).endswith("TB"))


class FlacIntegrityTests(IPodTestCase):
    def test_a_missing_file_is_broken(self):
        app = self.bare_app()
        self.assertTrue(app._is_flac_broken("/nonexistent/x.flac"))

    def test_a_truncated_file_is_broken(self):
        # Caught by the size check before ffprobe is ever invoked.
        path = self.ipod.add_track(filename="tiny.flac", size=100)
        app = self.bare_app()
        self.assertTrue(app._is_flac_broken(path))

    @requires_ffprobe
    def test_a_large_file_of_garbage_is_broken(self):
        path = self.ipod.add_track(filename="garbage.flac", size=8192)
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertTrue(app._is_flac_broken(path))

    @requires_ffmpeg
    def test_a_real_flac_is_not_broken(self):
        import subprocess
        path = os.path.join(self.ipod.music_dir, "real.flac")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=2", path],
            check=True, capture_output=True, timeout=60)
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertFalse(app._is_flac_broken(path))

    @requires_ffmpeg
    def test_probe_reports_bit_depth_and_sample_rate(self):
        import subprocess
        path = os.path.join(self.ipod.music_dir, "probe.flac")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=1",
             "-sample_fmt", "s16", "-ar", "44100", path],
            check=True, capture_output=True, timeout=60)
        info = self.p2i.AudioConverter().probe(path)
        self.assertIsNotNone(info)
        self.assertEqual(info["sample_rate"], 44100)
        self.assertEqual(info["bit_depth"], 16)


if __name__ == "__main__":
    unittest.main()
