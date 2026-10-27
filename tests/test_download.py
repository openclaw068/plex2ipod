"""Downloading a media part from Plex, against a real local HTTP server.

The failure that matters: a connection that drops mid-body ends the read
loop exactly like a finished download. Without a length check the short
file is placed on the iPod, and from then on sync skips it as present.
"""

import os
import shutil
import socket
import tempfile
import threading
import unittest
from unittest import mock

from helpers import app_module

BODY = bytes(range(256)) * 4000          # ~1 MB


class _Server:
    """Serves BODY. Each entry of `plan` shapes one response:
    "full", "cut" (declares the full length, sends a third, hangs up) or
    "nolength" (no Content-Length header, sends `nolength_bytes`)."""

    def __init__(self, plan, nolength_bytes=None):
        self.plan = list(plan)
        self.nolength_bytes = nolength_bytes or len(BODY)
        self.hits = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.url = "http://127.0.0.1:%d" % self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            conn.recv(65536)
            mode = self.plan[min(self.hits, len(self.plan) - 1)]
            self.hits += 1
            if mode == "nolength":
                conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n"
                             + BODY[:self.nolength_bytes])
            else:
                head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(BODY)
                conn.sendall(head + (BODY if mode == "full"
                                     else BODY[:len(BODY) // 3]))
            conn.close()

    def close(self):
        self.sock.close()


class DownloadPartTests(unittest.TestCase):
    def setUp(self):
        self.p2i = app_module()
        self.dir = tempfile.mkdtemp(prefix="plex2ipod-dl-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.dest = os.path.join(self.dir, "track.flac")
        # Retries back off with sleep(); none of that is under test.
        patcher = mock.patch.object(self.p2i.plexapi.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def download(self, plan, **kwargs):
        server = _Server(plan, kwargs.pop("nolength_bytes", None))
        self.addCleanup(server.close)
        client = self.p2i.plexapi.PlexClient(server.url, "token")
        result = client.download_part("/library/parts/1/file.flac",
                                      self.dest, **kwargs)
        return result, server

    def leftovers(self):
        return sorted(os.listdir(self.dir))

    def test_a_complete_download_is_returned(self):
        (ok, tmp), _ = self.download(["full"])
        self.assertTrue(ok)
        with open(tmp, "rb") as fh:
            self.assertEqual(fh.read(), BODY)

    def test_a_cut_connection_is_retried(self):
        (ok, tmp), server = self.download(["cut", "full"])
        self.assertTrue(ok)
        self.assertEqual(server.hits, 2)
        self.assertEqual(os.path.getsize(tmp), len(BODY))

    def test_a_connection_that_keeps_dropping_fails(self):
        (ok, err), server = self.download(["cut"])
        self.assertFalse(ok)
        self.assertIn("incomplete download", err)
        self.assertEqual(server.hits, 3)
        self.assertEqual(self.leftovers(), [], "no partial file left behind")

    def test_without_a_header_the_plex_size_is_checked(self):
        (ok, err), _ = self.download(["nolength"], nolength_bytes=1000,
                                     expected_size=len(BODY))
        self.assertFalse(ok)
        self.assertIn("1000 of %d" % len(BODY), err)

    def test_without_a_header_a_matching_plex_size_passes(self):
        (ok, _tmp), _ = self.download(["nolength"], expected_size=len(BODY))
        self.assertTrue(ok)

    def test_with_no_size_to_check_against_the_body_is_accepted(self):
        (ok, tmp), _ = self.download(["nolength"], nolength_bytes=1000)
        self.assertTrue(ok)
        self.assertEqual(os.path.getsize(tmp), 1000)

    def test_the_header_wins_over_a_stale_plex_size(self):
        # Plex's recorded size lags behind a file re-tagged on the server;
        # the server's own Content-Length is the truth.
        (ok, _tmp), _ = self.download(["full"], expected_size=12345)
        self.assertTrue(ok)


class ParseTrackTests(unittest.TestCase):
    def parse(self, file_attr):
        from xml.etree import ElementTree
        p2i = app_module()
        el = ElementTree.fromstring(
            '<Track title="T" grandparentTitle="A" parentTitle="B" '
            'duration="1000"><Media><Part key="/p/1" size="10" '
            'container="flac" file="%s"/></Media></Track>' % file_attr)
        return p2i.plexapi.PlexClient("http://x", "t")._parse_track(el)

    def test_keeps_the_server_path_with_forward_slashes(self):
        track = self.parse("D:\\Music\\Release\\01 Song.flac")
        self.assertEqual(track["server_path"], "D:/Music/Release/01 Song.flac")
        self.assertEqual(track["filename"], "01 Song.flac")

    def test_a_linux_server_path_is_kept_as_is(self):
        track = self.parse("/music/Release/01 Song.flac")
        self.assertEqual(track["server_path"], "/music/Release/01 Song.flac")


if __name__ == "__main__":
    unittest.main()
