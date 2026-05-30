from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import (
    _BRIDGE_ALLOWED_KEYS,
    parse_bridge_config_text,
    repo_bridge_config_path,
    resolve_bridge_config_path,
    resolve_explicit_config_path,
    write_bridge_config_file,
)
from ..paths import (
    BRIDGE_CONFIG_SEARCH_NAMES,
    appdata_cornerstone_dir,
    default_bridge_config_path,
    expand_config_path,
    new_default_bridge_config_path,
)


def resolve_config_path() -> Path:
    env = (os.environ.get("CORNERSTONE_BRIDGE_CONFIG") or "").strip()
    if env:
        explicit = resolve_explicit_config_path(env)
        if explicit is not None:
            return explicit
        p = Path(expand_config_path(env))
        if p.is_file():
            return p.resolve()
    found = resolve_bridge_config_path()
    if found is not None:
        return found.resolve()
    repo = repo_bridge_config_path()
    if repo is not None:
        return repo
    cwd = Path.cwd()
    for name in BRIDGE_CONFIG_SEARCH_NAMES:
        c = cwd / name
        if c.is_file():
            return c.resolve()
    pkg_root = Path(__file__).resolve().parents[3]
    for name in BRIDGE_CONFIG_SEARCH_NAMES:
        c = pkg_root / name
        if c.is_file():
            return c.resolve()
    return new_default_bridge_config_path().resolve()


def load_config_dict(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or resolve_config_path()
    if not p.is_file():
        return {}
    return parse_bridge_config_text(p.read_text(encoding="utf-8"), path=p)


def save_config_dict(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = path or resolve_config_path()
    base: Dict[str, Any] = {}
    if p.is_file():
        try:
            base = parse_bridge_config_text(p.read_text(encoding="utf-8"), path=p)
        except (ValueError, OSError):
            base = {}
    for k, v in data.items():
        if k in _BRIDGE_ALLOWED_KEYS:
            base[k] = v
    write_bridge_config_file(p, base)
    return p


def merge_config_update(existing: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    for k, v in updates.items():
        if k in _BRIDGE_ALLOWED_KEYS:
            merged[k] = v
    return merged


def api_base_url(cfg: Dict[str, Any]) -> str:
    host = str(cfg.get("bridge_api_host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(cfg.get("bridge_api_port") or 8081)
    except (TypeError, ValueError):
        port = 8081
    return f"http://{host}:{port}"


def log_file_path(cfg: Dict[str, Any]) -> Path:
    raw = str(cfg.get("log_file") or "").strip()
    if not raw:
        return appdata_cornerstone_dir() / "logs" / "bridge.log"
    return Path(expand_config_path(raw)).resolve()
