"""ffmpeg/ffprobe wrapper for FLAC downsampling.

Used by the sync-time checkbox and the Manage iPod "Downsample 24-bit
Tracks" scan-and-replace feature. Converts 24-bit FLACs to 16-bit with
triangular high-pass dither. Sample rate is preserved; tags are kept and
embedded cover art is dropped.
"""

import os
import shutil
import subprocess
import time

from .paths import resource_dirs
from .platform_io import IS_WINDOWS


class AudioConverter:
    """Wraps bundled ffmpeg/ffprobe for FLAC downsampling."""

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

    def downsample(self, src, dst, timeout=300):
        """Convert src -> dst as 16-bit FLAC. Sample rate is preserved.
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
        # No osr= means sample rate is preserved.
        af = "aresample=dither_method=triangular_hp"
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
