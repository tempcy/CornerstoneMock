"""Windows 单实例：启动前结束同应用的其它进程。"""
from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
from pathlib import Path

_APP_IMAGES: dict[str, str] = {
    "cornerstone-bridge": "cornerstone-bridge.exe",
    "cornerstone-web": "cornerstone-web.exe",
}


def _run_dir() -> Path:
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(base) / "CornerstoneMock" / "run"


def _lock_path(app_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in app_id)
    return _run_dir() / f"{safe}.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        return False
    import ctypes

    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _kill_pid(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform != "win32":
        return False
    import ctypes

    terminate = 0x0001
    handle = ctypes.windll.kernel32.OpenProcess(terminate, False, pid)
    if not handle:
        return False
    ok = bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
    ctypes.windll.kernel32.CloseHandle(handle)
    return ok


def _kill_by_image(image_name: str, except_pid: int) -> list[int]:
    if sys.platform != "win32":
        return []
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            creationflags=creationflags,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    killed: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or "No tasks" in line:
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1].replace(",", ""))
        except ValueError:
            continue
        if pid == except_pid:
            continue
        if _kill_pid(pid):
            killed.append(pid)
    return killed


def _release_lock(path: Path, owner_pid: int) -> None:
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(owner_pid):
            path.unlink()
    except OSError:
        pass


def ensure_single_instance(
    app_id: str,
    *,
    log_prefix: str | None = None,
    wait_after_kill_s: float = 0.5,
) -> None:
    """
    结束同应用其它实例后登记当前 PID。
    打包 exe 时按映像名扫描；开发模式仅依据 PID 锁文件。
    """
    if sys.platform != "win32":
        return

    prefix = log_prefix or app_id
    me = os.getpid()
    run_dir = _run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(app_id)
    killed: list[int] = []

    if lock.exists():
        try:
            old = int(lock.read_text(encoding="utf-8").strip())
        except ValueError:
            old = 0
        if old and old != me and _pid_alive(old) and _kill_pid(old):
            killed.append(old)

    image = _APP_IMAGES.get(app_id)
    if getattr(sys, "frozen", False):
        image = image or Path(sys.executable).name
    if image:
        for pid in _kill_by_image(image, me):
            if pid not in killed:
                killed.append(pid)

    if killed:
        print(f"[{prefix}] 已结束已有实例: PID {', '.join(str(p) for p in killed)}")
        time.sleep(wait_after_kill_s)

    lock.write_text(str(me), encoding="utf-8")
    atexit.register(_release_lock, lock, me)
