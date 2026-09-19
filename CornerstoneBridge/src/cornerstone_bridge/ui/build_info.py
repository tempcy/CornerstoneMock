from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _candidate_build_info_paths() -> list[Path]:
    if not getattr(sys, "frozen", False):
        return []
    exe_dir = Path(sys.executable).resolve().parent
    # {app}/Bridge/cornerstone-bridge-ui.exe → {app}/build-info.json
    return [
        exe_dir / "build-info.json",
        exe_dir.parent / "build-info.json",
        exe_dir.parent.parent / "build-info.json",
    ]


def _format_built_at(iso: str) -> str:
    raw = iso.strip()
    if not raw:
        return ""
    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return raw[:19].replace("T", " ")
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def load_build_info() -> dict[str, Any]:
    for path in _candidate_build_info_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def packaging_time_label() -> str:
    info = load_build_info()
    built_at = str(info.get("built_at") or "").strip()
    if built_at:
        return _format_built_at(built_at)
    build_id = str(info.get("build_id") or "").strip()
    if build_id and len(build_id) >= 14 and build_id[:14].isdigit():
        ts = build_id[:14]
        return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]} UTC"
    return ""


def package_version_label() -> str:
    """
    窗口标题用版本号。

    安装包优先读 ``build-info.json``（与 Inno / VERSION 一致），避免 PyInstaller
    打进旧的 ``importlib.metadata``（venv 里 editable 安装失败时会残留旧版）。
    """
    info = load_build_info()
    from_build = str(info.get("version") or "").strip()
    if from_build:
        return from_build
    try:
        from cornerstone_bridge import __version__ as pkg_ver

        if pkg_ver:
            return str(pkg_ver).strip()
    except Exception:
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("cornerstone-bridge")
    except Exception:
        return ""
