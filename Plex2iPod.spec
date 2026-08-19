# -*- mode: python ; coding: utf-8 -*-

# The implementation lives in the plex2ipod package next to Plex2iPod.pyw.
# collect_submodules pulls in every module explicitly, so a new module does
# not have to be remembered here and cannot be silently left out of the exe.
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules('plex2ipod')

a = Analysis(
    ['Plex2iPod.pyw'],
    pathex=[SPECPATH],
    binaries=[('ffmpeg\\ffmpeg.exe', 'ffmpeg'), ('ffmpeg\\ffprobe.exe', 'ffmpeg')],
    datas=[('Plex2iPod.ico', '.')],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Plex2iPod',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Plex2iPod.ico'],
)
