"""Connecting when the saved server address has gone stale, and telling
the user plainly when their sign-in has expired."""

import unittest
from unittest import mock
from urllib.error import HTTPError

from helpers import IPodTestCase, app_module

URL = "https://192-168-1-219.abc123def.plex.direct:32400"
NEW = "https://192-168-1-50.abc123def.plex.direct:32400"


class PlexDirectIdTests(unittest.TestCase):
    def setUp(self):
        self.extract = app_module().plexapi._plex_direct_id

    def test_extracts_the_server_id(self):
        self.assertEqual(self.extract(URL), "abc123def")

    def test_a_plain_address_has_none(self):
        self.assertEqual(self.extract("http://192.168.1.219:32400"), "")
        self.assertEqual(self.extract(""), "")


class FindServerTests(unittest.TestCase):
    def setUp(self):
        self.plexapi = app_module().plexapi

    def server(self, name, uri):
        return {"name": name, "owned": True, "token": "t-" + name,
                "connections": [{"uri": uri, "local": True}]}

    def find(self, servers, reachable, previous=URL):
        pick = lambda s: ((s["connections"][0]["uri"], s["token"])
                          if s["name"] in reachable else (None, s["token"]))
        with mock.patch.object(self.plexapi, "plex_list_servers",
                               return_value=servers), \
                mock.patch.object(self.plexapi, "plex_pick_connection",
                                  side_effect=pick):
            return self.plexapi.plex_find_server("cid", "tok", previous)

    def test_the_server_behind_the_old_url_is_preferred(self):
        servers = [self.server("other", "https://9-9-9-9.zzz.plex.direct:1"),
                   self.server("mine", NEW)]
        self.assertEqual(self.find(servers, {"other", "mine"}),
                         (NEW, "t-mine"))

    def test_falls_back_to_any_reachable_server(self):
        servers = [self.server("mine", NEW),
                   self.server("other", "https://9-9-9-9.zzz.plex.direct:1")]
        self.assertEqual(self.find(servers, {"other"})[1], "t-other")

    def test_nothing_reachable_is_none(self):
        self.assertIsNone(self.find([self.server("mine", NEW)], set()))

    def test_a_plex_tv_failure_is_none(self):
        with mock.patch.object(self.plexapi, "plex_list_servers",
                               side_effect=HTTPError("u", 401, "no", {}, None)):
            self.assertIsNone(self.plexapi.plex_find_server("c", "t", URL))


class PingTests(unittest.TestCase):
    def test_nothing_listening_is_false_and_quick(self):
        import socket, time
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        client = app_module().plexapi.PlexClient("http://127.0.0.1:%d" % port,
                                                 "t")
        start = time.monotonic()
        self.assertFalse(client.ping(timeout=2))
        self.assertLess(time.monotonic() - start, 3)


class _Client:
    """PlexClient stand-in. `alive` lists base URLs that answer."""

    alive = set()
    status = None

    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def ping(self, timeout=5):
        return self.url in self.alive

    def get_playlists(self):
        if self.status:
            raise HTTPError(self.url, self.status, "x", {}, None)
        if self.url not in self.alive:
            raise OSError("No route to host")
        return []

    def get_music_section_id(self):
        return "3"


class ConnectWorkerTests(IPodTestCase):
    def run_connect(self, alive, found=None, status=None):
        app = self.bare_app()
        app.cfg = {"plex_url": URL, "plex_token": "tok", "client_id": "cid"}
        app.cfg_mgr = mock.Mock()
        app._status_var = mock.Mock()
        app._url_var = mock.Mock()
        app._token_var = mock.Mock()
        app._on_connected = mock.Mock()
        client = type("C", (_Client,), {"alive": set(alive),
                                        "status": status})
        module = self.p2i.app
        with mock.patch.object(module, "PlexClient", client), \
                mock.patch.object(module, "plex_find_server",
                                  return_value=found) as finder:
            app._connect_worker()
        app.finder = finder
        return app

    def test_a_reachable_server_skips_the_lookup(self):
        app = self.run_connect({URL})
        app.finder.assert_not_called()
        app._on_connected.assert_called_once()

    def test_a_moved_server_is_found_and_remembered(self):
        app = self.run_connect({NEW}, found=(NEW, "tok2"))
        app._on_connected.assert_called_once()
        self.assertEqual(app.plex.url, NEW)
        self.assertEqual(app.cfg["plex_url"], NEW)
        self.assertEqual(app.cfg["plex_token"], "tok2")
        app.cfg_mgr.save.assert_called()
        self.assertIn("now using " + NEW, "\n".join(app.logs))

    def test_a_server_that_cannot_be_found_reports_failure(self):
        app = self.run_connect(set(), found=None)
        app._on_connected.assert_not_called()
        last = app._status_var.set.call_args[0][0]
        self.assertTrue(last.startswith("Connection failed"), last)
        self.assertEqual(app.cfg["plex_url"], URL)

    def test_an_expired_sign_in_says_so(self):
        app = self.run_connect({URL}, status=401)
        last = app._status_var.set.call_args[0][0]
        self.assertIn("Sign in to Plex", last)


if __name__ == "__main__":
    unittest.main()
