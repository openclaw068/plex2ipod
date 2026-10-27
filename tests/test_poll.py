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
        # app.py binds these at import time, so that is the module whose
        # attribute has to be replaced.
        module = self.p2i.app
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
        with mock.patch.object(self.p2i.app, "detect_ipod_roots",
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
        with mock.patch.object(self.p2i.app, "detect_ipod_roots",
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
    def count_listings(self, app):
        """How often an hour of heartbeats lists the iPod's root."""
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
            for _ in range(3600 * 1000 // app.POLL_SETTLED_MS):
                app._poll_ipod()
        finally:
            os.listdir = real_listdir
        return len(hits)

    def test_a_selection_that_was_never_indexed_is_listed_once(self):
        # A saved path that has just become valid: indexed once, then quiet.
        app = self.make_polling_app(selected=True)
        app._ipod_index_worker = lambda music_root: None
        self.assertEqual(self.count_listings(app), 1)

    def test_a_settled_hour_costs_no_volume_listings(self):
        """Regression guard for the spun-up-disk bug."""
        app = self.make_polling_app(selected=True)
        app._indexed_root = self.ipod.path      # settled: already indexed
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


# lsblk nests partitions under their disk in some versions...
LSBLK = {"blockdevices": [
    {"path": "/dev/sda", "pkname": None, "fstype": None, "mountpoint": None,
     "model": "iPod",
     "children": [
         {"path": "/dev/sda1", "pkname": "sda", "fstype": None,
          "mountpoint": None, "model": None},
         {"path": "/dev/sda2", "pkname": "sda", "fstype": "vfat",
          "mountpoint": None, "model": None}]},
    {"path": "/dev/sdb", "pkname": None, "fstype": None, "mountpoint": None,
     "model": "SanDisk Ultra",
     "children": [{"path": "/dev/sdb1", "pkname": "sdb", "fstype": "vfat",
                   "mountpoint": None, "model": None}]},
    {"path": "/dev/nvme0n1", "pkname": None, "fstype": None,
     "mountpoint": None, "model": "Samsung SSD", "children": []},
]}

# ...and lists them flat in others: this is Fedora's, from a real iPod.
LSBLK_FLAT = {"blockdevices": [
    {"path": "/dev/sda", "pkname": None, "fstype": None, "mountpoint": None,
     "model": "iPod"},
    {"path": "/dev/sda1", "pkname": "sda", "fstype": None,
     "mountpoint": None, "model": None},
    {"path": "/dev/sda2", "pkname": "sda", "fstype": "vfat",
     "mountpoint": None, "model": None},
    {"path": "/dev/sdb", "pkname": None, "fstype": None, "mountpoint": None,
     "model": "SanDisk Ultra"},
    {"path": "/dev/sdb1", "pkname": "sdb", "fstype": "vfat",
     "mountpoint": None, "model": None},
    {"path": "/dev/zram0", "pkname": None, "fstype": "swap",
     "mountpoint": "[SWAP]", "model": None},
]}


class IpodPartitionTests(unittest.TestCase):
    """Only a disk that says it is an iPod is ever considered, and only
    its FAT32 music partition."""

    def setUp(self):
        from helpers import app_module
        self.io = app_module().platform_io

    def partitions(self, data):
        import json
        result = mock.Mock(stdout=json.dumps(data))
        with mock.patch.object(self.io, "IS_WINDOWS", False), \
                mock.patch.object(self.io.shutil, "which",
                                  return_value="/usr/bin/lsblk"), \
                mock.patch.object(self.io, "_no_console_run",
                                  return_value=result):
            return self.io.ipod_partitions()

    def test_finds_the_ipod_music_partition_only(self):
        self.assertEqual(self.partitions(LSBLK),
                         [{"device": "/dev/sda2", "mountpoint": None}])

    def test_flat_lsblk_output_is_understood_too(self):
        self.assertEqual(self.partitions(LSBLK_FLAT),
                         [{"device": "/dev/sda2", "mountpoint": None}])

    def test_reports_where_it_is_mounted(self):
        import copy
        data = copy.deepcopy(LSBLK)
        data["blockdevices"][0]["children"][1]["mountpoint"] = "/run/media/u/IPOD"
        self.assertEqual(self.partitions(data)[0]["mountpoint"],
                         "/run/media/u/IPOD")

    def test_bad_lsblk_output_is_nothing(self):
        with mock.patch.object(self.io, "IS_WINDOWS", False), \
                mock.patch.object(self.io.shutil, "which",
                                  return_value="/usr/bin/lsblk"), \
                mock.patch.object(self.io, "_no_console_run",
                                  return_value=mock.Mock(stdout="garbage")):
            self.assertEqual(self.io.ipod_partitions(), [])

    def test_mount_reports_the_mount_point(self):
        for out in ("Mounted /dev/sda2 at /run/media/u/ADMIN_S IPO\n",
                    "Mounted /dev/sda2 at /run/media/u/ADMIN_S IPO.\n"):
            with mock.patch.object(self.io, "_no_console_run",
                                   return_value=mock.Mock(returncode=0,
                                                          stdout=out)):
                self.assertEqual(self.io.mount_partition("/dev/sda2"),
                                 ("/run/media/u/ADMIN_S IPO", None))

    def test_mount_failure_is_reported(self):
        with mock.patch.object(self.io, "_no_console_run",
                               return_value=mock.Mock(
                                   returncode=1, stdout="",
                                   stderr="Not authorized")):
            self.assertEqual(self.io.mount_partition("/dev/sda2"),
                             (None, "Not authorized"))


class AutoMountTests(PollTestCase):
    """A plugged-in but unmounted iPod is mounted, once per plug-in."""

    def run_worker(self, app, parts, mount_result=("/run/media/u/IPOD", None)):
        module = self.p2i.app
        calls = []

        def mount(device):
            calls.append(device)
            return mount_result

        with mock.patch.object(module, "ipod_partitions", return_value=parts), \
                mock.patch.object(module, "mount_partition", mount):
            app._automount_worker()
        return calls

    def unmounted(self):
        return [{"device": "/dev/sda2", "mountpoint": None}]

    def test_an_unmounted_ipod_is_mounted(self):
        app = self.make_polling_app(selected=False)
        self.assertEqual(self.run_worker(app, self.unmounted()), ["/dev/sda2"])

    def test_it_is_not_mounted_again_after_the_user_unmounts_it(self):
        app = self.make_polling_app(selected=False)
        self.run_worker(app, self.unmounted())
        self.assertEqual(self.run_worker(app, self.unmounted()), [])

    def test_unplugging_and_replugging_mounts_again(self):
        app = self.make_polling_app(selected=False)
        self.run_worker(app, self.unmounted())
        self.run_worker(app, [])                      # unplugged
        self.assertEqual(self.run_worker(app, self.unmounted()), ["/dev/sda2"])

    def test_an_already_mounted_ipod_is_left_alone(self):
        app = self.make_polling_app(selected=False)
        mounted = [{"device": "/dev/sda2", "mountpoint": "/run/media/u/I"}]
        self.assertEqual(self.run_worker(app, mounted), [])

    def test_a_failed_mount_is_explained_and_not_retried(self):
        app = self.make_polling_app(selected=False)
        self.run_worker(app, self.unmounted(), (None, "Not authorized"))
        for func, args in app.root.calls:      # deliver the queued logs
            if func == app._log_msg:
                func(*args)
        self.assertIn("Not authorized", "\n".join(app.logs))
        self.assertEqual(self.run_worker(app, self.unmounted()), [])

    def test_the_refresh_button_tries_again(self):
        app = self.make_polling_app(selected=False)
        self.run_worker(app, self.unmounted())
        app._automount_tried.clear()   # what _refresh_ipod_roots does
        self.assertEqual(self.run_worker(app, self.unmounted()), ["/dev/sda2"])

    def test_a_mounted_ipod_is_selected(self):
        app = self.make_polling_app(selected=False)
        app._maybe_refresh_ipod_index = mock.Mock()
        app._automount_finished([self.ipod.path])
        self.assertEqual(app._ipod_root_var.get(), self.ipod.path)
        app._maybe_refresh_ipod_index.assert_called_once()

    def test_a_valid_saved_path_gets_indexed_by_the_heartbeat(self):
        # The iPod mounted where it was last time: the path was saved, so
        # nothing is "detected", but it still has to be indexed.
        app = self.make_polling_app(selected=True)
        app._maybe_refresh_ipod_index = mock.Mock()
        app._poll_ipod()
        app._maybe_refresh_ipod_index.assert_called_once()
