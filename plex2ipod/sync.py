"""Planning where each Plex track lands on the iPod, and writing playlists."""

import os

from .naming import ipod_rel_path, sanitize_component
from .platform_io import music_folder_name


class SyncEngine:
    """Plans where each Plex track lands on the iPod and writes playlists.
    Destination layout is built entirely from track metadata
    (Artist/Album/filename) so it needs no access to the Plex server's
    own filesystem — the media itself is downloaded over HTTP."""

    def __init__(self, ipod_root):
        self.ipod_root = ipod_root
        # Resolve the on-disk music folder casing ('Music' vs 'music') once.
        # music_folder_name() lists the volume root, and this used to run
        # for every track in dest_path() and again in m3u_path() — several
        # thousand directory listings over a large sync, all on a slow USB
        # device. The casing cannot change mid-sync, so cache it.
        self.music_folder = music_folder_name(ipod_root)
        self._music_dir = os.path.join(ipod_root, self.music_folder)

    def ipod_music_dir(self):
        return self._music_dir

    def ipod_playlist_dir(self):
        return os.path.join(self.ipod_root, "Playlists")

    def dest_path(self, track):
        rel = ipod_rel_path(track).replace("/", os.sep)
        return os.path.join(self._music_dir, rel)

    def m3u_path(self, track):
        return "/" + self.music_folder + "/" + ipod_rel_path(track)

    def build_sync_plan(self, tracks):
        """Split tracks into (to_copy, already_exist).

        A zero-byte file counts as missing: it is what an interrupted or
        failed write leaves behind, and treating it as synced means the
        track can never be recovered by syncing again. Size is not checked
        beyond that — a downsampled file legitimately differs in size from
        the copy on the server.
        """
        to_copy = []
        already_exist = []
        for t in tracks:
            dest = self.dest_path(t)
            try:
                present = os.path.getsize(dest) > 0
            except OSError:
                present = False
            if present:
                already_exist.append(t)
            else:
                to_copy.append(t)
        return to_copy, already_exist

    def generate_m3u(self, playlist_name, tracks):
        os.makedirs(self.ipod_playlist_dir(), exist_ok=True)
        safe_name = sanitize_component(playlist_name)
        path = os.path.join(self.ipod_playlist_dir(), safe_name + ".m3u")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for t in tracks:
                dur = t["duration_ms"] // 1000
                label = f"{t['artist']} - {t['title']}" if t["artist"] else t["title"]
                f.write(f"#EXTINF:{dur},{label}\n{self.m3u_path(t)}\n")
