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


class PlaylistUntickCascadeTests(IPodTestCase):
    """Un-ticking a playlist un-ticks its tracks in the library, except the
    ones another ticked playlist still uses."""

    SHARED = make_track(1, filename="shared.flac")
    ONLY_A = make_track(2, filename="only-a.flac")

    def make_app(self, a_ticked=True, b_ticked=True, cached=True):
        app = self.bare_app()
        app._playlist_vars = {
            "a": (_Var(a_ticked), {"id": "a", "title": "A"}),
            "b": (_Var(b_ticked), {"id": "b", "title": "B"})}
        app._playlist_ticked = {pid for pid, (var, _pl)
                                in app._playlist_vars.items() if var.get()}
        if cached:
            app._playlist_track_cache = {"a": [self.SHARED, self.ONLY_A],
                                         "b": [self.SHARED]}
        # Two library rows, both ticked by the pre-check.
        app._tree_data = {"t1": {"type": "track", "data": self.SHARED},
                          "t2": {"type": "track", "data": self.ONLY_A}}
        app._tree_checked = {"t1": True, "t2": True}
        app._set_checked = lambda iid, state: app._tree_checked.__setitem__(
            iid, state)
        app._refresh_ancestor_marks = lambda iid: None
        app._schedule_capacity_update = lambda: None
        return app

    def untick(self, app, pid):
        app._playlist_vars[pid][0].set(False)
        app._on_playlist_toggled(pid)

    def test_only_tracks_no_other_playlist_uses_are_unticked(self):
        app = self.make_app()
        self.untick(app, "a")
        self.assertEqual(app._tree_checked, {"t1": True, "t2": False})

    def test_with_no_other_playlist_everything_is_unticked(self):
        app = self.make_app(b_ticked=False)
        self.untick(app, "a")
        self.assertEqual(app._tree_checked, {"t1": False, "t2": False})

    def test_the_log_says_what_was_unticked(self):
        app = self.make_app()
        self.untick(app, "a")
        self.assertTrue(any("Un-ticked 1 track" in m for m in app.logs),
                        app.logs)

    def test_an_untick_waits_for_tracks_still_loading(self):
        app = self.make_app(b_ticked=False, cached=False)
        app._ensure_playlist_tracks = lambda pid: None
        self.untick(app, "a")
        self.assertEqual(app._tree_checked, {"t1": True, "t2": True})
        self.assertEqual(app._playlist_pending_untick, {"a"})
        app._store_playlist_tracks("a", [self.SHARED, self.ONLY_A])
        self.assertEqual(app._tree_checked, {"t1": False, "t2": False})
        self.assertEqual(app._playlist_pending_untick, set())

    def test_reticking_before_the_tracks_arrive_cancels_the_cascade(self):
        app = self.make_app(b_ticked=False, cached=False)
        app._ensure_playlist_tracks = lambda pid: None
        self.untick(app, "a")
        app._playlist_vars["a"][0].set(True)
        app._on_playlist_toggled("a")
        app._store_playlist_tracks("a", [self.SHARED, self.ONLY_A])
        self.assertEqual(app._tree_checked, {"t1": True, "t2": True})


class PlaylistBaselineTests(IPodTestCase):
    """A pre-checked playlist's tracks may be deleted; nothing else's."""

    def make_app(self, pid_in_baseline=True):
        track = make_track(1, artist="Artist", album="Album",
                           filename="a.flac")
        path = self.ipod.add_track("Artist", "Album", "a.flac")
        app = self.bare_app()
        app._ipod_index = {"artist/album/a.flac": path}
        app._playlist_vars = {"1": (_Var(False), {"id": "1",
                                                  "title": "Workout"})}
        app._playlist_track_cache = {"1": [track]}
        app._baseline_playlists = {"1"} if pid_in_baseline else set()
        app._gather_library_tracks = lambda: []
        return app

    def test_an_unticked_prechecked_playlist_queues_its_tracks(self):
        tracks, _ = self.make_app()._pending_deletions()
        self.assertEqual([os.path.basename(p) for p in tracks], ["a.flac"])

    def test_a_playlist_the_app_never_prechecked_queues_nothing(self):
        tracks, _ = self.make_app(pid_in_baseline=False)._pending_deletions()
        self.assertEqual(tracks, [])

    def test_a_track_not_on_the_device_is_not_queued(self):
        app = self.make_app()
        app._ipod_index = {}
        self.assertEqual(app._pending_deletions()[0], [])


class SyncWaitsForPlaylistsTests(IPodTestCase):
    def test_sync_refuses_while_playlist_tracks_are_loading(self):
        from unittest import mock
        app = self.bare_app(plex=object())
        app._playlist_fetching = {"1"}
        app._recompute_capacity = mock.Mock()
        with mock.patch.object(self.p2i.app.messagebox, "showinfo") as shown:
            app._on_sync()
        shown.assert_called_once()
        app._recompute_capacity.assert_not_called()
        self.assertFalse(app._syncing)


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
