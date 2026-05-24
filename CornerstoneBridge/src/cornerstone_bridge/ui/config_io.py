from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import repo_bridge_config_path, resolve_bridge_config_path, resolve_explicit_config_path
from ..paths import appdata_cornerstone_dir, default_bridge_config_path, expand_config_path

_CONFIG_NAME = "cornerstone-bridge.config.json"
_EXAMPLE_NAME = "cornerstone-bridge.config.example.json"

_ALLOWED_KEYS = frozenset(
    {
        "host",
        "port",
        "bridge_api_host",
        "bridge_api_port",
        "web_host",
        "web_port",
        "upstream_host",
        "upstream_port",
        "encoding",
        "add_samples_queue_size",
        "no_synthetic_logon",
        "instrument_long_connection",
        "upstream_heartbeat_interval",
        "upstream_inner_reassembly_timeout",
        "upstream_auto_reconnect",
        "async_message_interval",
        "web_user",
        "web_password",
        "privileged_add_samples_host",
        "persist_add_samples_queue",
        "add_samples_queue_persist_file",
        "log_level",
        "log_verbose_gateway",
        "log_file",
        "log_file_level",
        "log_file_max_mb",
        "log_file_backup_count",
        "log_throttle_interval_s",
    }
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
    for name in (_CONFIG_NAME, _EXAMPLE_NAME):
        c = cwd / name
        if c.is_file():
            return c.resolve()
    pkg_root = Path(__file__).resolve().parents[3]
    for name in (_CONFIG_NAME, _EXAMPLE_NAME):
        c = pkg_root / name
        if c.is_file():
            return c.resolve()
    return default_bridge_config_path().resolve()


def load_config_dict(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or resolve_config_path()
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点须为 JSON 对象")
    return raw


def save_config_dict(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = path or resolve_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    base: Dict[str, Any] = {}
    if p.is_file():
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            base = raw
    for k, v in data.items():
        if k in _ALLOWED_KEYS:
            base[k] = v
    p.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def merge_config_update(existing: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    for k, v in updates.items():
        if k in _ALLOWED_KEYS:
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
