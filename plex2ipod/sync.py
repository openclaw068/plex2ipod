"""Planning where each Plex track lands on the iPod, and writing playlists."""

import os
import posixpath

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


# Anything Rockbox might play. A folder holding one of these is never
# treated as clutter, whatever else is in it.
AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".m4b", ".aac", ".alac",
                    ".ogg", ".oga", ".opus", ".wav", ".aif", ".aiff", ".ape",
                    ".wv", ".mpc", ".wma", ".mka", ".dsf", ".dff")


def find_clutter(music_root):
    """Folders under music_root with no music in them at any depth.

    Returns [(path, file count, bytes), ...] for the topmost such folders
    only, so a release folder full of leftovers is one entry rather than
    one per subfolder. A folder that could not be read counts as holding
    music: what was not seen is never offered for deletion.
    """
    has_music = {}
    totals = {}
    for dirpath, dirnames, filenames in os.walk(music_root, topdown=False):
        music = any(n.lower().endswith(AUDIO_EXTENSIONS) for n in filenames)
        count, size = len(filenames), 0
        for name in filenames:
            try:
                size += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
        for name in dirnames:
            child = os.path.join(dirpath, name)
            music = music or has_music.get(child, True)
            sub_count, sub_size = totals.get(child, (0, 0))
            count, size = count + sub_count, size + sub_size
        has_music[dirpath] = music
        totals[dirpath] = (count, size)
    clutter = []
    for path, music in has_music.items():
        if music or path == music_root:
            continue
        parent = os.path.dirname(path)
        if parent == music_root or has_music.get(parent, True):
            clutter.append((path,) + totals[path])
    return sorted(clutter)


# Sidecars left by an interrupted download, copy or conversion. Never music.
LEFTOVER_SUFFIXES = (".part", ".flac.tmp")


def index_device(music_root):
    """Walk the iPod's music folder once.

    Returns (files, leftovers): files maps each non-empty file's lowercased
    path relative to music_root (forward slashes) to (real path, size);
    leftovers lists the real paths of interrupted-transfer sidecars.
    """
    files = {}
    leftovers = []
    for dirpath, _dirs, names in os.walk(music_root):
        for name in names:
            full = os.path.join(dirpath, name)
            if name.lower().endswith(LEFTOVER_SUFFIXES):
                leftovers.append(full)
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size <= 0:
                continue
            rel = os.path.relpath(full, music_root).replace(os.sep, "/")
            files[rel.lower()] = (full, size)
    return files, leftovers


def group_by_name_and_size(sizes):
    """{(lowercased basename, size): [rel, ...]} from {rel: size}."""
    groups = {}
    for rel, size in sizes.items():
        groups.setdefault((posixpath.basename(rel), size), []).append(rel)
    return groups


def relocation_source(track, sizes, by_name_size, claimed):
    """The lowercased path of a file already on the iPod that *is* this
    track, sitting somewhere other than where the track belongs; or None.

    Moving such a file into place is a rename on the device: no data
    crosses USB. Two kinds of evidence count:

    * The file's path is the tail of the track's path on the server, at
      least folder/filename deep. Music copied to the iPod by hand keeps
      the server's folder names, so this finds it even if it was later
      converted and its size changed.
    * It is the only file with the track's filename and exact size. Plex
      renames (a corrected artist or album) move where the app expects a
      track without changing the file itself.

    `claimed` holds paths that must stay put: where other tracks belong.
    """
    candidates = []
    parts = [p for p in (track.get("server_path") or "").lower().split("/")
             if p]
    for depth in range(len(parts), 1, -1):
        candidates.append("/".join(parts[-depth:]))
    try:
        size = int(track.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    name = (track.get("filename") or "").lower()
    if size > 0 and name:
        matches = [rel for rel in by_name_size.get((name, size), ())
                   if rel not in claimed]
        if len(matches) == 1:
            candidates.append(matches[0])
    for rel in candidates:
        if rel in sizes and rel not in claimed:
            return rel
    return None
