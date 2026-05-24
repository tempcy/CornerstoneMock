# -*- mode: python ; coding: utf-8 -*-
# 两个 EXE 须各自 Analysis + MERGE，否则共用一个 a.scripts 时 UI 会误跑 run_bridge.py
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

hidden_bridge = collect_submodules("cornerstone_bridge") + collect_submodules("cornerstone_cli")
hidden_ui = hidden_bridge + collect_submodules("cornerstone_bridge.ui")
datas_cli, binaries_cli, hidden_cli = collect_all("cornerstone_cli")
datas_br, binaries_br, hidden_br = collect_all("cornerstone_bridge")
datas_pyside, binaries_pyside, hidden_pyside = collect_all("PySide6")

a_bridge = Analysis(
    [os.path.join(SPECPATH, "..", "entrypoints", "run_bridge.py")],
    pathex=[
        os.path.join(SPECPATH, "..", "..", "CornerstoneBridge", "src"),
        os.path.join(SPECPATH, "..", "..", "CornerstoneCLI", "src"),
    ],
    binaries=binaries_cli + binaries_br,
    datas=datas_cli + datas_br,
    hiddenimports=hidden_bridge + hidden_cli + hidden_br,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

a_ui = Analysis(
    [os.path.join(SPECPATH, "..", "entrypoints", "run_bridge_ui.py")],
    pathex=[
        os.path.join(SPECPATH, "..", "..", "CornerstoneBridge", "src"),
        os.path.join(SPECPATH, "..", "..", "CornerstoneCLI", "src"),
    ],
    binaries=binaries_cli + binaries_br + binaries_pyside,
    datas=datas_cli + datas_br + datas_pyside,
    hiddenimports=hidden_ui + hidden_cli + hidden_br + hidden_pyside,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

MERGE((a_bridge, "bridge", "bridge"), (a_ui, "ui", "bridge"))

pyz_bridge = PYZ(a_bridge.pure, a_bridge.zipped_data, cipher=block_cipher)
pyz_ui = PYZ(a_ui.pure, a_ui.zipped_data, cipher=block_cipher)

exe_bridge = EXE(
    pyz_bridge,
    a_bridge.scripts,
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
    uac_admin=False,
)

exe_ui = EXE(
    pyz_ui,
    a_ui.scripts,
    [],
    exclude_binaries=True,
    name="cornerstone-bridge-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)

coll = COLLECT(
    exe_bridge,
    exe_ui,
    a_bridge.binaries,
    a_bridge.zipfiles,
    a_bridge.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cornerstone-bridge",
)
