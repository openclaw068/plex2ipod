"""Tests that need a real Tk display.

Skipped automatically when no display is available.

Two things worth knowing if you extend these:

  * Windows must be *mapped* for event_generate() to deliver anything.
    root.withdraw() silently swallows every generated event, which makes
    a broken binding look like it works. Park windows off-screen instead.
  * Worker threads call root.after(...), which needs a running mainloop.
    Pumping root.update() in a loop is not enough — the callback never
    registers. run_steps() below drives a real mainloop.
"""

import unittest

from helpers import (OFFSCREEN, StubPlex, TempAppDir, app_module,
                     destroy_tk, requires_tk)

try:
    import tkinter as tk
except Exception:                                  # pragma: no cover
    tk = None


PLAYLISTS = [{"id": "1", "title": "Workout", "leaf_count": 12,
              "smart": False}]
ARTISTS = [{"title": "Radiohead", "key": "/k/1", "rating_key": "1"},
           {"title": "Portishead", "key": "/k/2", "rating_key": "2"}]


@requires_tk
class WheelBindingTests(unittest.TestCase):
    """The wheel handler must be wired for all three event sequences and
    must not let Tk's own class binding scroll a second time."""

    def setUp(self):
        self.p2i = app_module()
        self.root = tk.Tk()
        self.root.geometry("240x120" + OFFSCREEN)
        self.root.update()
        self.addCleanup(self._destroy)

        self.canvas = tk.Canvas(self.root, width=240, height=120)
        self.inner = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.rows = []
        for i in range(40):
            row = tk.Frame(self.inner)
            row.pack(fill="x")
            tk.Label(row, text="row %d" % i).pack(side="left")
            self.rows.append(row)
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.pack()
        self.root.update()

        self.scrolls = []
        real = self.canvas.yview_scroll

        def recording(number, what):
            self.scrolls.append(number)
            return real(number, what)

        self.canvas.yview_scroll = recording
        self.app = self.p2i.App.__new__(self.p2i.App)

    def _destroy(self):
        destroy_tk(self.root)

    def top(self):
        return self.canvas.yview()[0]

    def test_all_three_sequences_are_bound(self):
        self.app._bind_mousewheel(self.canvas, self.canvas)
        bound = {str(seq) for seq in self.canvas.bind()}
        self.assertLessEqual({"<MouseWheel>", "<Button-4>", "<Button-5>"},
                             bound)

    def test_wheel_scrolls_the_canvas(self):
        self.app._bind_mousewheel(self.canvas, self.canvas)
        before = self.top()
        self.canvas.event_generate("<MouseWheel>", x=10, y=10, delta=-120)
        self.root.update()
        self.assertGreater(self.top(), before)
        self.assertEqual(self.scrolls, [1])

    def test_wheel_scrolls_back_up(self):
        self.app._bind_mousewheel(self.canvas, self.canvas)
        self.canvas.event_generate("<MouseWheel>", x=10, y=10, delta=-120)
        self.root.update()
        middle = self.top()
        self.canvas.event_generate("<MouseWheel>", x=10, y=10, delta=120)
        self.root.update()
        self.assertLess(self.top(), middle)

    def test_the_class_binding_does_not_double_scroll(self):
        # The handler returns "break"; without it Tk 9's built-in Canvas
        # binding would scroll a second time for one wheel notch.
        self.app._bind_mousewheel(self.canvas, self.canvas)
        self.canvas.event_generate("<MouseWheel>", x=10, y=10, delta=-120)
        self.root.update()
        self.assertEqual(len(self.scrolls), 1)

    def test_recurse_binds_row_children(self):
        # Rows contain a label; the pointer is usually over that, not over
        # the row frame itself.
        row = self.rows[0]
        self.app._bind_mousewheel(row, self.canvas, recurse=True)
        label = row.winfo_children()[0]
        before = self.top()
        label.event_generate("<MouseWheel>", x=3, y=3, delta=-120)
        self.root.update()
        self.assertGreater(self.top(), before)

    def test_without_recurse_children_are_not_bound(self):
        row = self.rows[1]
        self.app._bind_mousewheel(row, self.canvas)
        label = row.winfo_children()[0]
        self.assertNotIn("<MouseWheel>", {str(s) for s in label.bind()})


@requires_tk
class StyledWidgetTests(unittest.TestCase):
    def setUp(self):
        self.p2i = app_module()
        self.root = tk.Tk()
        self.root.geometry("300x200" + OFFSCREEN)
        self.root.update()
        self.addCleanup(self._destroy)
        self.frame = tk.Frame(self.root, bg="#ffffff")
        self.frame.pack()

    def _destroy(self):
        destroy_tk(self.root)

    def test_disabled_colors_come_from_the_theme(self):
        dark = self.p2i.StyledButton(self.frame, "X", self.p2i.THEMES["dark"])
        light = self.p2i.StyledButton(self.frame, "X",
                                      self.p2i.THEMES["light"])
        self.assertEqual(dark._disabled_bg,
                         self.p2i.THEMES["dark"]["check_off"])
        self.assertEqual(light._disabled_bg,
                         self.p2i.THEMES["light"]["check_off"])
        self.assertNotEqual(dark._disabled_bg, light._disabled_bg)
        self.assertNotEqual(dark._disabled_fg, light._disabled_fg)

    def test_set_state_round_trips(self):
        button = self.p2i.StyledButton(self.frame, "X",
                                       self.p2i.THEMES["dark"])
        self.assertFalse(button._disabled)
        button.set_state(False)
        self.assertTrue(button._disabled)
        button.set_state(True)
        self.assertFalse(button._disabled)

    def test_a_disabled_button_ignores_clicks(self):
        fired = []
        button = self.p2i.StyledButton(self.frame, "X",
                                       self.p2i.THEMES["dark"],
                                       command=lambda: fired.append(1))
        button.set_state(False)
        button._on_click(None)
        self.assertEqual(fired, [])
        button.set_state(True)
        button._on_click(None)
        self.assertEqual(fired, [1])

    def test_checkbox_draws_a_checkmark_only_when_checked(self):
        var = tk.BooleanVar(value=True)
        box = self.p2i.StyledCheckbutton(self.frame, "t", var,
                                         self.p2i.THEMES["dark"])
        box.pack()
        self.root.update()
        checked = len(box._box.find_all())
        var.set(False)
        self.root.update()
        unchecked = len(box._box.find_all())
        self.assertEqual(checked - unchecked, 2)   # two checkmark strokes

    def test_checkbox_enable_toggle_still_redraws(self):
        var = tk.BooleanVar(value=True)
        box = self.p2i.StyledCheckbutton(self.frame, "t", var,
                                         self.p2i.THEMES["dark"])
        box.pack()
        self.root.update()
        box.set_enabled(False)
        self.root.update()
        self.assertFalse(box._enabled)
        box._toggle()                              # must be inert
        self.assertTrue(var.get())
        box.set_enabled(True)
        box._toggle()
        self.assertFalse(var.get())


@requires_tk
class AppLifecycleTests(unittest.TestCase):
    """Exercises the real App, including the connect path."""

    def setUp(self):
        self.p2i = app_module()
        self._cfg = TempAppDir(self.p2i)
        self._cfg.__enter__()
        self.addCleanup(lambda: self._cfg.__exit__(None, None, None))
        self._plex_cls = self.p2i.PlexClient
        self.p2i.PlexClient = lambda *a, **k: StubPlex(
            playlists=PLAYLISTS, artists=ARTISTS)
        self.addCleanup(self._restore_plex)
        self.app = None

    def _restore_plex(self):
        self.p2i.PlexClient = self._plex_cls

    def tearDown(self):
        if self.app is not None:
            destroy_tk(self.app.root)

    def start_app(self):
        self.app = self.p2i.App()
        self.app.root.geometry("900x700" + OFFSCREEN)
        self.app.root.update()
        return self.app

    def run_steps(self, steps):
        """Drive a real mainloop through a list of (delay_ms, callable).

        A running mainloop is required: the app's workers call
        root.after() from other threads, which fails outright without one.
        """
        app = self.app

        def step(index=0):
            if index >= len(steps):
                app.root.quit()
                return
            delay, func = steps[index]
            func(app)
            app.root.after(delay, lambda: step(index + 1))

        app.root.after(50, step)
        app.root.mainloop()

    def artist_rows(self):
        return [self.app._tree.item(i, "text")
                for i in self.app._tree.get_children()]

    # -- construction --
    def test_builds_three_tabs_and_can_switch_between_them(self):
        app = self.start_app()
        self.assertEqual(len(app._tab_frames), 3)
        for index in (0, 1, 2):
            app._select_tab(index)
            app.root.update()
            self.assertEqual(app._active_tab.get(), index)

    def test_busy_state_drives_the_sync_and_cancel_buttons(self):
        app = self.start_app()
        app._set_busy(True)
        self.assertTrue(app._sync_btn._disabled)
        self.assertFalse(app._cancel_btn._disabled)
        app._set_busy(False)
        self.assertFalse(app._sync_btn._disabled)
        self.assertTrue(app._cancel_btn._disabled)

    def test_theme_toggle_rebuilds_the_ui(self):
        app = self.start_app()
        first = app._current_theme
        app._toggle_theme()
        app.root.update()
        self.assertNotEqual(app._current_theme, first)
        self.assertEqual(len(app._tab_frames), 3)
        app._toggle_theme()
        app.root.update()
        self.assertEqual(app._current_theme, first)

    def test_the_playlist_canvas_is_wheel_bound(self):
        app = self.start_app()
        bound = {str(seq) for seq in app._pl_canvas.bind()}
        self.assertLessEqual({"<MouseWheel>", "<Button-4>", "<Button-5>"},
                             bound)

    # -- the connect race --
    def test_artists_load_when_connecting_with_library_tab_active(self):
        """Regression: the tree used to sit empty until the user switched
        tabs, because the load only fired from _select_tab."""
        app = self.start_app()
        rows = {}
        self.run_steps([
            (50, lambda a: a._select_tab(1)),
            (50, lambda a: rows.__setitem__("before", self.artist_rows())),
            (1500, lambda a: (setattr(a, "plex", StubPlex(artists=ARTISTS)),
                              a._on_connected(PLAYLISTS, "3"))),
            (50, lambda a: rows.__setitem__("after", self.artist_rows())),
        ])
        self.assertEqual(rows["before"], [])
        self.assertEqual(len(rows["after"]), 2)

    def test_the_library_tab_still_loads_lazily(self):
        app = self.start_app()
        rows = {}
        self.run_steps([
            (50, lambda a: a._select_tab(0)),
            (1000, lambda a: (setattr(a, "plex", StubPlex(artists=ARTISTS)),
                              a._on_connected(PLAYLISTS, "3"))),
            (50, lambda a: rows.__setitem__("mid", self.artist_rows())),
            (1500, lambda a: a._select_tab(1)),
            (50, lambda a: rows.__setitem__("after", self.artist_rows())),
        ])
        self.assertEqual(rows["mid"], [])
        self.assertEqual(len(rows["after"]), 2)

    def test_reconnecting_does_not_duplicate_artist_rows(self):
        app = self.start_app()
        rows = {}
        self.run_steps([
            (50, lambda a: a._select_tab(1)),
            (1500, lambda a: (setattr(a, "plex", StubPlex(artists=ARTISTS)),
                              a._on_connected(PLAYLISTS, "3"))),
            (50, lambda a: rows.__setitem__("first", self.artist_rows())),
            (900, lambda a: a._select_tab(1)),
            (50, lambda a: rows.__setitem__("second", self.artist_rows())),
        ])
        self.assertEqual(len(rows["first"]), 2)
        self.assertEqual(len(rows["second"]), 2)

    def test_connecting_populates_the_playlist_tab(self):
        app = self.start_app()
        counts = {}
        self.run_steps([
            (50, lambda a: a._select_tab(0)),
            (1000, lambda a: (setattr(a, "plex", StubPlex(artists=ARTISTS)),
                              a._on_connected(PLAYLISTS, "3"))),
            (50, lambda a: counts.__setitem__(
                "n", len(a._playlist_vars))),
        ])
        self.assertEqual(counts["n"], 1)


@requires_tk
class ManageTreeTests(unittest.TestCase):
    """Selection gathering over the Manage tree, including the Playlists
    group that sits outside the artist/album/track hierarchy."""

    def setUp(self):
        from tkinter import ttk
        self.p2i = app_module()
        self.root = tk.Tk()
        self.root.geometry("600x400" + OFFSCREEN)
        self.root.update()
        self.addCleanup(lambda: destroy_tk(self.root))

        self.app = self.p2i.App.__new__(self.p2i.App)
        self.app._manage_tree = ttk.Treeview(self.root, columns=("info",))
        self.app._manage_checked = {}
        self.app._manage_data = {}
        self.app._manage_status_var = tk.StringVar()
        self.app._ipod_root_var = tk.StringVar(value="/fake/ipod")
        self.app._human_size = lambda n: "%dB" % n

        data = [("Artist", "/m/Artist",
                 [("Album", "/m/Artist/Album",
                   [("01.flac", "/m/Artist/Album/01.flac", 10),
                    ("02.flac", "/m/Artist/Album/02.flac", 20)])])]
        playlists = [("Mix.m3u", "/p/Mix.m3u", 5),
                     ("Gym.m3u", "/p/Gym.m3u", 7)]
        self.app._populate_manage_tree(data, playlists, "/p")

    def iids_of(self, kind):
        return [iid for iid, info in self.app._manage_data.items()
                if info["type"] == kind]

    def test_playlists_appear_as_their_own_group(self):
        self.assertEqual(len(self.iids_of("playlist_group")), 1)
        self.assertEqual(len(self.iids_of("playlist")), 2)

    def test_nothing_checked_gathers_nothing(self):
        self.assertEqual(self.app._gather_manage_files(), set())

    def test_checking_one_playlist_gathers_only_it(self):
        iid = sorted(self.iids_of("playlist"))[0]
        self.app._manage_checked[iid] = True
        gathered = self.app._gather_manage_files()
        self.assertEqual(gathered, {self.app._manage_data[iid]["path"]})

    def test_checking_the_playlist_group_gathers_every_playlist(self):
        group = self.iids_of("playlist_group")[0]
        self.app._set_manage_checked(group, True)
        self.assertEqual(self.app._gather_manage_files(),
                         {"/p/Mix.m3u", "/p/Gym.m3u"})

    def test_checking_an_artist_gathers_its_tracks_only(self):
        artist = self.iids_of("artist")[0]
        self.app._set_manage_checked(artist, True)
        self.assertEqual(self.app._gather_manage_files(),
                         {"/m/Artist/Album/01.flac", "/m/Artist/Album/02.flac"})

    def test_container_paths_are_never_gathered_for_deletion(self):
        for iid in self.app._manage_checked:
            self.app._manage_checked[iid] = True
        gathered = self.app._gather_manage_files()
        self.assertNotIn("/m/Artist", gathered)
        self.assertNotIn("/m/Artist/Album", gathered)
        self.assertEqual(len(gathered), 4)      # 2 tracks + 2 playlists

    def test_the_status_line_mentions_playlists(self):
        self.assertIn("playlists", self.app._manage_status_var.get())

    def test_a_scan_with_no_playlists_adds_no_group(self):
        for iid in self.app._manage_tree.get_children():
            self.app._manage_tree.delete(iid)
        self.app._manage_checked.clear()
        self.app._manage_data.clear()
        self.app._populate_manage_tree([], [], "/p")
        self.assertEqual(self.iids_of("playlist_group"), [])


@requires_tk
class WorkAreaTests(unittest.TestCase):
    """Maximize used a hardcoded screenheight - 48 for the taskbar."""

    def setUp(self):
        self.p2i = app_module()
        self.root = tk.Tk()
        self.root.geometry("300x200" + OFFSCREEN)
        self.root.update()
        self.addCleanup(lambda: destroy_tk(self.root))
        self.app = self.p2i.App.__new__(self.p2i.App)
        self.app.root = self.root

    def test_returns_a_plausible_rectangle(self):
        x, y, width, height = self.app._work_area()
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertLessEqual(width, self.root.winfo_screenwidth())
        self.assertLessEqual(height, self.root.winfo_screenheight())
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_all_four_values_are_integers(self):
        for value in self.app._work_area():
            self.assertIsInstance(value, int)


@requires_tk
class ManageTabGuardTests(unittest.TestCase):
    """_scan_ipod must refuse to run while another operation owns the busy
    state, or it would clear busy mid-sync and re-enable Sync and Eject."""

    def setUp(self):
        self.p2i = app_module()
        self._cfg = TempAppDir(self.p2i)
        self._cfg.__enter__()
        self.addCleanup(lambda: self._cfg.__exit__(None, None, None))
        self.app = self.p2i.App()
        self.app.root.geometry("900x700" + OFFSCREEN)
        self.app.root.update()
        self.addCleanup(self._destroy)

    def _destroy(self):
        destroy_tk(self.app.root)

    def test_scan_is_refused_while_syncing(self):
        self.app._syncing = True
        self.app._scan_ipod()
        self.assertIn("Busy", self.app._manage_status_var.get())

    def test_scan_is_refused_while_busy(self):
        self.app._busy = True
        self.app._scan_ipod()
        self.assertIn("Busy", self.app._manage_status_var.get())

    def test_a_refused_scan_leaves_busy_untouched(self):
        self.app._syncing = True
        self.app._busy = True
        self.app._scan_ipod()
        self.assertTrue(self.app._busy)

    def test_a_refused_scan_does_not_mark_the_tree_loaded(self):
        self.app._busy = True
        self.app._manage_loaded = False
        self.app._scan_ipod()
        self.assertFalse(self.app._manage_loaded)


if __name__ == "__main__":
    unittest.main()


ALBUMS = {"/k/1": [{"title": "Kid A", "key": "/a/1", "rating_key": "a1",
                    "year": "2000"}]}
ALBUM_TRACKS = {"/a/1": [
    {"title": "Everything", "artist": "Radiohead", "album": "Kid A",
     "duration_ms": 100000, "part_key": "/p/1", "filename": "01.flac",
     "container": "flac", "size": 4096}]}


@requires_tk
class ThemeTogglePreservationTests(unittest.TestCase):
    """Switching theme rebuilds every widget. That used to throw away the
    user's selections, the loaded library tree, the Manage listing and the
    busy state, and fire a fresh Plex round trip to repopulate."""

    def setUp(self):
        self.p2i = app_module()
        self._cfg = TempAppDir(self.p2i)
        self._cfg.__enter__()
        self.addCleanup(lambda: self._cfg.__exit__(None, None, None))
        self.stub = StubPlex(playlists=PLAYLISTS, artists=ARTISTS,
                             albums=ALBUMS, album_tracks=ALBUM_TRACKS)
        self._real_plex = self.p2i.PlexClient
        self.p2i.PlexClient = lambda *a, **k: self.stub
        self.addCleanup(self._restore)
        self.app = self.p2i.App()
        self.app.root.geometry("900x700" + OFFSCREEN)
        self.app.root.update()
        self.addCleanup(lambda: destroy_tk(self.app.root))

    def _restore(self):
        self.p2i.PlexClient = self._real_plex

    def run_steps(self, steps):
        app = self.app

        def step(index=0):
            if index >= len(steps):
                app.root.quit()
                return
            delay, func = steps[index]
            func(app)
            app.root.after(delay, lambda: step(index + 1))

        app.root.after(50, step)
        app.root.mainloop()

    def connect(self, app):
        app.plex = self.stub
        app._on_connected(PLAYLISTS, "3")

    def artist_rows(self):
        return [self.app._tree.item(i, "text")
                for i in self.app._tree.get_children()]

    # -- settings fields --
    def test_connection_fields_survive(self):
        self.run_steps([
            (50, lambda a: (a._url_var.set("http://box:32400"),
                            a._token_var.set("secret-token"))),
            (300, lambda a: a._toggle_theme()),
        ])
        self.assertEqual(self.app._url_var.get(), "http://box:32400")
        self.assertEqual(self.app._token_var.get(), "secret-token")

    def test_an_unsaved_downsample_choice_survives(self):
        self.run_steps([
            (50, lambda a: a._downsample_var.set(False)),
            (300, lambda a: a._toggle_theme()),
        ])
        self.assertFalse(self.app._downsample_var.get())

    def test_the_update_m3u_choice_survives(self):
        self.run_steps([
            (50, lambda a: a._update_m3u_var.set(False)),
            (300, lambda a: a._toggle_theme()),
        ])
        self.assertFalse(self.app._update_m3u_var.get())

    # -- playlists --
    def test_playlist_rows_and_ticks_survive(self):
        checked = {}
        self.run_steps([
            (50, self.connect),
            (400, lambda a: [v.set(True) for v, _p in
                             a._playlist_vars.values()]),
            (300, lambda a: a._toggle_theme()),
            (300, lambda a: checked.update(
                {pid: v.get() for pid, (v, _p) in a._playlist_vars.items()})),
        ])
        self.assertEqual(len(self.app._playlist_vars), len(PLAYLISTS))
        self.assertTrue(all(checked.values()), checked)

    def test_unticked_playlists_stay_unticked(self):
        self.run_steps([
            (50, self.connect),
            (400, lambda a: a._toggle_theme()),
        ])
        self.assertTrue(self.app._playlist_vars)
        self.assertFalse(any(v.get() for v, _p in
                             self.app._playlist_vars.values()))

    # -- library tree --
    def test_the_loaded_artist_tree_survives(self):
        self.run_steps([
            (50, lambda a: a._select_tab(1)),
            (1200, self.connect),
            (300, lambda a: a._toggle_theme()),
        ])
        self.assertEqual(len(self.artist_rows()), len(ARTISTS))

    def test_library_ticks_survive(self):
        state = {}
        self.run_steps([
            (50, lambda a: a._select_tab(1)),
            (1200, self.connect),
            (300, lambda a: a._set_checked(a._tree.get_children()[0], True)),
            (900, lambda a: a._toggle_theme()),
            (300, lambda a: state.update(
                rows=self.artist_rows(),
                checked=[a._tree_checked[i]
                         for i in a._tree.get_children()])),
        ])
        self.assertEqual(len(state["rows"]), len(ARTISTS))
        self.assertTrue(state["checked"][0])
        self.assertTrue(state["rows"][0].startswith("☑"))

    def radiohead(self, app):
        """The artist the stub actually has albums for. Rows sort with
        Portishead first, so index 0 is the wrong one."""
        for iid in app._tree.get_children():
            if "Radiohead" in app._tree.item(iid, "text"):
                return iid
        raise AssertionError("Radiohead row not found")

    def test_an_expanded_branch_keeps_its_children(self):
        state = {}
        self.run_steps([
            (50, lambda a: a._select_tab(1)),
            (1200, self.connect),
            # Checking an artist lazily loads its albums, then its tracks.
            (300, lambda a: a._set_checked(self.radiohead(a), True)),
            (1500, lambda a: a._toggle_theme()),
            (400, lambda a: state.update(
                artists=list(a._tree.get_children()))),
        ])
        radiohead = [i for i in state["artists"]
                     if "Radiohead" in self.app._tree.item(i, "text")]
        self.assertEqual(len(radiohead), 1)
        albums = self.app._tree.get_children(radiohead[0])
        self.assertTrue(albums, "expanded albums were lost")
        self.assertIn("Kid A", self.app._tree.item(albums[0], "text"))

    def test_tree_data_is_still_usable_for_syncing(self):
        state = {}
        self.run_steps([
            (50, lambda a: a._select_tab(1)),
            (1200, self.connect),
            (300, lambda a: a._set_checked(self.radiohead(a), True)),
            (1500, lambda a: a._toggle_theme()),
            (400, lambda a: state.update(
                tracks=a._gather_library_tracks())),
        ])
        self.assertTrue(state["tracks"], "no tracks gathered after toggle")
        self.assertEqual(state["tracks"][0]["title"], "Everything")

    def test_no_reconnect_round_trip_is_made(self):
        # The old code re-ran the whole connect worker to repopulate.
        calls = {"n": 0}
        self.run_steps([
            (50, self.connect),
            (400, lambda a: (setattr(self.stub, "get_playlists",
                                     lambda: (calls.__setitem__(
                                         "n", calls["n"] + 1), PLAYLISTS)[1]),
                             a._toggle_theme())),
            (600, lambda a: None),
        ])
        self.assertEqual(calls["n"], 0)

    # -- manage tab --
    def test_the_manage_listing_survives_without_a_rescan(self):
        state = {}

        def fake_populate(a):
            a._populate_manage_tree(
                [("Radiohead", "/m/Radiohead",
                  [("Kid A", "/m/Radiohead/Kid A",
                    [("01.flac", "/m/Radiohead/Kid A/01.flac", 10)])])],
                [("Mix.m3u", "/p/Mix.m3u", 5)], "/p")

        self.run_steps([
            (50, lambda a: a._select_tab(2)),
            (600, fake_populate),
            (200, lambda a: setattr(a, "_scans", 0)),
            (100, lambda a: setattr(
                a, "_scan_ipod",
                lambda: setattr(a, "_scans", a._scans + 1))),
            (100, lambda a: a._toggle_theme()),
            (400, lambda a: state.update(
                top=[a._manage_tree.item(i, "text")
                     for i in a._manage_tree.get_children()],
                kinds=sorted({v["type"] for v in a._manage_data.values()}),
                scans=a._scans)),
        ])
        self.assertEqual(len(state["top"]), 2)          # Playlists + artist
        self.assertIn("playlist", state["kinds"])
        self.assertIn("track", state["kinds"])
        self.assertEqual(state["scans"], 0, "the tab rescanned the device")

    # -- transient state --
    def test_the_active_tab_survives(self):
        for index in (0, 1, 2):
            with self.subTest(tab=index):
                self.run_steps([
                    (50, lambda a, i=index: a._select_tab(i)),
                    (400, lambda a: a._toggle_theme()),
                ])
                self.assertEqual(self.app._active_tab.get(), index)

    def test_busy_state_survives(self):
        # Toggling mid-operation used to hand back an enabled Sync button
        # and a disabled Cancel button while the operation ran on.
        state = {}
        self.run_steps([
            (50, lambda a: a._set_busy(True)),
            (300, lambda a: a._toggle_theme()),
            (300, lambda a: state.update(
                busy=a._busy,
                sync_disabled=a._sync_btn._disabled,
                cancel_disabled=a._cancel_btn._disabled)),
        ])
        self.assertTrue(state["busy"])
        self.assertTrue(state["sync_disabled"])
        self.assertFalse(state["cancel_disabled"])

    def test_idle_state_is_not_made_busy(self):
        state = {}
        self.run_steps([
            (50, lambda a: a._toggle_theme()),
            (300, lambda a: state.update(
                sync_disabled=a._sync_btn._disabled)),
        ])
        self.assertFalse(state["sync_disabled"])

    def test_progress_survives(self):
        self.run_steps([
            (50, lambda a: a._set_progress(42)),
            (300, lambda a: a._toggle_theme()),
        ])
        self.assertEqual(self.app._progress_val, 42)

    def test_the_status_line_survives(self):
        self.run_steps([
            (50, self.connect),
            (400, lambda a: a._toggle_theme()),
        ])
        self.assertIn("Connected", self.app._status_var.get())

    def test_the_log_survives(self):
        self.run_steps([
            (50, lambda a: a._log_msg("a distinctive log line")),
            (300, lambda a: a._toggle_theme()),
        ])
        body = self.app._log.get("1.0", "end")
        self.assertIn("a distinctive log line", body)

    def test_toggling_twice_returns_to_the_original_theme(self):
        start = self.app._current_theme
        self.run_steps([
            (50, self.connect),
            (400, lambda a: a._toggle_theme()),
            (400, lambda a: a._toggle_theme()),
        ])
        self.assertEqual(self.app._current_theme, start)
        self.assertEqual(len(self.app._playlist_vars), len(PLAYLISTS))


@requires_tk
class StaleCallbackTests(unittest.TestCase):
    """Item ids are per-widget and reused, so a library load that lands
    after a rebuild must be dropped rather than inserted somewhere else."""

    def setUp(self):
        self.p2i = app_module()
        self._cfg = TempAppDir(self.p2i)
        self._cfg.__enter__()
        self.addCleanup(lambda: self._cfg.__exit__(None, None, None))
        self.app = self.p2i.App()
        self.app.root.geometry("900x700" + OFFSCREEN)
        self.app.root.update()
        self.addCleanup(lambda: destroy_tk(self.app.root))

    def test_a_stale_artist_load_is_ignored(self):
        generation = self.app._ui_generation
        self.app._toggle_theme()
        self.app.root.update()
        self.app._populate_artists(ARTISTS, generation)
        self.assertEqual(self.app._tree.get_children(), ())

    def test_a_current_artist_load_is_applied(self):
        self.app._populate_artists(ARTISTS, self.app._ui_generation)
        self.assertEqual(len(self.app._tree.get_children()), len(ARTISTS))

    def test_a_load_with_no_generation_is_applied(self):
        self.app._populate_artists(ARTISTS)
        self.assertEqual(len(self.app._tree.get_children()), len(ARTISTS))

    def test_a_stale_album_load_is_ignored(self):
        self.app._populate_artists(ARTISTS)
        parent = self.app._tree.get_children()[0]
        generation = self.app._ui_generation
        self.app._toggle_theme()
        self.app.root.update()
        before = self.app._capture_tree(
            self.app._tree, self.app._tree_data, self.app._tree_checked)
        self.app._populate_albums(parent, [{"title": "Ghost", "key": "/x",
                                            "rating_key": "x", "year": ""}],
                                  generation)
        after = self.app._capture_tree(
            self.app._tree, self.app._tree_data, self.app._tree_checked)
        self.assertEqual(before, after)

    def test_an_album_load_for_a_vanished_parent_is_ignored(self):
        self.app._populate_artists(ARTISTS)
        parent = self.app._tree.get_children()[0]
        self.app._tree.delete(parent)
        self.app._populate_albums(parent, [{"title": "Ghost", "key": "/x",
                                            "rating_key": "x", "year": ""}],
                                  self.app._ui_generation)
        self.assertNotIn("Ghost", str(self.app._tree.get_children()))

    def test_the_generation_advances_on_each_rebuild(self):
        first = self.app._ui_generation
        self.app._toggle_theme()
        self.app.root.update()
        self.assertNotEqual(self.app._ui_generation, first)
