"""Turning Plex metadata into names and paths a FAT32 iPod can hold."""


def sort_key(name):
    """Alphabetical sort key. Ignores leading 'The ' (case-insensitive)
    and is case-insensitive overall."""
    if not name:
        return ""
    s = name.strip()
    if s.lower().startswith("the "):
        s = s[4:].strip()
    return s.lower()


_INVALID_FAT_CHARS = '<>:"/\\|?*'

# Windows reserves these device names. "CON.flac" cannot be created at all,
# and the failure arrives as an opaque open()/mkdir error mid-sync rather
# than anything that points at the name. The extension does not help — the
# reservation applies to the stem.
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)

# FAT32 long filenames cap one path component at 255 characters.
MAX_COMPONENT_LEN = 255


def sanitize_component(name):
    """Make a single path component safe for a FAT32 iPod volume.

    Strips characters Windows/FAT can't store and trailing dots/spaces
    (which Windows silently drops, causing 'file not found' mismatches),
    escapes reserved device names, and caps the length at what FAT32 can
    actually hold.
    """
    if not name:
        return "Unknown"
    cleaned = "".join("_" if c in _INVALID_FAT_CHARS else c for c in name)
    # Control chars -> underscore
    cleaned = "".join(c if ord(c) >= 32 else "_" for c in cleaned)
    cleaned = cleaned.rstrip(" .").strip()
    if not cleaned:
        return "Unknown"

    # "CON", "con.flac", "Com1.mp3" are all reserved. Prefixing keeps the
    # name recognizable while making it storable.
    stem = cleaned.split(".", 1)[0]
    if stem.upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned

    if len(cleaned) > MAX_COMPONENT_LEN:
        root, dot, ext = cleaned.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            # Keep the extension — Rockbox picks the decoder from it.
            cleaned = root[:MAX_COMPONENT_LEN - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:MAX_COMPONENT_LEN]
        cleaned = cleaned.rstrip(" .") or "Unknown"
    return cleaned


def ipod_rel_path(track):
    """Build the iPod-relative path (Artist/Album/filename) for a track
    purely from its Plex metadata — no local filesystem access needed.
    Returned with forward slashes; callers convert separators as needed."""
    artist = sanitize_component(track.get("artist") or "Unknown Artist")
    album = sanitize_component(track.get("album") or "Unknown Album")
    fname = sanitize_component(track.get("filename") or "")
    if not fname or fname == "Unknown":
        # Synthesize a name from the title + container if Plex gave us
        # no usable original filename.
        title = sanitize_component(track.get("title") or "track")
        ext = (track.get("container") or "flac").lstrip(".")
        fname = f"{title}.{ext}"
    return f"{artist}/{album}/{fname}"


def find_path_collisions(tracks):
    """Group tracks that would be written to the same place on the iPod.

    The destination is built from Artist/Album/filename, and two different
    Plex tracks can share all three — two releases of the same album, a
    multi-disc set whose discs both start at "01 ...", or a compilation
    duplicated under the same artist. Only one file can occupy a path, so
    without a warning the loser is simply never synced and its playlist
    entry points at the winner's audio.

    Returns {relative_path: [track, ...]} for paths claimed by more than
    one distinct media part. Renaming is deliberately not attempted: the
    recovery features map iPod files back to Plex through this same path,
    so a rename that those cannot reproduce would make Verify & Repair
    treat the file as an orphan and delete it.
    """
    by_path = {}
    for track in tracks:
        by_path.setdefault(ipod_rel_path(track).lower(), []).append(track)
    return {rel: group for rel, group in by_path.items()
            if len({t.get("part_key") for t in group}) > 1}
