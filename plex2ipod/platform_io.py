"""Platform layer \u2014 the only place that knows how an iPod is mounted and
ejected differs between Windows (drive letters, Shell.Application) and
Linux (mount points under /media etc., udisks). Everything else in the
app works in terms of an "iPod root path".
"""

import os
import subprocess

IS_WINDOWS = os.name == "nt"


def list_ipod_roots():
    """Return candidate iPod root paths for the current OS, newest mounts
    last. On Windows these are drive roots like 'E:\\'; on Linux they are
    removable mount points like '/media/you/IPOD'."""
    roots = []
    if IS_WINDOWS:
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            r = letter + ":\\"
            if os.path.exists(r):
                roots.append(r)
    else:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get("USER", "")
        bases = []
        if user:
            bases += [f"/media/{user}", f"/run/media/{user}"]
        bases += ["/media", "/mnt"]
        seen = set()
        for base in bases:
            try:
                entries = sorted(os.listdir(base))
            except OSError:
                continue
            for name in entries:
                p = os.path.join(base, name)
                if p in seen:
                    continue
                seen.add(p)
                try:
                    if os.path.isdir(p) and os.path.ismount(p):
                        roots.append(p)
                except OSError:
                    pass
    return roots


def looks_like_ipod(root):
    """True if a mounted volume looks like a (Rockbox) iPod: it has a
    .rockbox folder, an iPod_Control folder, or a Music/music folder.
    Lets us auto-pick the real device instead of the system drive."""
    try:
        names = {n.lower() for n in os.listdir(root)}
    except OSError:
        return False
    return bool(names & {".rockbox", "ipod_control", "music"})


def detect_ipod_roots():
    """Candidate roots that actually look like an iPod, best first."""
    return [r for r in list_ipod_roots() if looks_like_ipod(r)]


def music_folder_name(root):
    """Return the on-disk music folder name on the iPod ('Music' vs
    'music'). Rockbox playlist entries can be case-sensitive, so match the
    actual casing. Falls back to 'Music' if the volume isn't readable."""
    try:
        for entry in os.listdir(root):
            if entry.lower() == "music" and os.path.isdir(os.path.join(root, entry)):
                return entry
    except OSError:
        pass
    return "Music"


def _no_console_run(cmd, timeout):
    """subprocess.run that never flashes a console window on Windows."""
    kw = {"capture_output": True, "text": True, "timeout": timeout}
    if IS_WINDOWS:
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.run(cmd, **kw)


def eject_volume(root):
    """Safely eject/unmount the iPod. Returns (ok, message)."""
    if IS_WINDOWS:
        return _eject_windows(root)
    return _eject_linux(root)


def _eject_windows(root):
    drive_letter = root[0]
    # Enumerate the Eject verb on the drive's Shell folder item and call
    # DoIt(). Passed via -EncodedCommand (base64 UTF-16LE) to bypass
    # cmd.exe quoting, which otherwise mangles the trailing backslash in
    # 'E:\\' and breaks the script. [char]92 builds the backslash at
    # runtime so no literal '\' ever sits next to a quote.
    script = (
        "$ErrorActionPreference='Stop'\n"
        f"$drive='{drive_letter}:' + [char]92\n"
        "$shell = New-Object -ComObject Shell.Application\n"
        "$item = $shell.Namespace(17).ParseName($drive)\n"
        "if (-not $item) { Write-Error 'Drive not found in My Computer'; exit 2 }\n"
        "$done = $false\n"
        "foreach ($v in $item.Verbs()) {\n"
        "  $n = $v.Name -replace '&',''\n"
        "  if ($n -match '^(Eject|Safely Remove|Disconnect)$') {\n"
        "    $v.DoIt(); $done = $true; break\n"
        "  }\n"
        "}\n"
        "if (-not $done) { $item.InvokeVerb('Eject') }\n"
        "Start-Sleep -Milliseconds 1500\n"
        f"if (Test-Path ('{drive_letter}:' + [char]92)) {{\n"
        "  Write-Error 'Drive still present after eject (in use?)'; exit 3\n"
        "}\n"
        "Write-Output 'OK'\n"
    )
    import base64
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = _no_console_run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded], timeout=20)
    except subprocess.TimeoutExpired:
        return False, "Eject timed out. The drive may be in use."
    if result.returncode == 0 and "OK" in (result.stdout or ""):
        return True, "OK"
    err = (result.stderr or result.stdout or "Unknown error").strip()
    return False, " ".join(err.split())


def _eject_linux(root):
    # Resolve the block device backing this mount point, unmount it via
    # udisks (no root needed for removable media), then power it off.
    try:
        dev = _no_console_run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", root],
            timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        dev = ""
    if not dev:
        return False, ("Could not find the device for this mount point. "
                       "Unmount it from your file manager instead.")
    try:
        r = _no_console_run(["udisksctl", "unmount", "-b", dev], timeout=30)
    except FileNotFoundError:
        return False, ("'udisksctl' not found. Install it with "
                       "'sudo apt install udisks2', or unmount from your "
                       "file manager.")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "unmount failed").strip()
    # Best-effort spin-down / power-off; safe to ignore failures.
    try:
        _no_console_run(["udisksctl", "power-off", "-b", dev], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return True, "OK"
