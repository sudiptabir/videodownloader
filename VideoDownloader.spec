# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Video Downloader.

Bundles the Flask templates and the bundled ffmpeg/ffprobe binaries alongside
the executable, and pulls in yt-dlp's dynamically-imported modules.
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

datas = [
    ("templates", "templates"),
    ("bin", "bin"),
]

# yt-dlp loads many extractors dynamically; collect them all.
hiddenimports = collect_submodules("yt_dlp")
datas += collect_data_files("yt_dlp")


block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # keep a console so the user sees the local URL + status
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VideoDownloader",
)
