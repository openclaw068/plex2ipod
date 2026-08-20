"""Reflecting the iPod's contents in the checkboxes, and the deletion queue
that follows from un-ticking them.

The safety property under test throughout: a sync may only ever delete a
file the app itself ticked because it was already on the device.
"""

import os
import unittest

from helpers import IPodTestCase, make_track


class FolderExtractionTests(IPodTestCase):
    def make_app(self, rels):
        app = self.bare_app()
        app._ipod_index = {rel: "/abs/" + rel for rel in rels}
        return app

    def test_artist_folders(self):
        app = self.make_app(["radiohead/kid a/01.flac",
                             "radiohead/amnesiac/01.flac",
                             "portishead/dummy/01.flac"])
        self.assertEqual(app._ipod_folders(1), {"radiohead", "portishead"})

    def test_album_folders(self):
        app = self.make_app(["radiohead/kid a/01.flac",
                             "radiohead/amnesiac/01.flac"])
        self.assertEqual(app._ipod_folders(2),
                         {"radiohead/kid a", "radiohead/amnesiac"})

    def test_an_empty_index_yields_nothing(self):
        app = self.make_app([])
        self.assertEqual(app._ipod_folders(1), set())

    def test_a_missing_index_yields_nothing(self):
        app = self.bare_app()
        app._ipod_index = None
        self.assertEqual(app._ipod_folders(1), set())

    def test_files_shallower_than_the_depth_are_ignored(self):
        app = self.make_app(["loose.flac", "artist/album/track.flac"])
        self.assertEqual(app._ipod_folders(2), {"artist/album"})


class PlaylistPrecheckTests(IPodTestCase):
    def make_app(self, titles):
        app = self.bare_app()
        app._playlist_vars = {}
        for index, title in enumerate(titles):
            app._playlist_vars[str(index)] = (
                _Var(False), {"id": str(index), "title": title})
        return app

    def test_ticks_playlists_that_are_on_the_device(self):
        self.ipod.write_m3u("Workout", ["/Music/a/b/c.flac"])
        app = self.make_app(["Workout", "Chill"])
        app._precheck_playlists()
        self.assertTrue(app._playlist_vars["0"][0].get())
        self.assertFalse(app._playlist_vars["1"][0].get())

    def test_records_them_as_deletable(self):
        self.ipod.write_m3u("Workout", ["/Music/a/b/c.flac"])
        app = self.make_app(["Workout", "Chill"])
        app._precheck_playlists()
        self.assertEqual(app._baseline_playlists, {"0"})

    def test_matching_ignores_case(self):
        self.ipod.write_m3u("WORKOUT", ["/Music/a/b/c.flac"])
        app = self.make_app(["Workout"])
        app._precheck_playlists()
        self.assertTrue(app._playlist_vars["0"][0].get())

    def test_a_title_needing_sanitizing_still_matches(self):
        self.ipod.write_m3u("Rock_Metal", ["/Music/a/b/c.flac"])
        app = self.make_app(["Rock/Metal"])
        app._precheck_playlists()
        self.assertTrue(app._playlist_vars["0"][0].get())

    def test_a_missing_playlist_folder_is_harmless(self):
        app = self.make_app(["Workout"])
        app._precheck_playlists()
        self.assertFalse(app._playlist_vars["0"][0].get())
        self.assertEqual(app._baseline_playlists, set())


class SelectedPathsTests(IPodTestCase):
    def test_combines_playlists_and_library(self):
        pl_track = make_track(1, album="P")
        lib_track = make_track(2, album="L")
        app = self.bare_app()
        app._playlist_vars = {"1": (_Var(True), {"id": "1", "title": "P"})}
        app._playlist_track_cache = {"1": [pl_track]}
        app._gather_library_tracks = lambda: [lib_track]
        self.assertEqual(
            app._selected_paths(),
            {self.p2i.ipod_rel_path(pl_track).lower(),
             self.p2i.ipod_rel_path(lib_track).lower()})

    def test_an_unticked_playlist_contributes_nothing(self):
        app = self.bare_app()
        app._playlist_vars = {"1": (_Var(False), {"id": "1", "title": "P"})}
        app._playlist_track_cache = {"1": [make_track(1)]}
        app._gather_library_tracks = lambda: []
        self.assertEqual(app._selected_paths(), set())


class PendingDeletionTests(IPodTestCase):
    def make_app(self, on_device, baseline, selected=()):
        """on_device: filenames to create; baseline: which were pre-ticked."""
        app = self.bare_app()
        app._playlist_vars = {}
        index = {}
        for name in on_device:
            path = self.ipod.add_track("Artist", "Album", name)
            index["artist/album/%s" % name.lower()] = path
        app._ipod_index = index
        app._baseline_paths = {"artist/album/%s" % n.lower() for n in baseline}
        chosen = list(selected)
        app._gather_library_tracks = lambda: chosen
        return app

    def test_unticking_queues_the_file(self):
        app = self.make_app(["a.flac", "b.flac"], baseline=["a.flac", "b.flac"])
        tracks, playlists = app._pending_deletions()
        self.assertEqual(sorted(os.path.basename(p) for p in tracks),
                         ["a.flac", "b.flac"])
        self.assertEqual(playlists, [])

    def test_a_still_ticked_track_is_kept(self):
        keep = make_track(0, filename="a.flac")
        app = self.make_app(["a.flac", "b.flac"],
                            baseline=["a.flac", "b.flac"], selected=[keep])
        tracks, _ = app._pending_deletions()
        self.assertEqual([os.path.basename(p) for p in tracks], ["b.flac"])

    def test_queued_paths_are_real_and_exist(self):
        # The baseline is lowercased for matching; using it directly as a
        # filesystem path names nothing on a case-sensitive volume.
        app = self.make_app(["Mixed Case.flac"], baseline=["Mixed Case.flac"])
        tracks, _ = app._pending_deletions()
        self.assertEqual(len(tracks), 1)
        self.assertTrue(os.path.isfile(tracks[0]), tracks[0])
        self.assertEqual(os.path.basename(tracks[0]), "Mixed Case.flac")

    def test_files_outside_the_baseline_are_never_queued(self):
        # Music the app never ticked - a failed artist load, or something
        # Plex does not know about - must be untouchable.
        app = self.make_app(["a.flac", "stranger.flac"], baseline=["a.flac"])
        tracks, _ = app._pending_deletions()
        self.assertEqual([os.path.basename(p) for p in tracks], ["a.flac"])

    def test_an_empty_baseline_queues_nothing(self):
        app = self.make_app(["a.flac", "b.flac"], baseline=[])
        self.assertEqual(app._pending_deletions(), ([], []))

    def test_unticking_a_playlist_queues_its_file(self):
        app = self.make_app([], baseline=[])
        self.ipod.write_m3u("Workout", ["/Music/a/b/c.flac"])
        app._playlist_vars = {"1": (_Var(False), {"id": "1",
                                                  "title": "Workout"})}
        app._baseline_playlists = {"1"}
        _tracks, playlists = app._pending_deletions()
        self.assertEqual([os.path.basename(p) for p in playlists],
                         ["Workout.m3u"])

    def test_a_still_ticked_playlist_is_kept(self):
        app = self.make_app([], baseline=[])
        app._playlist_vars = {"1": (_Var(True), {"id": "1",
                                                 "title": "Workout"})}
        app._baseline_playlists = {"1"}
        self.assertEqual(app._pending_deletions()[1], [])


class ApplyDeletionTests(IPodTestCase):
    def make_app(self):
        app = self.bare_app()
        app._playlist_vars = {}
        app._gather_library_tracks = lambda: []
        return app

    def test_removes_the_queued_files(self):
        a = self.ipod.add_track("Artist", "Album", "a.flac")
        b = self.ipod.add_track("Artist", "Album", "b.flac")
        app = self.make_app()
        self.assertEqual(app._apply_deletions(([a], [])), 1)
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(b))

    def test_emptied_folders_are_cleaned_up(self):
        a = self.ipod.add_track("Artist", "Album", "a.flac")
        app = self.make_app()
        app._apply_deletions(([a], []))
        self.assertFalse(os.path.exists(os.path.join(self.ipod.music_dir,
                                                     "Artist")))

    def test_playlists_lose_the_deleted_entries(self):
        a = self.ipod.add_track("Artist", "Album", "a.flac")
        self.ipod.add_track("Artist", "Album", "b.flac")
        self.ipod.write_m3u("Mix", ["/Music/Artist/Album/a.flac",
                                    "/Music/Artist/Album/b.flac"])
        app = self.make_app()
        app._apply_deletions(([a], []))
        self.assertEqual(self.ipod.m3u_entries("Mix"),
                         ["/Music/Artist/Album/b.flac"])

    def test_nothing_queued_is_a_no_op(self):
        app = self.make_app()
        self.assertEqual(app._apply_deletions(None), 0)
        self.assertEqual(app._apply_deletions(([], [])), 0)

    def test_an_already_missing_file_is_not_an_error(self):
        app = self.make_app()
        ghost = os.path.join(self.ipod.music_dir, "gone.flac")
        self.assertEqual(app._apply_deletions(([ghost], [])), 0)

    def test_cancel_stops_the_removal(self):
        a = self.ipod.add_track("Artist", "Album", "a.flac")
        b = self.ipod.add_track("Artist", "Album", "b.flac")
        app = self.make_app()
        app._cancel = True
        self.assertEqual(app._apply_deletions(([a, b], [])), 0)
        self.assertTrue(os.path.exists(a))
        self.assertTrue(os.path.exists(b))


class _Var:
    """Minimal BooleanVar stand-in."""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


if __name__ == "__main__":
    unittest.main()
