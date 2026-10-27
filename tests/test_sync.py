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


class _ConvertingAudio(NoAudio):
    """Reports every FLAC as 24-bit and converts it, recording every path
    it was asked to read or write."""

    available = True

    def __init__(self):
        self.touched = []

    def probe(self, path):
        self.touched.append(path)
        return {"bit_depth": 24, "sample_rate": 96000}

    def downsample(self, src, dst, timeout=300):
        self.touched += [src, dst]
        with open(dst, "wb") as fh:
            fh.write(b"converted")
        return True, None


class StagingTests(SyncTestCase):
    """Only the finished file may cross USB. Downloading and converting on
    the iPod writes the full-size file, reads it back and writes again."""

    def test_conversion_never_touches_the_ipod(self):
        audio = _ConvertingAudio()
        app = self.build([make_track(1)], audio=audio)
        self.sync(app, downsample=True)
        self.assertTrue(audio.touched)
        for path in audio.touched:
            self.assertFalse(path.startswith(self.ipod.path), path)

    def test_the_converted_file_is_what_lands(self):
        app = self.build([make_track(1)], audio=_ConvertingAudio())
        self.sync(app, downsample=True)
        [rel] = self.ipod.audio_files()
        with open(os.path.join(self.ipod.music_dir, rel), "rb") as fh:
            self.assertEqual(fh.read(), b"converted")
        self.assertIn("Downsampled: 24-bit", "\n".join(app.logs))

    def test_downloads_are_not_written_to_the_ipod(self):
        app = self.build([make_track(1)])
        seen = []
        real = app.plex.download_part

        def spy(part_key, dest_path, **kwargs):
            seen.append(dest_path)
            return real(part_key, dest_path, **kwargs)

        app.plex.download_part = spy
        self.sync(app)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].startswith(self.ipod.path), seen[0])
        self.assertEqual(len(self.ipod.audio_files()), 1)

    def test_the_plex_size_is_passed_for_the_length_check(self):
        app = self.build([make_track(1, size=4096)])
        sizes = []
        real = app.plex.download_part

        def spy(part_key, dest_path, **kwargs):
            sizes.append(kwargs.get("expected_size"))
            return real(part_key, dest_path, **kwargs)

        app.plex.download_part = spy
        self.sync(app)
        self.assertEqual(sizes, [4096])

    def test_a_failed_copy_leaves_nothing_on_the_ipod(self):
        app = self.build([make_track(1)])
        with mock.patch.object(self.p2i.app.shutil, "copyfile",
                               side_effect=OSError(28, "No space left")):
            self.sync(app)
        leftovers = [f for _d, _s, fs in os.walk(self.ipod.music_dir)
                     for f in fs]
        self.assertEqual(leftovers, [])
        self.assertIn("No space left", "\n".join(app.logs))

    def test_the_staging_folder_is_removed(self):
        app = self.build([make_track(1), make_track(2)])
        made = []
        real = self.p2i.app.tempfile.mkdtemp

        def spy(*a, **k):
            made.append(real(*a, **k))
            return made[-1]

        with mock.patch.object(self.p2i.app.tempfile, "mkdtemp", spy):
            self.sync(app)
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))


@unittest.skipUnless(__import__("shutil").which("ffmpeg"), "needs ffmpeg")
class RealDownsampleTests(SyncTestCase):
    """End to end with the real converter: a 24-bit FLAC served by "Plex"
    arrives on the iPod as 16-bit."""

    def test_a_24_bit_flac_lands_as_16_bit(self):
        import shutil, subprocess, tempfile
        work = tempfile.mkdtemp(prefix="plex2ipod-ffmpeg-")
        self.addCleanup(shutil.rmtree, work, True)
        source = os.path.join(work, "source.flac")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=3", "-ar", "96000",
                        "-sample_fmt", "s32", "-bits_per_raw_sample", "24",
                        source], check=True)

        app = self.build([make_track(1)],
                         audio=self.p2i.AudioConverter())
        self.assertEqual(app.audio.probe(source)["bit_depth"], 24)

        def serve(part_key, dest_path, cancel_check=None, expected_size=0):
            shutil.copyfile(source, dest_path + ".part")
            return True, dest_path + ".part"

        app.plex.download_part = serve
        self.sync(app, downsample=True)
        [rel] = self.ipod.audio_files()
        landed = os.path.join(self.ipod.music_dir, rel)
        self.assertEqual(app.audio.probe(landed)["bit_depth"], 16)
        self.assertLess(os.path.getsize(landed), os.path.getsize(source))


RELEASE = "Outkast-Original Album Classics-BOXSET-3CD-FLAC-2011-xpost"


class RelocationTests(SyncTestCase):
    """A track already on the iPod somewhere else is moved into place, not
    downloaded again. Shaped on a real device: hand-copied scene-release
    folders that mirror the server's D:\\Music, and Plex renames."""

    def copied_by_hand(self, *parts, size=4096):
        """Put a file at Music/<parts...> the way a manual copy would."""
        path = os.path.join(self.ipod.music_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"\1" * size)
        return path

    def release_track(self, n, *folders, size=12345):
        name = "%02d - Song.flac" % n
        return make_track(n, artist="OutKast", album="Speakerboxxx",
                          filename=name, size=size,
                          server_path="D:/Music/" + "/".join(folders + (name,)))

    def dest(self, track):
        return os.path.join(self.ipod.music_dir,
                            *self.p2i.ipod_rel_path(track).split("/"))

    def test_a_hand_copied_release_is_moved_not_downloaded(self):
        src = self.copied_by_hand(RELEASE, "01 - Song.flac", size=777)
        track = self.release_track(1, RELEASE)
        app = self.build([track])
        self.sync(app)
        self.assertEqual(app.plex.downloads, 0)
        self.assertFalse(os.path.exists(src))
        self.assertEqual(os.path.getsize(self.dest(track)), 777,
                         "the original bytes, not a fresh download")

    def test_a_multi_disc_release_is_matched_one_level_deeper(self):
        self.copied_by_hand(RELEASE, "CD1", "01 - Song.flac")
        track = self.release_track(1, RELEASE, "CD1")
        app = self.build([track])
        self.sync(app)
        self.assertEqual(app.plex.downloads, 0)
        self.assertTrue(os.path.exists(self.dest(track)))

    def test_a_plex_rename_is_matched_by_name_and_size(self):
        self.copied_by_hand("OutKast", "Old Album Name", "01 Song.flac",
                            size=5000)
        track = make_track(1, artist="OutKast", album="Corrected Name",
                           filename="01 Song.flac", size=5000)
        app = self.build([track])
        self.sync(app)
        self.assertEqual(app.plex.downloads, 0)
        self.assertTrue(os.path.exists(self.dest(track)))

    def test_a_different_size_without_a_path_match_is_downloaded(self):
        self.copied_by_hand("OutKast", "Old Album Name", "01 Song.flac",
                            size=4999)
        track = make_track(1, artist="OutKast", album="Corrected Name",
                           filename="01 Song.flac", size=5000)
        app = self.build([track])
        self.sync(app)
        self.assertEqual(app.plex.downloads, 1)

    def test_two_lookalikes_are_ambiguous_and_neither_is_moved(self):
        a = self.copied_by_hand("X", "One", "01 Song.flac", size=5000)
        b = self.copied_by_hand("X", "Two", "01 Song.flac", size=5000)
        track = make_track(1, artist="OutKast", album="Three",
                           filename="01 Song.flac", size=5000)
        app = self.build([track])
        self.sync(app)
        self.assertEqual(app.plex.downloads, 1)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_a_file_another_selected_track_owns_is_never_taken(self):
        owner = make_track(1, artist="A", album="One", filename="x.flac",
                           size=4096)
        owned = self.ipod.add_track("A", "One", "x.flac")
        thief = make_track(2, artist="B", album="Two", filename="x.flac",
                           size=4096)
        app = self.build([owner, thief])
        self.sync(app)
        self.assertTrue(os.path.exists(owned))
        self.assertEqual(app.plex.downloads, 1)

    def test_a_file_at_the_music_root_is_not_a_path_match(self):
        # Folder/filename is the least evidence that counts; a bare name
        # could be anything.
        self.copied_by_hand("01 - Song.flac", size=1)
        track = self.release_track(1, RELEASE)
        app = self.build([track])
        self.sync(app)
        self.assertEqual(app.plex.downloads, 1)

    def test_non_music_files_and_their_folder_are_left_alone(self):
        self.copied_by_hand(RELEASE, "01 - Song.flac")
        nfo = self.copied_by_hand(RELEASE, "release.nfo")
        app = self.build([self.release_track(1, RELEASE)])
        self.sync(app)
        self.assertTrue(os.path.exists(nfo))

    def test_an_emptied_release_folder_is_removed(self):
        self.copied_by_hand(RELEASE, "CD1", "01 - Song.flac")
        app = self.build([self.release_track(1, RELEASE, "CD1")])
        self.sync(app)
        self.assertFalse(os.path.exists(
            os.path.join(self.ipod.music_dir, RELEASE)))

    def test_the_synced_playlist_lists_the_new_path(self):
        self.copied_by_hand(RELEASE, "01 - Song.flac")
        track = self.release_track(1, RELEASE)
        app = self.build([track])
        self.sync(app, playlist="Mix")
        self.assertEqual(self.ipod.m3u_entries("Mix"),
                         ["/Music/" + self.p2i.ipod_rel_path(track)])

    def test_other_playlists_follow_the_move(self):
        self.copied_by_hand(RELEASE, "01 - Song.flac")
        track = self.release_track(1, RELEASE)
        os.makedirs(self.ipod.playlist_dir, exist_ok=True)
        rockbox = os.path.join(self.ipod.playlist_dir, "Shuffle.m3u8")
        with open(rockbox, "wb") as fh:
            fh.write(("\ufeff/<HDD0>/Music/%s/01 - Song.flac\r\n"
                      "/<HDD0>/Music/Other/keep.flac\r\n" % RELEASE)
                     .encode("utf-8"))
        app_style = self.ipod.write_m3u(
            "Old", ["/Music/%s/01 - Song.flac" % RELEASE])
        app = self.build([track])
        self.sync(app, playlist="Mix")

        new = self.p2i.ipod_rel_path(track)
        with open(rockbox, "rb") as fh:
            self.assertEqual(fh.read(), (
                "\ufeff/<HDD0>/Music/%s\r\n/<HDD0>/Music/Other/keep.flac\r\n"
                % new).encode("utf-8"), "BOM, prefix and CRLF kept")
        self.assertEqual(self.ipod.m3u_entries("Old"), ["/Music/" + new])

    def test_the_summary_counts_moves_separately(self):
        self.copied_by_hand(RELEASE, "01 - Song.flac")
        app = self.build([self.release_track(1, RELEASE),
                          make_track(2, artist="Z", album="Z")])
        self.sync(app)
        log = "\n".join(app.logs)
        self.assertIn("1 to download", log)
        self.assertIn("1 moved into place, no transfer", log)
        self.assertIn("Done. 1 downloaded, 1 moved, 0 skipped", log)


class SyncOrderTests(SyncTestCase):
    def test_deletions_run_before_downloads(self):
        app = self.build([make_track(1)])
        events = []
        app._apply_deletions = lambda d: events.append("delete") or 0
        real = app._fetch_track

        def fetch(*a, **k):
            events.append("download")
            return real(*a, **k)

        app._fetch_track = fetch
        self.sync(app)
        self.assertEqual(events, ["delete", "download"])

    def test_space_freed_by_deletions_is_used_by_the_same_sync(self):
        # A full iPod: the new track only fits once the old one is gone.
        old = self.ipod.add_track("Old", "Album", "old.flac", size=8192)
        app = self.build([make_track(1)])
        free = {"bytes": 0}

        def usage(_root):
            return (100000, 100000 - free["bytes"], free["bytes"])

        real_remove = os.remove

        def remove(path):
            real_remove(path)
            if path == old:
                free["bytes"] += 8192 + self.p2i.app.FREE_SPACE_MARGIN

        with mock.patch.object(self.p2i.app, "disk_usage", usage), \
                mock.patch.object(self.p2i.app.os, "remove", remove):
            app._do_sync([("1", {"title": "Mix"})], [], False,
                         ([old], []))
        self.assertFalse(os.path.exists(old))
        self.assertEqual(app.plex.downloads, 1)
        self.assertNotIn("iPod is full", "\n".join(app.logs))

    def test_leftover_part_files_are_cleared(self):
        leftover = self.ipod.add_track("A", "B", "x.flac.part")
        tmp = self.ipod.add_track("A", "B", "y.flac.tmp")
        app = self.build([make_track(1)])
        self.sync(app)
        self.assertFalse(os.path.exists(leftover))
        self.assertFalse(os.path.exists(tmp))
        self.assertIn("2 leftover temp file", "\n".join(app.logs))


class NoEmptyFoldersTests(SyncTestCase):
    def test_a_failed_download_leaves_no_album_folder(self):
        track = make_track(1, artist="Ghost", album="Never Arrived")
        app = self.build([track], fail_keys={track["part_key"]})
        self.sync(app)
        self.assertFalse(os.path.exists(
            os.path.join(self.ipod.music_dir, "Ghost")))
