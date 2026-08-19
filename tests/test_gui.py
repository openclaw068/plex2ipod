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
