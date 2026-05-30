from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, Optional, Tuple


def _hidden_subprocess_kwargs() -> Dict[str, Any]:
    """Windows GUI 下调用 ``net`` / ``sc`` 时不弹出控制台窗口。"""
    if sys.platform != "win32":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        return {"creationflags": flags}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"startupinfo": si}


def _run_hidden(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_hidden_subprocess_kwargs(),
        **kwargs,
    )


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


def windows_service_state(service_name: str) -> Optional[str]:
    """``running`` | ``stopped`` | ``pending``；服务不存在时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    r = _run_hidden(["sc", "query", service_name])
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if "STATE" not in line:
            continue
        upper = line.upper()
        if "RUNNING" in upper:
            return "running"
        if "STOPPED" in upper:
            return "stopped"
        return "pending"
    return None


def windows_service_stop(service_name: str) -> Tuple[bool, str]:
    """停止服务；已停止视为成功。"""
    state = windows_service_state(service_name)
    if state is None:
        return False, f"未找到 Windows 服务「{service_name}」。"
    if state == "stopped":
        return True, ""
    r = _run_hidden(["net", "stop", service_name])
    if r.returncode == 0 or windows_service_state(service_name) == "stopped":
        return True, ""
    return False, (r.stderr or r.stdout or "停止失败").strip()


def windows_service_start(service_name: str) -> Tuple[bool, str]:
    """启动服务；已在运行视为成功。"""
    state = windows_service_state(service_name)
    if state is None:
        return False, f"未找到 Windows 服务「{service_name}」。"
    if state == "running":
        return True, ""
    r = _run_hidden(["net", "start", service_name])
    if r.returncode == 0 or windows_service_state(service_name) == "running":
        return True, ""
    return False, (r.stderr or r.stdout or "启动失败").strip()
