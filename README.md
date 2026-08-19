# Plex2iPod

Sync playlists and albums from a Plex Media Server to a Rockbox-flashed iPod.

Tracks are pulled from Plex over HTTP — you don't need access to the server's
filesystem — and written to the iPod as a plain `Music/Artist/Album/track.flac`
tree plus `.m3u` playlists that Rockbox can read.

![Music folder structure](Music%20Folder%20Structure.png)

## Features

- Browse and select Plex **playlists** (smart playlists included) or drill into
  the **library** by artist → album → track
- Downloads only what's missing; a song in two playlists is fetched once
- Optional **24-bit → 16-bit FLAC downsampling** with triangular high-pass
  dither, for iPod hardware that can't play hi-res files
- **Manage iPod** tab: browse what's on the device, delete tracks/albums/artists,
  and strip the deleted entries out of your `.m3u` files
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
