# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['Plex2iPod.pyw'],
    pathex=[],
    binaries=[('ffmpeg\\ffmpeg.exe', 'ffmpeg'), ('ffmpeg\\ffprobe.exe', 'ffmpeg')],
    datas=[('Plex2iPod.ico', '.')],
    hiddenimports=[],
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
