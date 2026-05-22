# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

hidden = collect_submodules("cornerstone_cli")
datas_pkg, binaries_pkg, hidden_pkg = collect_all("cornerstone_cli")

a = Analysis(
    [os.path.join(SPECPATH, "..", "entrypoints", "run_cli.py")],
    pathex=[os.path.join(SPECPATH, "..", "..", "CornerstoneCLI", "src")],
    binaries=binaries_pkg,
    datas=datas_pkg,
    hiddenimports=hidden + hidden_pkg,
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
    name="cornerstone-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    upx=False,
    upx_exclude=[],
    name="cornerstone-cli",
)
