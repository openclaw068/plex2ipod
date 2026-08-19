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

    def test_a_playlist_without_extinf_lines_is_still_cleaned(self):
        # Rockbox saves bare one-path-per-line playlists. The old parser
        # only recognized #EXTINF/path pairs and left these untouched.
        tracks = [make_track(n) for n in range(3)]
        paths = self.ipod.add_tracks(tracks)
        playlist = os.path.join(self.ipod.playlist_dir, "Rockbox.m3u")
        os.makedirs(self.ipod.playlist_dir, exist_ok=True)
        with open(playlist, "w", encoding="utf-8") as fh:
            for t in tracks:
                fh.write("/Music/Artist/Album/%s\n" % t["filename"])

        app = self.bare_app()
        self.assertEqual(app._update_m3u_files([paths[1]]), 1)
        self.assertEqual(self.ipod.m3u_entries("Rockbox"),
                         ["/Music/Artist/Album/00 Track.flac",
                          "/Music/Artist/Album/02 Track.flac"])

    def test_a_mixed_playlist_keeps_its_extinf_pairing(self):
        tracks = [make_track(n) for n in range(3)]
        paths = self.ipod.add_tracks(tracks)
        os.makedirs(self.ipod.playlist_dir, exist_ok=True)
        playlist = os.path.join(self.ipod.playlist_dir, "Mixed.m3u")
        with open(playlist, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")
            fh.write("#EXTINF:1,a\n/Music/Artist/Album/00 Track.flac\n")
            fh.write("/Music/Artist/Album/01 Track.flac\n")   # bare entry
            fh.write("#EXTINF:3,c\n/Music/Artist/Album/02 Track.flac\n")

        app = self.bare_app()
        app._update_m3u_files([paths[0], paths[1]])

        with open(playlist, encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("00 Track.flac", body)
        self.assertNotIn("01 Track.flac", body)
        self.assertIn("02 Track.flac", body)
        # The #EXTINF for the removed track must go with it, and the one
        # for the surviving track must stay.
        self.assertEqual(body.count("#EXTINF"), 1)
        self.assertIn("#EXTINF:3,c", body)
        self.assertIn("#EXTM3U", body)

    def test_backslash_separators_in_a_playlist_are_matched(self):
        tracks = [make_track(0)]
        paths = self.ipod.add_tracks(tracks)
        os.makedirs(self.ipod.playlist_dir, exist_ok=True)
        playlist = os.path.join(self.ipod.playlist_dir, "Win.m3u")
        with open(playlist, "w", encoding="utf-8") as fh:
            fh.write("\\Music\\Artist\\Album\\00 Track.flac\n")
        app = self.bare_app()
        app._update_m3u_files(paths)
        with open(playlist, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "")

    def test_comments_and_blank_lines_survive(self):
        tracks = [make_track(0)]
        paths = self.ipod.add_tracks(tracks)
        os.makedirs(self.ipod.playlist_dir, exist_ok=True)
        playlist = os.path.join(self.ipod.playlist_dir, "Notes.m3u")
        with open(playlist, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n# hand written note\n\n"
                     "/Music/Artist/Album/00 Track.flac\n")
        app = self.bare_app()
        app._update_m3u_files(paths)
        with open(playlist, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("# hand written note", body)
        self.assertIn("#EXTM3U", body)
        self.assertNotIn("00 Track.flac", body)


class PlaylistScanTests(IPodTestCase):
    """Playlists live outside the music folder, so the artist/album walk
    never saw them and they could not be removed from inside the app."""

    def test_finds_m3u_files(self):
        self.ipod.write_m3u("Workout", ["/Music/a/b/c.flac"])
        self.ipod.write_m3u("Chill", ["/Music/a/b/d.flac"])
        app = self.bare_app()
        names = [n for n, _p, _s in
                 app._scan_playlists(self.ipod.playlist_dir)]
        self.assertEqual(names, ["Chill.m3u", "Workout.m3u"])

    def test_reports_each_playlist_size(self):
        self.ipod.write_m3u("Workout", ["/Music/a/b/c.flac"])
        app = self.bare_app()
        (_name, path, size), = app._scan_playlists(self.ipod.playlist_dir)
        self.assertEqual(size, os.path.getsize(path))

    def test_ignores_non_playlist_files(self):
        os.makedirs(self.ipod.playlist_dir, exist_ok=True)
        with open(os.path.join(self.ipod.playlist_dir, "notes.txt"), "w") as fh:
            fh.write("x")
        self.ipod.write_m3u("Real", ["/Music/a/b/c.flac"])
        app = self.bare_app()
        self.assertEqual(
            [n for n, _p, _s in app._scan_playlists(self.ipod.playlist_dir)],
            ["Real.m3u"])

    def test_missing_playlist_folder_yields_nothing(self):
        app = self.bare_app()
        self.assertEqual(app._scan_playlists(self.ipod.playlist_dir), [])

    def test_the_scan_worker_never_reads_a_tk_variable(self):
        """Tk variables belong to the main thread. The worker is handed the
        paths it needs; reading _ipod_root_var here raised outright."""
        self.ipod.write_m3u("Workout", ["/Music/a/b/c.flac"])
        self.ipod.add_track()

        class Exploding:
            def get(self):
                raise AssertionError(
                    "scan worker read a Tk variable off the main thread")

        app = self.bare_app()
        captured = {}
        app._populate_manage_tree = lambda *a: captured.setdefault("args", a)
        app._set_busy = lambda value: None
        app._ipod_root_var = Exploding()

        app._scan_ipod_worker(self.ipod.music_dir, self.ipod.playlist_dir)

        data, playlists, playlist_dir = captured["args"]
        self.assertEqual([n for n, _p, _s in playlists], ["Workout.m3u"])
        self.assertEqual(playlist_dir, self.ipod.playlist_dir)
        self.assertEqual(len(data), 1)

    def test_a_deleted_playlist_file_is_removed_from_disk(self):
        path = self.ipod.write_m3u("Gone", ["/Music/a/b/c.flac"])
        app = self.bare_app()
        app.scans = []
        app._scan_ipod = lambda: app.scans.append(True)
        app._remove_worker({path}, True)
        self.assertFalse(os.path.exists(path))

    def test_removing_a_playlist_does_not_corrupt_other_playlists(self):
        # The deleted path is outside the music folder, so it must not be
        # treated as a track reference when the .m3u cleanup runs.
        keep = self.ipod.write_m3u("Keep", ["/Music/Artist/Album/00 Track.flac"])
        drop = self.ipod.write_m3u("Drop", ["/Music/Artist/Album/00 Track.flac"])
        before = open(keep, "rb").read()
        app = self.bare_app()
        app.scans = []
        app._scan_ipod = lambda: app.scans.append(True)
        app._remove_worker({drop}, True)
        self.assertFalse(os.path.exists(drop))
        self.assertEqual(open(keep, "rb").read(), before)


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
