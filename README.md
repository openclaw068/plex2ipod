# Plex2iPod

Sync playlists and albums from a Plex Media Server to a Rockbox-flashed iPod.

Tracks are pulled from Plex over HTTP — you don't need access to the server's
filesystem — and written to the iPod as a plain `Music/Artist/Album/track.flac`
tree plus `.m3u` playlists that Rockbox can read.

![Music folder structure](Music%20Folder%20Structure.png)

## Features

- Browse and select Plex **playlists** (smart playlists included) or drill into
  the **library** by artist → album → track
- **What's already on the iPod starts out ticked.** Fully-synced albums show
  ☑, partly-synced ones ☒, so you adjust from your current state instead of
  re-selecting everything. Un-ticking something that is on the device queues it
  for removal; Sync confirms additions and deletions together before touching
  anything
- Downloads only what's missing; a song in two playlists is fetched once
- **Capacity bar** showing how full the iPod is and what the current selection
  would add. Tracks already on the device cost nothing. If the selection won't
  fit, the bar turns red and Sync offers to fill the device and stop, or to
  cancel so you can trim the selection
- Optional **24-bit → 16-bit FLAC downsampling** with triangular high-pass
  dither, for iPod hardware that can't play hi-res files
- **Manage iPod** tab: browse what's on the device, delete tracks, albums,
  artists or playlists, and strip the deleted entries out of your `.m3u` files
- **Verify & Repair**: probes every FLAC on the iPod and re-downloads broken
  ones from Plex
- **Rebuild Rockbox DB**: clears `.rockbox/database_*.tcd` so Rockbox rescans
- Auto-detects the iPod on Windows (drive letters) and Linux (`/media`, `/mnt`)
- Safe eject from inside the app
- Dark and light themes

## Running from source

Needs Python 3.8+ and Tkinter. No pip packages required.

```bash
python3 Plex2iPod.pyw
```

On Linux there's a launcher that checks for the system packages first:

```bash
bash run_on_linux.sh
```

It will tell you if you're missing `python3-tk`, `ffmpeg`, or `udisks2`.

### ffmpeg

ffmpeg and ffprobe power the downsampling features. They're optional — without
them the app runs fine and just disables those buttons.

- **Linux / macOS**: install from your package manager
  (`sudo apt install ffmpeg`). The app finds them on `PATH`.
- **Windows**: run `python download_ffmpeg.py` to fetch them into `ffmpeg/`.

## Connecting to Plex

Click **Sign in to Plex**. The app requests a short code from plex.tv, opens
your browser to [plex.tv/link](https://plex.tv/link), and waits for you to enter
it. Once you authorize, it retrieves your token, asks plex.tv which servers your
account owns, and fills in the server URL automatically — preferring a LAN
address over a remote one.

If auto-discovery can't reach your server, type the address yourself
(e.g. `http://192.168.1.50:32400`) and click **Connect**.

Your token is saved to `config.json` next to the app. **That file is gitignored
— don't commit it.**

## Building the Windows .exe

```
python download_ffmpeg.py    # fetch ffmpeg.exe + ffprobe.exe first
build_exe.bat
```

The result is a self-contained `dist/Plex2iPod.exe` with ffmpeg bundled inside —
no Python or ffmpeg install needed on the target machine. `config.json` is
created next to the .exe on first run.

To rebuild the icon and logo from scratch: `python make_icon.py`.

## Layout

`Plex2iPod.pyw` is just a launcher; the implementation is a package beside it.

```
Plex2iPod.pyw        launcher — creates the App and runs it
plex2ipod/
├── version.py       APP_VERSION
├── paths.py         where config.json and bundled resources live
├── platform_io.py   mounting, detecting and ejecting an iPod per OS
├── naming.py        Plex metadata → FAT32-safe names and paths
├── config.py        reading and writing config.json
├── plexapi.py       plex.tv sign-in, server discovery, PlexClient
├── audio.py         ffmpeg/ffprobe wrapper for FLAC downsampling
├── sync.py          SyncEngine — destination planning and .m3u writing
├── theme.py         dark and light palettes
├── widgets.py       hand-drawn Tk cards, buttons and checkboxes
└── app.py           the window and all of its behaviour
```

Dependencies run one way, from `app.py` down; nothing imports the package
root, so there are no cycles.

## Tests

```bash
python3 run_tests.py             # everything
python3 run_tests.py -v          # per-test names
python3 run_tests.py test_sync   # one module
python3 run_tests.py --no-gui    # skip anything needing a display
```

Standard library `unittest`, no pytest — same zero-dependency rule as the
app. Tests that need Tk are skipped automatically when there's no display,
and the ffmpeg-dependent ones skip when ffmpeg isn't installed.

| Module | Covers |
| --- | --- |
| `test_paths` | filename sanitizing, reserved names, length caps, path collisions |
| `test_sync_engine` | destination layout, `.m3u` path form, sync planning, folder-name caching |
| `test_sync` | downloading, dedup, collision warnings, `.m3u` lists only real files |
| `test_manage` | deletion, Cancel, playlist cleanup and removal, empty-folder tidying |
| `test_poll` | the iPod detection heartbeat and its idle cost |
| `test_capacity` | free space, selection size, and the over-capacity warning |
| `test_precheck` | reflecting the device in the checkboxes, and the deletion queue |
| `test_wheel` | mouse wheel normalization across X11, Windows and macOS |
| `test_audio` | ffmpeg/ffprobe discovery and platform-correct binary choice |
| `test_gui` | wheel bindings, styled widgets, connect flow, Manage-tab guards |

To check the suite still catches regressions, point it at an older
checkout of the project:

```bash
PLEX2IPOD_ROOT=/path/to/older/checkout python3 run_tests.py
```

## On the iPod

Expected layout, which is what the app writes:

```
E:\
├── Music\Artist\Album\track.flac
├── Playlists\MyPlaylist.m3u
└── .rockbox\
```

After a large sync, use **Rebuild Rockbox DB**, eject, then on the iPod go to
*Settings → General Settings → Database → Initialize now*.

## License

MIT — see [LICENSE](LICENSE).
