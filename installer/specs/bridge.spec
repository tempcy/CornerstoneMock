# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

hidden = collect_submodules("cornerstone_bridge") + collect_submodules("cornerstone_cli")
datas_cli, binaries_cli, hidden_cli = collect_all("cornerstone_cli")
datas_br, binaries_br, hidden_br = collect_all("cornerstone_bridge")

a = Analysis(
    [os.path.join(SPECPATH, "..", "entrypoints", "run_bridge.py")],
    pathex=[
        os.path.join(SPECPATH, "..", "..", "CornerstoneBridge", "src"),
        os.path.join(SPECPATH, "..", "..", "CornerstoneCLI", "src"),
    ],
    binaries=binaries_cli + binaries_br,
    datas=datas_cli + datas_br,
    hiddenimports=hidden + hidden_cli + hidden_br,
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
    name="cornerstone-bridge",
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
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cornerstone-bridge",
)
