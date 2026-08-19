"""_do_sync: downloading, deduplication, and the rule that a .m3u may only
list tracks that actually reached the iPod.
"""

import os
import unittest
from unittest import mock

from helpers import (FailingAudio, IPodTestCase, NoAudio, StubPlex,
                     app_module, make_track)


class SyncTestCase(IPodTestCase):
    def setUp(self):
        super().setUp()
        # FakeRoot runs after() callbacks inline, so a messagebox reached
        # from a worker would pop a real modal dialog mid-test.
        module = app_module().app
        for name in ("showinfo", "showwarning", "showerror"):
            patcher = mock.patch.object(module.messagebox, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self, tracks, fail_keys=(), cancel_after=None, audio=None):
        app = self.bare_app()
        app.plex = StubPlex(tracks, fail_keys=fail_keys,
                            cancel_after=cancel_after, app=app)
        app.sync_engine = self.p2i.SyncEngine(self.ipod.path)
        app.audio = audio or NoAudio()
        return app

    def sync(self, app, downsample=False, playlist="Mix"):
        app._do_sync([("1", {"title": playlist})], [], downsample)


class PlaylistOnlyListsRealFilesTests(SyncTestCase):
    """The core guarantee: every .m3u entry resolves to a file on disk."""

    def test_failed_downloads_are_left_out(self):
        tracks = [make_track(n) for n in range(6)]
        app = self.build(tracks, fail_keys=["/library/parts/2/file.flac",
                                            "/library/parts/4/file.flac"])
        self.sync(app)

        self.assertEqual(self.ipod.basenames(),
                         ["00 Track.flac", "01 Track.flac",
                          "03 Track.flac", "05 Track.flac"])
        entries = self.ipod.m3u_entries("Mix")
        self.assertEqual(len(entries), 4)
        self.assertEntriesResolve(entries)

    def test_cancelling_partway_leaves_a_consistent_playlist(self):
        tracks = [make_track(n) for n in range(6)]
        app = self.build(tracks, cancel_after=3)
        self.sync(app)

        entries = self.ipod.m3u_entries("Mix")
        self.assertTrue(0 < len(entries) < 6)
        self.assertEqual(len(entries), len(self.ipod.basenames()))
        self.assertEntriesResolve(entries)

    def test_tracks_already_on_the_ipod_stay_in_the_playlist(self):
        tracks = [make_track(n) for n in range(6)]
        self.ipod.add_tracks(tracks[:2])
        app = self.build(tracks, fail_keys=["/library/parts/5/file.flac"])
        self.sync(app)

        entries = self.ipod.m3u_entries("Mix")
        self.assertEqual(len(entries), 5)
        self.assertNotIn("05 Track.flac",
                         [os.path.basename(e) for e in entries])
        self.assertEntriesResolve(entries)

    def test_a_failed_downsample_excludes_the_track(self):
        # downsample() cleans up its temp file on failure, so nothing is
        # left on disk and the playlist must not reference it.
        tracks = [make_track(n) for n in range(3)]
        app = self.build(tracks, audio=FailingAudio())
        self.sync(app, downsample=True)

        self.assertEqual(self.ipod.basenames(), [])
        self.assertIsNone(self.ipod.m3u_entries("Mix"))

    def test_every_track_present_writes_a_complete_playlist(self):
        tracks = [make_track(n) for n in range(6)]
        app = self.build(tracks)
        self.sync(app)

        self.assertEqual(len(self.ipod.basenames()), 6)
        entries = self.ipod.m3u_entries("Mix")
        self.assertEqual(len(entries), 6)
        self.assertEntriesResolve(entries)

    def assertEntriesResolve(self, entries):
        """Every playlist line must name a file that exists on the volume."""
        for entry in entries:
            local = os.path.join(self.ipod.path,
                                 entry.lstrip("/").replace("/", os.sep))
            self.assertTrue(os.path.isfile(local),
                            "playlist points at a missing file: %s" % entry)


class PlaylistPreservationTests(SyncTestCase):
    def test_an_existing_playlist_is_not_clobbered_when_nothing_lands(self):
        # generate_m3u rewrites the file from scratch, so writing an empty
        # playlist would destroy a previously good one.
        tracks = [make_track(n) for n in range(3)]
        original = self.ipod.write_m3u(
            "Mix", ["/Music/Artist/Album/99 Old.flac"])
        before = open(original, "rb").read()

        app = self.build(tracks, fail_keys=[t["part_key"] for t in tracks])
        self.sync(app)

        self.assertEqual(open(original, "rb").read(), before)

    def test_skipping_is_reported_in_the_log(self):
        tracks = [make_track(n) for n in range(3)]
        app = self.build(tracks, fail_keys=[t["part_key"] for t in tracks])
        self.sync(app)
        self.assertTrue(any("Playlist skipped" in line for line in app.logs),
                        app.logs)

    def test_omitted_track_count_is_reported(self):
        tracks = [make_track(n) for n in range(4)]
        app = self.build(tracks, fail_keys=["/library/parts/1/file.flac"])
        self.sync(app)
        self.assertTrue(any("omitted" in line for line in app.logs), app.logs)


class CollisionWarningTests(SyncTestCase):
    """When several tracks want one path, only one file can be written.
    The app warns instead of renaming: the recovery features map iPod
    files back to Plex through the same path, so a rename they cannot
    reproduce would make Verify & Repair delete the file as an orphan.
    """

    def test_a_collision_is_reported(self):
        a = make_track(1, album="Greatest Hits", filename="01 Song.flac")
        b = make_track(2, album="Greatest Hits", filename="01 Song.flac")
        app = self.build([a, b])
        self.sync(app)
        self.assertTrue(any("claimed by more than one track" in line
                            for line in app.logs), app.logs)

    def test_the_colliding_path_and_titles_are_named_in_the_log(self):
        a = make_track(1, album="Greatest Hits", filename="01 Song.flac")
        b = make_track(2, album="Greatest Hits", filename="01 Song.flac")
        app = self.build([a, b])
        self.sync(app)
        # Match the detail line specifically ("<path>  <-  <titles>"), not
        # merely a log line that happens to mention the filename.
        detail = [line for line in app.logs if "  <-  " in line]
        self.assertEqual(len(detail), 1, app.logs)
        self.assertIn("01 song.flac", detail[0].lower())
        self.assertIn(a["title"], detail[0])
        self.assertIn(b["title"], detail[0])

    def test_a_clean_selection_produces_no_warning(self):
        app = self.build([make_track(n) for n in range(4)])
        self.sync(app)
        self.assertFalse(any("claimed by more than one" in line
                             for line in app.logs), app.logs)

    def test_the_same_track_in_two_playlists_is_not_reported(self):
        tracks = [make_track(n) for n in range(3)]
        app = self.build(tracks)
        app._do_sync([("1", {"title": "A"}), ("2", {"title": "B"})],
                     [], False)
        self.assertFalse(any("claimed by more than one" in line
                             for line in app.logs), app.logs)

    def test_the_warning_is_capped(self):
        tracks = []
        for n in range(20):
            tracks.append(make_track(100 + n, album="Dupe",
                                     filename="01 Song.flac"))
        app = self.build(tracks)
        self.sync(app)
        self.assertTrue(any("more" in line for line in app.logs), app.logs)

    def test_syncing_still_completes_despite_a_collision(self):
        a = make_track(1, album="Dupe", filename="01 Song.flac")
        b = make_track(2, album="Dupe", filename="01 Song.flac")
        c = make_track(3)
        app = self.build([a, b, c])
        self.sync(app)
        # One file for the contested path, one for the clean track.
        self.assertEqual(len(self.ipod.basenames()), 2)


class DownloadBehaviourTests(SyncTestCase):
    def test_tracks_already_present_are_not_downloaded_again(self):
        tracks = [make_track(n) for n in range(4)]
        self.ipod.add_tracks(tracks[:3])
        app = self.build(tracks)
        self.sync(app)
        self.assertEqual(app.plex.downloads, 1)

    def test_no_part_files_are_left_behind(self):
        tracks = [make_track(n) for n in range(4)]
        app = self.build(tracks)
        self.sync(app)
        leftovers = []
        for root, _dirs, files in os.walk(self.ipod.path):
            leftovers += [f for f in files if f.endswith((".part", ".tmp"))]
        self.assertEqual(leftovers, [])

    def test_a_track_in_two_playlists_is_downloaded_once(self):
        tracks = [make_track(n) for n in range(3)]
        app = self.build(tracks)
        app._do_sync([("1", {"title": "A"}), ("2", {"title": "B"})],
                     [], False)
        self.assertEqual(app.plex.downloads, 3)
        self.assertEqual(len(self.ipod.m3u_entries("A")), 3)
        self.assertEqual(len(self.ipod.m3u_entries("B")), 3)

    def test_library_selections_sync_without_any_playlist(self):
        tracks = [make_track(n) for n in range(3)]
        app = self.build(tracks)
        app._do_sync([], tracks, False)
        self.assertEqual(len(self.ipod.basenames()), 3)
        self.assertIsNone(self.ipod.m3u_entries("Mix"))

    def test_nothing_selected_is_a_no_op(self):
        app = self.build([])
        app._do_sync([], [], False)
        self.assertEqual(self.ipod.basenames(), [])
        self.assertTrue(any("Nothing selected" in line for line in app.logs))


if __name__ == "__main__":
    unittest.main()


class DeviceFullTests(SyncTestCase):
    """When the volume fills mid-sync, stop cleanly instead of failing
    every remaining track with a disk-full error."""

    def sync_with_free_space(self, app, free_bytes, downsample=False):
        """Run a sync with disk_usage reporting a fixed amount free."""
        module = app_module().app
        total = 1000000
        usage = (total, total - free_bytes, free_bytes)
        with mock.patch.object(module, "disk_usage", lambda root: usage):
            app._do_sync([("1", {"title": "Mix"})], [], downsample)

    def test_stops_before_writing_when_there_is_no_room(self):
        tracks = [make_track(n) for n in range(5)]
        for t in tracks:
            t["size"] = 1000
        app = self.build(tracks)
        self.sync_with_free_space(app, free_bytes=0)

        self.assertEqual(self.ipod.basenames(), [])
        self.assertTrue(any("full" in line.lower() for line in app.logs),
                        app.logs)

    def test_the_log_says_how_many_were_skipped(self):
        tracks = [make_track(n) for n in range(5)]
        for t in tracks:
            t["size"] = 1000
        app = self.build(tracks)
        self.sync_with_free_space(app, free_bytes=0)
        self.assertTrue(any("5 track(s) were not copied" in line
                            for line in app.logs), app.logs)

    def test_the_summary_reports_the_device_filling_up(self):
        tracks = [make_track(n) for n in range(3)]
        for t in tracks:
            t["size"] = 1000
        app = self.build(tracks)
        self.sync_with_free_space(app, free_bytes=0)
        self.assertTrue(any("filled up" in line for line in app.logs),
                        app.logs)

    def test_plenty_of_room_syncs_everything(self):
        tracks = [make_track(n) for n in range(4)]
        for t in tracks:
            t["size"] = 1000
        app = self.build(tracks)
        self.sync_with_free_space(app, free_bytes=10 ** 9)

        self.assertEqual(len(self.ipod.basenames()), 4)
        self.assertFalse(any("full" in line.lower() for line in app.logs),
                         app.logs)

    def test_a_playlist_is_still_written_for_what_fitted(self):
        # Nothing landed, so there is nothing to point a playlist at and
        # the existing one must not be clobbered with an empty file.
        tracks = [make_track(n) for n in range(3)]
        for t in tracks:
            t["size"] = 1000
        self.ipod.write_m3u("Mix", ["/Music/Artist/Album/99 Old.flac"])
        before = self.ipod.m3u_raw("Mix")
        app = self.build(tracks)
        self.sync_with_free_space(app, free_bytes=0)
        self.assertEqual(self.ipod.m3u_raw("Mix"), before)

    def test_unknown_disk_usage_does_not_block_the_sync(self):
        tracks = [make_track(n) for n in range(3)]
        app = self.build(tracks)
        module = app_module().app
        with mock.patch.object(module, "disk_usage", lambda root: None):
            app._do_sync([("1", {"title": "Mix"})], [], False)
        self.assertEqual(len(self.ipod.basenames()), 3)
