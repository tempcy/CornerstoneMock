from __future__ import annotations

import os
from pathlib import Path

_QUEUE_JSON_NAME = "cornerstone-bridge.add-samples-queue.json"
_BRIDGE_CONFIG_TOML = "cornerstone-bridge.config.toml"
_BRIDGE_CONFIG_JSON = "cornerstone-bridge.config.json"
_BRIDGE_CONFIG_EXAMPLE_TOML = "cornerstone-bridge.config.example.toml"
_BRIDGE_CONFIG_EXAMPLE_JSON = "cornerstone-bridge.config.example.json"
_WEB_CONFIG_TOML = "cornerstone-web.config.toml"
_WEB_CONFIG_JSON = "cornerstone-web.config.json"
_WEB_CONFIG_EXAMPLE_TOML = "cornerstone-web.config.example.toml"
_WEB_CONFIG_EXAMPLE_JSON = "cornerstone-web.config.example.json"

WEB_CONFIG_SEARCH_NAMES = (
    _WEB_CONFIG_TOML,
    _WEB_CONFIG_EXAMPLE_TOML,
    _WEB_CONFIG_JSON,
    _WEB_CONFIG_EXAMPLE_JSON,
)

BRIDGE_CONFIG_SEARCH_NAMES = (
    _BRIDGE_CONFIG_TOML,
    _BRIDGE_CONFIG_EXAMPLE_TOML,
    _BRIDGE_CONFIG_JSON,
    _BRIDGE_CONFIG_EXAMPLE_JSON,
)

BRIDGE_CONFIG_EXAMPLE_NAMES = (
    _BRIDGE_CONFIG_EXAMPLE_TOML,
    _BRIDGE_CONFIG_EXAMPLE_JSON,
)


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


def _pick_bridge_config_in_dir(base: Path, leg: Path) -> Path:
    for name in (_BRIDGE_CONFIG_TOML, _BRIDGE_CONFIG_JSON):
        picked = _pick_existing(base / name, leg / name)
        if picked.is_file():
            return picked
    return base / _BRIDGE_CONFIG_TOML


def new_default_bridge_config_path() -> Path:
    """新安装默认写入的 Bridge 配置路径（TOML）。"""
    return appdata_cornerstone_dir() / _BRIDGE_CONFIG_TOML


def default_bridge_config_path() -> Path:
    """已存在的 Bridge 配置（TOML 优先于 JSON），无文件时返回默认 TOML 路径。"""
    base = appdata_cornerstone_dir()
    leg = legacy_program_data_cornerstone_dir()
    return _pick_bridge_config_in_dir(base, leg)


def new_default_web_config_path() -> Path:
    """新安装默认写入的 Web 配置路径（TOML）。"""
    return appdata_cornerstone_dir() / _WEB_CONFIG_TOML


def _pick_web_config_in_dir(base: Path, leg: Path) -> Path:
    for name in (_WEB_CONFIG_TOML, _WEB_CONFIG_JSON):
        picked = _pick_existing(base / name, leg / name)
        if picked.is_file():
            return picked
    return base / _WEB_CONFIG_TOML


def default_web_config_path() -> Path:
    """已存在的 Web 配置（TOML 优先于 JSON），无文件时返回默认 TOML 路径。"""
    base = appdata_cornerstone_dir()
    leg = legacy_program_data_cornerstone_dir()
    return _pick_web_config_in_dir(base, leg)


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
