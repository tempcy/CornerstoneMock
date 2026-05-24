from __future__ import annotations

import subprocess
import sys


def is_user_admin() -> bool:
    if sys.platform != "win32":
        return True
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> None:
    """非管理员时 UAC 提升并退出当前进程。"""
    if sys.platform != "win32" or is_user_admin():
        return
    import ctypes

    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        executable = sys.executable
        params = subprocess.list2cmdline(["-m", "cornerstone_bridge.ui", *sys.argv[1:]])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    raise SystemExit(0)
