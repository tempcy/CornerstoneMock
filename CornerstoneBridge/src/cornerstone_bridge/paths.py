from __future__ import annotations

import os
from pathlib import Path

_QUEUE_JSON_NAME = "cornerstone-bridge.add-samples-queue.json"
_BRIDGE_CONFIG_NAME = "cornerstone-bridge.config.json"
_WEB_CONFIG_NAME = "cornerstone-web.config.json"


def appdata_cornerstone_dir() -> Path:
    """用户配置与队列持久化目录：``%APPDATA%\\CornerstoneMock``。"""
    root = os.environ.get("APPDATA")
    if not root:
        root = str(Path.home() / "AppData" / "Roaming")
    return (Path(root) / "CornerstoneMock").resolve()


def legacy_program_data_cornerstone_dir() -> Path:
    """旧版安装目录（仅作配置/队列迁移回退）。"""
    root = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE") or r"C:\ProgramData"
    return (Path(root) / "CornerstoneMock").resolve()


def program_data_cornerstone_dir() -> Path:
    """已弃用别名，等同于 :func:`appdata_cornerstone_dir`。"""
    return appdata_cornerstone_dir()


def _pick_existing(primary: Path, legacy: Path) -> Path:
    if primary.is_file():
        return primary
    if legacy.is_file():
        return legacy
    return primary


def default_bridge_config_path() -> Path:
    base = appdata_cornerstone_dir()
    leg = legacy_program_data_cornerstone_dir()
    return _pick_existing(base / _BRIDGE_CONFIG_NAME, leg / _BRIDGE_CONFIG_NAME)


def default_web_config_path() -> Path:
    base = appdata_cornerstone_dir()
    leg = legacy_program_data_cornerstone_dir()
    return _pick_existing(base / _WEB_CONFIG_NAME, leg / _WEB_CONFIG_NAME)


def default_queue_persist_path() -> Path:
    base = appdata_cornerstone_dir()
    leg = legacy_program_data_cornerstone_dir()
    return _pick_existing(base / _QUEUE_JSON_NAME, leg / _QUEUE_JSON_NAME)


def expand_config_path(value: str) -> str:
    """展开 ``%APPDATA%``、``%ProgramData%`` 等环境变量与用户目录。"""
    s = str(value or "").strip()
    if not s:
        return s
    return os.path.expandvars(os.path.expanduser(s))
