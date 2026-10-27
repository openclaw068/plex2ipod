"""Manage-iPod tab: removal with busy guards and Cancel, playlist cleanup,
and empty-folder tidying.
"""

import os
import unittest
from unittest import mock

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

    def make_flac(self, name, seconds=30, extra=()):
        import subprocess
        path = os.path.join(self.ipod.music_dir, name)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=%d" % seconds]
            + list(extra) + [path],
            check=True, capture_output=True, timeout=60)
        return path

    @requires_ffmpeg
    def test_a_file_cut_short_mid_download_is_broken(self):
        # The header still claims the full length, so ffprobe alone
        # passes this file; only decoding its tail shows the audio is gone.
        path = self.make_flac("cut.flac")
        with open(path, "rb") as fh:
            data = fh.read()
        with open(path, "wb") as fh:
            fh.write(data[:len(data) // 3])
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertTrue(app._is_flac_broken(path))

    @requires_ffmpeg
    def test_an_appended_id3v1_tag_is_not_damage(self):
        # Found on a real iPod: taggers append a 128-byte "TAG" block after
        # the FLAC data. ffmpeg complains about it, but the audio is whole.
        path = self.make_flac("tagged.flac")
        with open(path, "ab") as fh:
            fh.write(b"TAG" + b"\0" * 125)
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertFalse(app._is_flac_broken(path))

    @requires_ffmpeg
    def test_a_file_missing_its_last_seconds_is_broken(self):
        # Also from the real iPod: a copy cut off ~3 s before the end.
        path = self.make_flac("ending.flac")
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            data = fh.read()
        with open(path, "wb") as fh:
            fh.write(data[:size - size // 10])
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertTrue(app._is_flac_broken(path))

    @requires_ffmpeg
    def test_a_file_much_shorter_than_plex_says_is_broken(self):
        path = self.make_flac("short.flac", seconds=10)
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertTrue(app._is_flac_broken(path, expected_ms=30000))

    @requires_ffmpeg
    def test_a_length_matching_plex_is_fine(self):
        path = self.make_flac("ok.flac", seconds=10)
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertFalse(app._is_flac_broken(path, expected_ms=10400))

    @requires_ffmpeg
    def test_a_healthy_hi_res_file_is_not_broken(self):
        path = self.make_flac("hires.flac", extra=[
            "-ar", "96000", "-sample_fmt", "s32",
            "-bits_per_raw_sample", "24"])
        app = self.bare_app(audio=self.p2i.AudioConverter())
        self.assertFalse(app._is_flac_broken(path))

    @requires_ffmpeg
    def test_embedded_cover_art_is_not_mistaken_for_damage(self):
        import subprocess
        art = os.path.join(self.ipod.music_dir, "cover.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=300x300", "-frames:v", "1", art],
                       check=True, capture_output=True, timeout=60)
        path = self.make_flac("art.flac", extra=[
            "-i", art, "-map", "0:a", "-map", "1:v", "-c:v", "png",
            "-disposition:v", "attached_pic"])
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


class OrganizeTests(IPodTestCase):
    """Organize moves hand-copied music into Artist/Album/track so the app
    recognises it. Renames only: nothing downloaded, nothing deleted."""

    RELEASE = "Pink.Floyd.-.The.Wall.(2007.Remaster).[FLAC].88-xpost"

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(self.p2i.app.messagebox, "showinfo")
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self, *parts, size=4096):
        path = os.path.join(self.ipod.music_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"\1" * size)
        return path

    def track(self, n, *server_folders, album="The Wall", size=4096):
        name = "%02d.Pink.Floyd.-.Song.flac" % n
        return make_track(n, artist="Pink Floyd", album=album, filename=name,
                          size=size, server_path="D:/Music/" + "/".join(
                              server_folders + (name,)))

    def run_organize(self, tracks, answer=True):
        app = self.bare_app()
        app.plex = object()
        app._section_id = "3"
        library = {self.p2i.ipod_rel_path(t).lower(): t for t in tracks}
        app._build_plex_index = lambda: (library, None)
        app._ask_yes_no = mock.Mock(return_value=answer)
        app._organize_finished = lambda moved: setattr(app, "moved", moved)
        app._organize_worker(self.ipod.path, self.ipod.music_dir)
        return app

    def dest(self, track):
        return os.path.join(self.ipod.music_dir,
                            *self.p2i.ipod_rel_path(track).split("/"))

    def test_hand_copied_tracks_are_moved_into_place(self):
        tracks = [self.track(n, self.RELEASE) for n in (1, 2)]
        for t in tracks:
            self.put(self.RELEASE, t["filename"])
        app = self.run_organize(tracks)
        self.assertEqual(app.moved, 2)
        for t in tracks:
            self.assertTrue(os.path.exists(self.dest(t)))
        self.assertFalse(os.path.exists(
            os.path.join(self.ipod.music_dir, self.RELEASE)))

    def test_playlists_follow_the_moved_files(self):
        t = self.track(1, self.RELEASE)
        self.put(self.RELEASE, t["filename"])
        self.ipod.write_m3u("Old", ["/Music/%s/%s" % (self.RELEASE,
                                                      t["filename"])])
        self.run_organize([t])
        self.assertEqual(self.ipod.m3u_entries("Old"),
                         ["/Music/" + self.p2i.ipod_rel_path(t)])

    def test_tracks_already_in_place_are_untouched(self):
        t = make_track(1, artist="A", album="B", filename="x.flac")
        path = self.ipod.add_track("A", "B", "x.flac")
        app = self.run_organize([t])
        self.assertTrue(os.path.exists(path))
        app._ask_yes_no.assert_not_called()

    def test_music_plex_does_not_know_stays_put(self):
        stranger = self.put("Bootlegs", "live.flac")
        t = self.track(1, self.RELEASE)
        self.put(self.RELEASE, t["filename"])
        self.run_organize([t])
        self.assertTrue(os.path.exists(stranger))

    def test_declining_moves_nothing(self):
        t = self.track(1, self.RELEASE)
        src = self.put(self.RELEASE, t["filename"])
        app = self.run_organize([t], answer=False)
        self.assertTrue(os.path.exists(src))
        self.assertEqual(app.moved, 0)

    def test_a_file_in_place_for_one_track_is_never_taken_by_another(self):
        owner = make_track(1, artist="A", album="One", filename="x.flac")
        twin = make_track(2, artist="B", album="Two", filename="x.flac")
        path = self.ipod.add_track("A", "One", "x.flac")
        self.run_organize([owner, twin])
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(self.dest(twin)))

    def test_non_music_leftovers_are_kept(self):
        t = self.track(1, self.RELEASE)
        self.put(self.RELEASE, t["filename"])
        nfo = self.put(self.RELEASE, "release.nfo")
        self.run_organize([t])
        self.assertTrue(os.path.exists(nfo))

    def test_the_log_reports_the_plan(self):
        t = self.track(1, self.RELEASE)
        self.put(self.RELEASE, t["filename"])
        self.put("Bootlegs", "live.flac")
        app = self.run_organize([t])
        log = "\n".join(app.logs)
        self.assertIn("0 track(s) already in place, 1 to move, "
                      "1 not found in Plex", log)
        self.assertIn("Organize complete: 1 track(s) moved", log)


def _real_flac(path, rate=96000, fmt="s32", bits="24", seconds=4):
    import subprocess
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=%d" % seconds,
                    "-ar", str(rate), "-sample_fmt", fmt,
                    "-bits_per_raw_sample", bits, path],
                   check=True, capture_output=True, timeout=60)
    return path


class ShrinkHiResTests(IPodTestCase):
    """Shrink Hi-Res replaces every FLAC above 16-bit or 48 kHz with an
    iPod-sized copy, from Plex when it has the track, else from the copy
    on the iPod. Plex is optional."""

    def setUp(self):
        super().setUp()
        for name in ("showinfo", "showwarning"):
            patcher = mock.patch.object(self.p2i.app.messagebox, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_shrink(self, plex_index=None, answer=True):
        app = self.bare_app(audio=self.p2i.AudioConverter())
        app._manage_status_var = mock.Mock()
        app._ask_yes_no = mock.Mock(return_value=answer)
        if plex_index is not None:
            app.plex = object()
            app._section_id = "3"
            app._build_plex_index = lambda: (plex_index, None)
        app._redownload_track = mock.Mock(return_value=(True, None))
        app._downsample_existing_worker(self.ipod.music_dir)
        return app

    def path(self, *parts):
        return os.path.join(self.ipod.music_dir, *parts)

    @requires_ffmpeg
    def test_without_plex_the_copy_on_the_ipod_is_converted(self):
        song = _real_flac(self.path("Release-xpost", "01.flac"))
        before = os.path.getsize(song)
        app = self.run_shrink()
        self.assertEqual(app.audio.probe(song),
                         {"bit_depth": 16, "sample_rate": 48000})
        self.assertLess(os.path.getsize(song), before / 2)
        self.assertIn("Shrink complete: 1/1 replaced", "\n".join(app.logs))

    @requires_ffmpeg
    def test_a_16_bit_96k_file_is_shrunk(self):
        song = _real_flac(self.path("A", "B", "01.flac"), fmt="s16",
                          bits="16")
        app = self.run_shrink()
        self.assertEqual(app.audio.probe(song)["sample_rate"], 48000)

    @requires_ffmpeg
    def test_a_track_plex_has_is_fetched_fresh_instead(self):
        _real_flac(self.path("Artist", "Album", "01.flac"))
        track = make_track(1, filename="01.flac")
        app = self.run_shrink({"artist/album/01.flac": track})
        app._redownload_track.assert_called_once()
        self.assertIs(app._redownload_track.call_args[0][0], track)

    @requires_ffmpeg
    def test_an_ipod_sized_file_is_left_alone(self):
        song = _real_flac(self.path("A", "B", "01.flac"), rate=44100,
                          fmt="s16", bits="16")
        before = os.path.getsize(song)
        app = self.run_shrink()
        app._ask_yes_no.assert_not_called()
        self.assertEqual(os.path.getsize(song), before)

    @requires_ffmpeg
    def test_a_damaged_source_is_kept_not_replaced_by_a_short_copy(self):
        song = _real_flac(self.path("A", "B", "01.flac"), seconds=30)
        with open(song, "rb") as fh:
            data = fh.read()
        with open(song, "wb") as fh:
            fh.write(data[:len(data) // 2])
        before = os.path.getsize(song)
        app = self.run_shrink()
        self.assertEqual(os.path.getsize(song), before)
        self.assertIn("looks damaged", "\n".join(app.logs))

    @requires_ffmpeg
    def test_declining_changes_nothing(self):
        song = _real_flac(self.path("A", "B", "01.flac"))
        before = os.path.getsize(song)
        self.run_shrink(answer=False)
        self.assertEqual(os.path.getsize(song), before)

    @requires_ffmpeg
    def test_no_local_copies_are_left_behind(self):
        _real_flac(self.path("A", "B", "01.flac"))
        _real_flac(self.path("A", "B", "02.flac"))
        self.run_shrink()
        self.assertEqual(os.listdir(self.staging_root), [])


class FindClutterTests(IPodTestCase):
    def put(self, *parts):
        path = os.path.join(self.ipod.music_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x" * 10)
        return path

    def clutter(self):
        found = self.p2i.sync.find_clutter(self.ipod.music_dir)
        return [(os.path.relpath(p, self.ipod.music_dir).replace(os.sep, "/"),
                 n, size) for p, n, size in found]

    def test_a_release_folder_left_with_only_leftovers(self):
        self.put("Release-xpost", "release.nfo")
        self.put("Release-xpost", "release.sfv")
        self.put("Release-xpost", "Scans", "front.jpg")
        self.assertEqual(self.clutter(), [("Release-xpost", 3, 30)])

    def test_download_tool_folders_inside_an_album_with_music(self):
        self.put("Release", "01.flac")
        self.put("Release", "cover.jpg")
        self.put("Release", "__ADMIN__", "SABnzbd_nzf_abc")
        self.assertEqual(self.clutter(), [("Release/__ADMIN__", 1, 10)])

    def test_music_at_any_depth_protects_its_folders(self):
        self.put("Box", "release.nfo")
        self.put("Box", "CD1", "01.mp3")
        self.assertEqual(self.clutter(), [])

    def test_every_audio_kind_counts_as_music(self):
        for ext in ("flac", "MP3", "m4a", "ogg", "opus", "wav", "ape", "wv"):
            self.put("K-" + ext, "track." + ext)
        self.assertEqual(self.clutter(), [])

    def test_an_empty_device_has_nothing(self):
        self.assertEqual(self.clutter(), [])


class CleanUpTests(IPodTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(self.p2i.app.messagebox, "showinfo")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_clean(self, answer=True):
        app = self.bare_app()
        app._ask_yes_no = mock.Mock(return_value=answer)
        app._clean_up_worker(self.ipod.music_dir)
        return app

    def test_leftover_folders_are_deleted_and_music_kept(self):
        junk = os.path.join(self.ipod.music_dir, "Release-xpost")
        os.makedirs(junk)
        open(os.path.join(junk, "release.nfo"), "w").close()
        song = self.ipod.add_track("Artist", "Album", "01.flac")
        app = self.run_clean()
        self.assertFalse(os.path.exists(junk))
        self.assertTrue(os.path.exists(song))
        self.assertIn("removed 1 folder", "\n".join(app.logs))

    def test_declining_deletes_nothing(self):
        junk = os.path.join(self.ipod.music_dir, "Release-xpost")
        os.makedirs(junk)
        open(os.path.join(junk, "release.nfo"), "w").close()
        self.run_clean(answer=False)
        self.assertTrue(os.path.exists(junk))

    def test_nothing_to_do_does_not_ask(self):
        self.ipod.add_track("Artist", "Album", "01.flac")
        app = self.run_clean()
        app._ask_yes_no.assert_not_called()
