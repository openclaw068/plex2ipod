"""ffmpeg/ffprobe wrapper for making hi-res FLACs iPod-sized.

Used by the sync-time checkbox and the Manage iPod "Shrink Hi-Res"
scan-and-replace feature. Converts anything above 16-bit to 16-bit with
triangular high-pass dither, and anything above 48 kHz down to 48 or
44.1 kHz. Tags are kept and embedded cover art is dropped.

The iPod's audio hardware tops out at 48 kHz, so Rockbox resamples a
96 kHz file on the fly anyway: storing it at 96 kHz costs twice the space,
twice the USB transfer time and extra battery, for nothing audible.
"""

import json
import os
import shutil
import subprocess
import time

from .paths import resource_dirs
from .platform_io import IS_WINDOWS


# Highest sample rate worth storing on the iPod; see the module docstring.
MAX_SAMPLE_RATE = 48000


def ipod_sample_rate(rate):
    """The rate to resample `rate` to for the iPod, or None to keep it.

    Stays in the source's family, 44.1 kHz multiples to 44.1 and the rest
    to 48, so the conversion is a clean 2:1 or 4:1.
    """
    if not rate or rate <= MAX_SAMPLE_RATE:
        return None
    return 44100 if rate % 44100 == 0 else 48000


def needs_conversion(info):
    """True if a probed FLAC is bigger than the iPod can make use of."""
    info = info or {}
    return ((info.get("bit_depth") or 16) > 16
            or ipod_sample_rate(info.get("sample_rate")) is not None)


def describe(info):
    """'24-bit/96 kHz' style label for a probe result."""
    info = info or {}
    rate = info.get("sample_rate")
    khz = f"{rate / 1000:g} kHz" if rate else "? kHz"
    return f"{info.get('bit_depth') or 16}-bit/{khz}"


def converted_info(info):
    """What needs_conversion's conversion produces from `info`."""
    info = info or {}
    rate = info.get("sample_rate")
    return {"bit_depth": 16, "sample_rate": ipod_sample_rate(rate) or rate}


def estimated_size(size, info):
    """Bytes a FLAC of `size` will take once converted for the iPod.

    Scaled by the bits and samples kept. Measured against real conversions
    this is exact for resampled files (96 kHz to 48 kHz keeps 33%, 192 kHz
    to 48 kHz 17%) and about ten points high for 24-bit to 16-bit at the
    same rate (58% rather than 67%): the safe direction for a capacity
    warning.
    """
    if not needs_conversion(info):
        return size
    bits = info.get("bit_depth") or 16
    rate = info.get("sample_rate")
    ratio = 16.0 / bits if bits > 16 else 1.0
    if rate:
        ratio *= converted_info(info)["sample_rate"] / rate
    return int(size * ratio)


class AudioConverter:
    """Wraps bundled ffmpeg/ffprobe for FLAC conversion."""

    def __init__(self):
        self.ffmpeg_path = self._locate("ffmpeg")
        self.ffprobe_path = self._locate("ffprobe")

    @staticmethod
    def _runnable(path):
        """True if path is a file this OS can actually execute. On Linux a
        bundled Windows .exe is a file but not runnable, and treating it as
        usable makes every ffmpeg call fail at spawn time."""
        return os.path.isfile(path) and (IS_WINDOWS or os.access(path, os.X_OK))

    @staticmethod
    def _locate(name):
        """Find ffmpeg/ffprobe in the bundled 'ffmpeg' folder, then on PATH.
        Returns None if not found.

        `name` is the bare tool name; the .exe suffix is applied only on
        Windows. A checkout that carries the bundled Windows binaries — a
        synced folder, or a repo shared between machines — must not pick
        those on Linux and then report itself as available while every
        conversion fails.
        """
        exe = name + ".exe" if IS_WINDOWS else name
        for base in resource_dirs():
            candidate = os.path.join(base, "ffmpeg", exe)
            if AudioConverter._runnable(candidate):
                return candidate
            # also try flat (if user dropped them next to the exe)
            flat = os.path.join(base, exe)
            if AudioConverter._runnable(flat):
                return flat
        # Last resort: a system-installed ffmpeg on PATH. Lets the app
        # downsample for users who didn't get the bundled binaries.
        on_path = shutil.which(exe) or shutil.which(name)
        if on_path:
            return on_path
        return None

    @property
    def available(self):
        return bool(self.ffmpeg_path and self.ffprobe_path)

    # -- no-console subprocess helper (Windows) --
    @staticmethod
    def _no_console_kwargs():
        kw = {"capture_output": True, "text": True}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        return kw

    def probe(self, file_path):
        """Return a dict with 'bit_depth' (int) and 'sample_rate' (int) for
        the first audio stream, or None if probing fails."""
        if not self.ffprobe_path or not os.path.isfile(file_path):
            return None
        try:
            result = subprocess.run(
                [
                    self.ffprobe_path, "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries",
                    "stream=bits_per_raw_sample,bits_per_sample,sample_rate",
                    "-of", "default=nw=1:nk=0",
                    file_path,
                ],
                timeout=10,
                **self._no_console_kwargs(),
            )
            if result.returncode != 0:
                return None
            info = {"bit_depth": None, "sample_rate": None}
            for line in result.stdout.splitlines():
                line = line.strip()
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.strip()
                if not v or v == "N/A":
                    continue
                try:
                    v_int = int(v)
                except ValueError:
                    continue
                if k == "bits_per_raw_sample" and v_int > 0:
                    info["bit_depth"] = v_int
                elif k == "bits_per_sample" and v_int > 0 and not info["bit_depth"]:
                    info["bit_depth"] = v_int
                elif k == "sample_rate":
                    info["sample_rate"] = v_int
            return info
        except (subprocess.TimeoutExpired, OSError):
            return None

    def read_tags(self, file_path):
        """{"artist", "album_artist", "album", "title", "duration"} for a
        file, from its header (no decoding, so cheap over USB), or None.
        Missing tags come back as ""."""
        if not self.ffprobe_path:
            return None
        try:
            result = subprocess.run(
                [self.ffprobe_path, "-v", "error", "-show_entries",
                 "format_tags=artist,album_artist,album,title"
                 ":format=duration", "-of", "json", file_path],
                timeout=15, stdin=subprocess.DEVNULL,
                **self._no_console_kwargs())
            fmt = json.loads(result.stdout or "{}").get("format") or {}
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        try:
            duration = float(fmt.get("duration") or 0)
        except ValueError:
            duration = 0.0
        info = {k: tags.get(k, "") for k in
                ("artist", "album_artist", "album", "title")}
        info["duration"] = duration
        return info

    def downsample(self, src, dst, timeout=600, sample_rate=None):
        """Convert src -> dst as 16-bit FLAC, resampled to sample_rate when
        one is given (see ipod_sample_rate), else at the source's rate.
        Tags are preserved; embedded cover art is dropped. Writes to dst
        atomically via a .tmp sidecar. Returns (True, None) on success,
        (False, err) on failure."""
        if not self.available:
            return False, "ffmpeg not available"
        if not os.path.isfile(src):
            return False, f"source not found: {src}"

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        # Clean up any stale tmp from a previous crashed run
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

        # Use ffmpeg's native swr resampler (soxr isn't in every build).
        # triangular_hp dither is supported by swr and is ideal for 24->16.
        # No osr= means sample rate is preserved. When resampling, a longer
        # filter than swr's default keeps the passband flat to ~20 kHz.
        af = "aresample=dither_method=triangular_hp"
        if sample_rate:
            af = (f"aresample=osr={int(sample_rate)}:filter_size=64:"
                  f"phase_shift=10:dither_method=triangular_hp")
        cmd = [
            self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
            "-i", src,
            "-map", "0:a:0",          # first audio stream only
            "-vn",                    # drop embedded cover art (unreliable
                                       # with -c:v copy + audio filter chain)
            "-c:a", "flac",
            "-sample_fmt", "s16",
            "-af", af,
            "-map_metadata", "0",
            "-f", "flac",             # force muxer; tmp filename ends in .tmp
            tmp,
        ]
        try:
            result = subprocess.run(cmd, timeout=timeout,
                                    **self._no_console_kwargs())
        except subprocess.TimeoutExpired:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
            return False, "ffmpeg timeout"
        except OSError as e:
            return False, f"ffmpeg spawn error: {e}"

        if result.returncode != 0 or not os.path.isfile(tmp) \
                or os.path.getsize(tmp) == 0:
            err = (result.stderr or "").strip().splitlines()
            err_msg = err[-1] if err else f"ffmpeg exit {result.returncode}"
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
            return False, err_msg

        # Atomic replace via os.replace (uses MoveFileEx + REPLACE_EXISTING
        # on Windows). Single syscall — no window where dst is missing.
        # Retry once with a short delay in case the destination is briefly
        # locked by Rockbox's database scan or antivirus.
        last_err = None
        for attempt in range(2):
            try:
                os.replace(tmp, dst)
                return True, None
            except OSError as e:
                last_err = e
                time.sleep(0.4)

        # Replace failed twice. Make sure we never leave a .tmp orphan
        # next to the original — that's what produces the "two copies
        # under Files, neither plays" symptom on the iPod.
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False, f"replace failed: {last_err}"
