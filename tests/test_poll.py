"""The iPod detection heartbeat.

_poll_ipod used to enumerate every mount point every 3 seconds forever.
detect_ipod_roots() lists each volume to decide whether it looks like an
iPod, which keeps a hard-drive iPod from ever spinning down and steals I/O
from an in-flight sync. It now only scans while it is still hunting.
"""

import os
import unittest
from unittest import mock

from helpers import FakeVar, IPodTestCase, RecordingRoot


class PollTestCase(IPodTestCase):
    def make_polling_app(self, selected=True, busy=False, syncing=False):
        app = self.bare_app(_busy=busy, _syncing=syncing)
        # The heartbeat reschedules itself; a root that ran callbacks
        # inline would recurse forever.
        app.root = RecordingRoot()
        app._ipod_root_var = FakeVar(self.ipod.path if selected else "")
        app._ipod_announced = None
        app._ipod_combo = {}
        return app

    def tick(self, app):
        """Run exactly one heartbeat, recording whether it enumerated
        volumes and how long it asked to wait before the next one."""
        module = self.p2i
        real_detect = module.detect_ipod_roots
        real_list = module.list_ipod_roots
        stats = {"detect": 0, "list": 0}

        def counting_detect():
            stats["detect"] += 1
            return real_detect()

        def counting_list():
            stats["list"] += 1
            return real_list()

        module.detect_ipod_roots = counting_detect
        module.list_ipod_roots = counting_list
        app.root.delays = []
        try:
            app._poll_ipod()
        finally:
            module.detect_ipod_roots = real_detect
            module.list_ipod_roots = real_list
        stats["delay"] = app.root.delays[-1] if app.root.delays else None
        return stats


class SettledPollTests(PollTestCase):
    def test_does_not_enumerate_volumes_when_an_ipod_is_selected(self):
        app = self.make_polling_app(selected=True)
        stats = self.tick(app)
        self.assertEqual(stats["detect"], 0)
        self.assertEqual(stats["list"], 0)

    def test_backs_off_to_the_settled_interval(self):
        app = self.make_polling_app(selected=True)
        self.assertEqual(self.tick(app)["delay"], app.POLL_SETTLED_MS)

    def test_still_reports_the_ipod_as_found(self):
        app = self.make_polling_app(selected=True)
        self.tick(app)
        self.assertIn("found", app._ipod_status_var.get())

    def test_settled_interval_is_longer_than_the_search_interval(self):
        app = self.make_polling_app()
        self.assertGreater(app.POLL_SETTLED_MS, app.POLL_SEARCHING_MS)


class SearchingPollTests(PollTestCase):
    def test_enumerates_volumes_while_hunting(self):
        app = self.make_polling_app(selected=False)
        self.assertEqual(self.tick(app)["detect"], 1)

    def test_keeps_the_brisk_interval_while_hunting(self):
        app = self.make_polling_app(selected=False)
        self.assertEqual(self.tick(app)["delay"], app.POLL_SEARCHING_MS)

    def test_auto_selects_a_detected_ipod_and_announces_it_once(self):
        app = self.make_polling_app(selected=False)
        with mock.patch.object(self.p2i, "detect_ipod_roots",
                               lambda: [self.ipod.path]):
            self.tick(app)
            self.assertEqual(app._ipod_root_var.get(), self.ipod.path)
            self.assertEqual(
                [l for l in app.logs if "iPod detected" in l],
                ["iPod detected: %s" % self.ipod.path])
            # Second tick is now settled, so nothing further is logged.
            self.tick(app)
            self.assertEqual(
                len([l for l in app.logs if "iPod detected" in l]), 1)

    def test_never_overrides_a_valid_manual_selection(self):
        app = self.make_polling_app(selected=True)
        other = os.path.dirname(self.ipod.path)
        with mock.patch.object(self.p2i, "detect_ipod_roots",
                               lambda: [other]):
            self.tick(app)
        self.assertEqual(app._ipod_root_var.get(), self.ipod.path)


class BusyPollTests(PollTestCase):
    def test_does_not_scan_during_a_sync(self):
        app = self.make_polling_app(selected=False, syncing=True)
        self.assertEqual(self.tick(app)["detect"], 0)

    def test_does_not_scan_during_another_operation(self):
        app = self.make_polling_app(selected=False, busy=True)
        self.assertEqual(self.tick(app)["detect"], 0)

    def test_backs_off_while_busy(self):
        app = self.make_polling_app(selected=False, busy=True)
        self.assertEqual(self.tick(app)["delay"], app.POLL_SETTLED_MS)


class PollReschedulingTests(PollTestCase):
    def test_always_reschedules_itself_even_when_something_throws(self):
        # The heartbeat must never die, or detection stops for the session.
        app = self.make_polling_app(selected=False)
        app._update_ipod_status = lambda: (_ for _ in ()).throw(
            RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            app._poll_ipod()
        self.assertEqual(app.root.delays[-1], app.POLL_SEARCHING_MS)


class IdleCostTests(PollTestCase):
    def test_a_settled_hour_costs_no_volume_listings(self):
        """Regression guard for the spun-up-disk bug."""
        app = self.make_polling_app(selected=True)
        real_listdir = os.listdir
        root = os.path.normpath(self.ipod.path)
        hits = []

        def counting(path="."):
            try:
                if os.path.normpath(path) == root:
                    hits.append(path)
            except (TypeError, ValueError):
                pass
            return real_listdir(path)

        os.listdir = counting
        try:
            ticks = 3600 * 1000 // app.POLL_SETTLED_MS
            for _ in range(ticks):
                app._poll_ipod()
        finally:
            os.listdir = real_listdir
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
