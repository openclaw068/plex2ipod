"""Talking to plex.tv and to a Plex Media Server.

Sign-in uses the plex.tv PIN flow: request a short code, the user enters
it at plex.tv/link in a browser, and Plex hands back a token. We then ask
plex.tv which servers the account owns and auto-fill the server URL.
"""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from xml.etree import ElementTree

from .platform_io import IS_WINDOWS
from .version import APP_VERSION

PLEX_TV = "https://plex.tv/api/v2"


def _plex_headers(client_id, token=None):
    h = {
        "X-Plex-Product": "Plex2iPod",
        "X-Plex-Version": APP_VERSION,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Device": "Plex2iPod",
        "X-Plex-Platform": "Windows" if IS_WINDOWS else "Linux",
        "Accept": "application/json",
    }
    if token:
        h["X-Plex-Token"] = token
    return h


def plex_create_pin(client_id):
    """Ask plex.tv for a login PIN. Returns (pin_id, code). Uses a normal
    (non-strong) PIN so the user gets a short 4-character code that can be
    typed at plex.tv/link."""
    data = urlencode({"strong": "false"}).encode()
    req = Request(f"{PLEX_TV}/pins", data=data,
                  headers=_plex_headers(client_id), method="POST")
    with urlopen(req, timeout=15) as resp:
        j = json.load(resp)
    return j["id"], j["code"]


def plex_check_pin(client_id, pin_id):
    """Return the authToken once the user has linked the PIN, else None."""
    req = Request(f"{PLEX_TV}/pins/{pin_id}",
                  headers=_plex_headers(client_id))
    with urlopen(req, timeout=15) as resp:
        j = json.load(resp)
    return j.get("authToken")


def plex_list_servers(client_id, token):
    """Return owned/accessible Plex Media Servers and their connections."""
    req = Request(f"{PLEX_TV}/resources?includeHttps=1&includeRelay=1",
                  headers=_plex_headers(client_id, token))
    with urlopen(req, timeout=20) as resp:
        resources = json.load(resp)
    servers = []
    for r in resources:
        if "server" not in (r.get("provides") or ""):
            continue
        servers.append({
            "name": r.get("name") or "Plex Server",
            "owned": bool(r.get("owned")),
            "token": r.get("accessToken") or token,
            "connections": r.get("connections") or [],
        })
    # Owned servers first
    servers.sort(key=lambda s: 0 if s["owned"] else 1)
    return servers


def plex_pick_connection(server, timeout=5):
    """Find a working base URL for a server by trying its connections,
    preferring a local (LAN) address over remote, and remote over relay."""
    def rank(c):
        return (0 if c.get("local") else 1, 1 if c.get("relay") else 0)
    candidates = []
    for c in sorted(server["connections"], key=rank):
        if c.get("uri"):
            candidates.append(c["uri"])
        addr, port, proto = c.get("address"), c.get("port"), c.get("protocol")
        if addr and port:
            candidates.append(f"{proto or 'http'}://{addr}:{port}")
    seen = set()
    for uri in candidates:
        if uri in seen:
            continue
        seen.add(uri)
        try:
            req = Request(uri.rstrip("/") + "/identity",
                          headers={"X-Plex-Token": server["token"],
                                   "Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return uri.rstrip("/"), server["token"]
        except (URLError, HTTPError, OSError):
            continue
    return None, server["token"]


class PlexClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, path):
        sep = "&" if "?" in path else "?"
        url = f"{self.base_url}{path}{sep}X-Plex-Token={self.token}"
        # Plex returns 503 when overloaded (lots of parallel artist
        # expansions for example). Back off and retry a few times so
        # transient overload doesn't surface as a hard failure.
        last_err = None
        for attempt in range(5):
            try:
                with urlopen(url, timeout=20) as resp:
                    return ElementTree.parse(resp).getroot()
            except HTTPError as e:
                last_err = e
                if e.code in (502, 503, 504):
                    time.sleep(0.5 * (2 ** attempt))  # 0.5,1,2,4,8s
                    continue
                raise
            except (URLError, OSError) as e:
                last_err = e
                time.sleep(0.5 * (2 ** attempt))
        raise last_err

    def download_part(self, part_key, dest_path, cancel_check=None):
        """Stream a media Part from the Plex server to dest_path. Returns
        (True, None) on success or (False, error_msg) on failure. Writes
        to a .part sidecar and renames on completion so a partial download
        never masquerades as a finished file. cancel_check() -> bool lets
        a long download abort cleanly."""
        if not part_key:
            return False, "no download key for this track"
        sep = "&" if "?" in part_key else "?"
        url = f"{self.base_url}{part_key}{sep}X-Plex-Token={self.token}"
        tmp = dest_path + ".part"
        last_err = None
        for attempt in range(3):
            try:
                with urlopen(url, timeout=30) as resp, open(tmp, "wb") as out:
                    while True:
                        if cancel_check and cancel_check():
                            out.close()
                            self._safe_remove(tmp)
                            return False, "cancelled"
                        chunk = resp.read(262144)  # 256 KB
                        if not chunk:
                            break
                        out.write(chunk)
                # Success — hand the finished temp file back to the caller,
                # which decides whether to downsample or atomically place it.
                return True, tmp
            except HTTPError as e:
                last_err = e
                if e.code in (502, 503, 504):
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                self._safe_remove(tmp)
                return False, f"HTTP {e.code}"
            except (URLError, OSError) as e:
                last_err = e
                time.sleep(0.5 * (2 ** attempt))
        self._safe_remove(tmp)
        return False, str(last_err)

    @staticmethod
    def _safe_remove(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def get_playlists(self):
        root = self._request("/playlists/all/")
        playlists = []
        for el in root.findall("Playlist"):
            if el.get("playlistType") != "audio":
                continue
            playlists.append({
                "id": el.get("ratingKey"),
                "title": el.get("title"),
                "leaf_count": int(el.get("leafCount", 0)),
                "smart": el.get("smart") == "1",
            })
        return playlists

    def get_playlist_tracks(self, playlist_id):
        tracks = []
        offset = 0
        while True:
            root = self._request(
                f"/playlists/{playlist_id}/items"
                f"?X-Plex-Container-Start={offset}"
                f"&X-Plex-Container-Size=200"
            )
            for el in root.findall("Track"):
                t = self._parse_track(el)
                if t:
                    tracks.append(t)
            total = int(root.get("totalSize", root.get("size", 0)))
            offset += 200
            if offset >= total:
                break
        return tracks

    def get_music_section_id(self):
        root = self._request("/library/sections/")
        for el in root.findall("Directory"):
            if el.get("type") == "artist":
                return el.get("key")
        return None

    def get_artists(self, section_id):
        artists = []
        offset = 0
        while True:
            root = self._request(
                f"/library/sections/{section_id}/all?type=8"
                f"&X-Plex-Container-Start={offset}"
                f"&X-Plex-Container-Size=200"
            )
            for el in root.findall("Directory"):
                artists.append({
                    "title": el.get("title"),
                    "key": el.get("key"),
                    "rating_key": el.get("ratingKey"),
                })
            total = int(root.get("totalSize", root.get("size", 0)))
            offset += 200
            if offset >= total:
                break
        return artists

    def get_artist_albums(self, artist_key):
        root = self._request(artist_key)
        albums = []
        for el in root.findall("Directory"):
            albums.append({
                "title": el.get("title"),
                "key": el.get("key"),
                "rating_key": el.get("ratingKey"),
                "year": el.get("year", ""),
            })
        return albums

    def get_album_tracks(self, album_key):
        root = self._request(album_key)
        tracks = []
        for el in root.findall("Track"):
            t = self._parse_track(el)
            if t:
                tracks.append(t)
        return tracks

    def get_all_tracks(self, section_id, progress_cb=None):
        """Fetch every track in the music section in one paged sweep
        (type=10). Used to build a lookup for the recovery features so
        they can re-download any iPod file straight from Plex."""
        tracks = []
        offset = 0
        while True:
            root = self._request(
                f"/library/sections/{section_id}/all?type=10"
                f"&X-Plex-Container-Start={offset}"
                f"&X-Plex-Container-Size=200"
            )
            for el in root.findall("Track"):
                t = self._parse_track(el)
                if t:
                    tracks.append(t)
            total = int(root.get("totalSize", root.get("size", 0)))
            offset += 200
            if progress_cb:
                progress_cb(min(offset, total), total)
            if offset >= total or total == 0:
                break
        return tracks

    def _parse_track(self, el):
        part = el.find(".//Part")
        if part is None:
            return None
        part_key = part.get("key")
        if not part_key:
            return None
        # Original on-server filename, used to name the file on the iPod.
        # The server path may use either separator; take the basename of
        # both so a Linux server path ("/music/a/b.flac") still works.
        server_path = part.get("file") or ""
        filename = server_path.replace("\\", "/").rsplit("/", 1)[-1]
        try:
            size = int(part.get("size") or 0)
        except ValueError:
            size = 0
        return {
            "title": el.get("title", ""),
            "artist": el.get("grandparentTitle", ""),
            "album": el.get("parentTitle", ""),
            "duration_ms": int(el.get("duration", 0)),
            "part_key": part_key,
            "filename": filename,
            "container": part.get("container", ""),
            "size": size,
        }
