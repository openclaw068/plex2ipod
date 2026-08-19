"""iPod capacity: free-space reporting, the size of a selection, and the
warning when it would not fit.

Sizes come from Plex metadata, so nothing here needs a real device.
"""

import os
import unittest
from unittest import mock

from helpers import FakeIPod, IPodTestCase, app_module, make_track


class DiskUsageTests(unittest.TestCase):
    def setUp(self):
        self.disk_usage = app_module().disk_usage

    def test_reports_a_plausible_triple_for_a_real_path(self):
        usage = self.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        self.assertIsNotNone(usage)
        total, used, free = usage
        self.assertGreater(total, 0)
        self.assertGreaterEqual(used, 0)
        self.assertGreaterEqual(free, 0)
        self.assertLessEqual(free, total)

    def test_a_missing_path_reports_nothing(self):
        self.assertIsNone(self.disk_usage("/nonexistent/path/xyzzy"))

    def test_an_empty_path_reports_nothing(self):
        self.assertIsNone(self.disk_usage(""))
        self.assertIsNone(self.disk_usage(None))


class SelectionSizeTests(IPodTestCase):
    """_selection_bytes decides what a sync would actually cost."""

    def make_app(self, tracks=(), checked=True, cached=True):
        app = self.bare_app()
        app._tree_checked = {}
        app._tree_data = {}
        app._gather_library_tracks = lambda: []
        if tracks:
            var = type("V", (), {"get": staticmethod(lambda: checked)})()
            app._playlist_vars = {"1": (var, {"id": "1", "title": "P"})}
            if cached:
                app._playlist_track_cache = {"1": list(tracks)}
        return app

    def test_an_empty_selection_costs_nothing(self):
        app = self.make_app()
        self.assertEqual(app._selection_bytes(), (0, False))

    def test_sums_the_sizes_of_selected_playlist_tracks(self):
        tracks = [make_track(n, **{"size": 1000}) for n in range(4)]
        for t in tracks:
            t["size"] = 1000
        app = self.make_app(tracks)
        total, pending = app._selection_bytes()
        self.assertEqual(total, 4000)
        self.assertFalse(pending)

    def test_an_unchecked_playlist_costs_nothing(self):
        tracks = [make_track(n) for n in range(4)]
        app = self.make_app(tracks, checked=False)
        self.assertEqual(app._selection_bytes()[0], 0)

    def test_a_playlist_whose_size_is_unknown_is_reported_as_pending(self):
        app = self.make_app([make_track(0)], cached=False)
        total, pending = app._selection_bytes()
        self.assertEqual(total, 0)
        self.assertTrue(pending)

    def test_the_same_track_in_two_playlists_is_counted_once(self):
        track = make_track(1)
        track["size"] = 500
        app = self.bare_app()
        app._gather_library_tracks = lambda: []
        yes = type("V", (), {"get": staticmethod(lambda: True)})()
        app._playlist_vars = {"1": (yes, {}), "2": (yes, {})}
        app._playlist_track_cache = {"1": [track], "2": [dict(track)]}
        self.assertEqual(app._selection_bytes()[0], 500)

    def test_library_selections_are_counted(self):
        track = make_track(0)
        track["size"] = 7000
        app = self.bare_app()
        app._playlist_vars = {}
        app._gather_library_tracks = lambda: [track]
        self.assertEqual(app._selection_bytes()[0], 7000)

    def test_tracks_already_on_the_ipod_cost_nothing(self):
        # The whole point of the index: re-syncing an existing library
        # must not look like it needs the space all over again.
        present = make_track(0)
        present["size"] = 9999
        missing = make_track(1)
        missing["size"] = 100
        app = self.bare_app()
        app._playlist_vars = {}
        app._gather_library_tracks = lambda: [present, missing]
        app._ipod_index = {self.p2i.ipod_rel_path(present).lower()}
        self.assertEqual(app._selection_bytes()[0], 100)

    def test_without_an_index_everything_is_counted(self):
        track = make_track(0)
        track["size"] = 2500
        app = self.bare_app()
        app._playlist_vars = {}
        app._gather_library_tracks = lambda: [track]
        app._ipod_index = None
        self.assertEqual(app._selection_bytes()[0], 2500)

    def test_a_missing_or_bogus_size_counts_as_zero(self):
        for value in (None, "", "abc", -5):
            with self.subTest(size=value):
                track = make_track(0)
                track["size"] = value
                app = self.bare_app()
                app._playlist_vars = {}
                app._gather_library_tracks = lambda: [track]
                self.assertEqual(app._selection_bytes()[0], 0)


class IpodIndexTests(IPodTestCase):
    def test_indexes_the_files_on_the_device(self):
        self.ipod.add_track("Radiohead", "Kid A", "01.flac")
        self.ipod.add_track("Radiohead", "Kid A", "02.flac")
        app = self.bare_app()
        captured = {}
        app._set_ipod_index = lambda index: captured.setdefault("i", index)
        app._ipod_index_worker(self.ipod.music_dir)
        self.assertEqual(captured["i"],
                         {"radiohead/kid a/01.flac", "radiohead/kid a/02.flac"})

    def test_zero_byte_files_are_not_indexed(self):
        # They are what a failed write leaves behind, and syncing has to
        # be able to replace them.
        self.ipod.add_track("A", "B", "good.flac", size=10)
        self.ipod.add_track("A", "B", "empty.flac", size=0)
        app = self.bare_app()
        captured = {}
        app._set_ipod_index = lambda index: captured.setdefault("i", index)
        app._ipod_index_worker(self.ipod.music_dir)
        self.assertEqual(captured["i"], {"a/b/good.flac"})

    def test_a_missing_music_folder_yields_an_empty_index(self):
        app = self.bare_app()
        captured = {}
        app._set_ipod_index = lambda index: captured.setdefault("i", index)
        app._ipod_index_worker("/nonexistent/xyzzy")
        self.assertEqual(captured["i"], set())


class CapacityConfirmTests(IPodTestCase):
    """Sync asks before starting something that cannot finish."""

    def make_app(self, capacity):
        app = self.bare_app()
        app._capacity = capacity
        app._human_size = lambda n: "%dB" % n
        return app

    def ask(self, answer, recorder=None):
        """Patch the confirm dialog. mock.patch.object restores the real
        function afterwards; assigning and deleting would remove it from
        tkinter.messagebox for the rest of the run."""
        def fake(title, message, **kw):
            if recorder is not None:
                recorder.update(title=title, message=message)
            return answer
        return mock.patch.object(self.p2i.app.messagebox, "askyesno", fake)

    def test_no_prompt_when_the_selection_fits(self):
        app = self.make_app({"over": 0, "selected": 10, "free": 100,
                             "total": 200, "used": 100})
        with mock.patch.object(self.p2i.app.messagebox, "askyesno") as asked:
            self.assertTrue(app._confirm_capacity())
        asked.assert_not_called()

    def test_no_prompt_when_capacity_is_unknown(self):
        app = self.make_app(None)
        with mock.patch.object(self.p2i.app.messagebox, "askyesno") as asked:
            self.assertTrue(app._confirm_capacity())
        asked.assert_not_called()

    def test_prompts_and_proceeds_on_yes(self):
        app = self.make_app({"over": 50, "selected": 150, "free": 100,
                             "total": 200, "used": 100})
        with self.ask(True):
            self.assertTrue(app._confirm_capacity())

    def test_prompts_and_aborts_on_no(self):
        app = self.make_app({"over": 50, "selected": 150, "free": 100,
                             "total": 200, "used": 100})
        with self.ask(False):
            self.assertFalse(app._confirm_capacity())
        self.assertTrue(any("cancelled" in line.lower() for line in app.logs),
                        app.logs)

    def test_the_prompt_reports_the_shortfall(self):
        app = self.make_app({"over": 50, "selected": 150, "free": 100,
                             "total": 200, "used": 100})
        seen = {}
        with self.ask(True, seen):
            app._confirm_capacity()
        self.assertIn("space", seen["title"].lower())
        self.assertIn("50B", seen["message"])
        self.assertIn("150B", seen["message"])
        self.assertIn("100B", seen["message"])


if __name__ == "__main__":
    unittest.main()
