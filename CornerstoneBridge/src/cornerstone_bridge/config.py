from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .protocol import _normalize_encoding

_BRIDGE_PKG_DIR = Path(__file__).resolve().parents[2]

_BRIDGE_CONFIG_FILENAMES = (
    "cornerstone-bridge.config.json",
    "cornerstone-bridge.config.example.json",
)

_WEB_KEYS_IN_DEV = (
    "web_host",
    "web_port",
    "bridge_api_host",
    "bridge_api_port",
    "bridge_api_url",
)


def resolve_explicit_config_path(path: str) -> Optional[Path]:
    """
    解析 ``-c`` 传入的路径：先按 cwd，再按 Bridge 包目录及其上级（仓库根）。

    便于在 ``CornerstoneWeb`` 下写 ``-c CornerstoneBridge/cornerstone-bridge.config.json``。
    """
    raw = Path(path).expanduser()
    if raw.is_file():
        return raw.resolve()
    search_roots = (Path.cwd(), _BRIDGE_PKG_DIR, _BRIDGE_PKG_DIR.parent)
    for root in search_roots:
        cand = root / raw
        if cand.is_file():
            return cand.resolve()
        by_name = root / raw.name
        if by_name.is_file():
            return by_name.resolve()
    return None


def resolve_bridge_config_path() -> Optional[Path]:
    """查找 Bridge JSON：``CORNERSTONE_BRIDGE_CONFIG`` → 当前目录 → 包目录示例。"""
    env = (os.environ.get("CORNERSTONE_BRIDGE_CONFIG") or "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    cwd = Path.cwd()
    for name in _BRIDGE_CONFIG_FILENAMES:
        cand = cwd / name
        if cand.is_file():
            return cand
    for name in _BRIDGE_CONFIG_FILENAMES:
        cand = _BRIDGE_PKG_DIR / name
        if cand.is_file():
            return cand
    return None


def merge_web_config_into_bridge(
    bridge: Dict[str, Any], web: Dict[str, Any]
) -> Dict[str, Any]:
    """``cornerstone-web-dev``：将 Web 专配中的监听/代理地址合并进 Bridge 参数字典。"""
    merged = dict(bridge)
    for k in _WEB_KEYS_IN_DEV:
        if k in web:
            merged[k] = web[k]
    return merged


def load_bridge_config_defaults(config_path: Path) -> Dict[str, Any]:
    """读取 Bridge JSON（网关、上游、REST、仪器账号等；不含浏览器 ``web_*`` 专配）。"""
    allowed = {
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
        "upstream_auto_reconnect",
        "async_message_interval",
        "web_user",
        "web_password",
        "privileged_add_samples_host",
        "persist_add_samples_queue",
        "add_samples_queue_persist_file",
    }
    text = config_path.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点须为 JSON 对象 {...}")
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in allowed:
            print(f"[cornerstone-bridge] 配置文件忽略未知键: {k!r}", file=sys.stderr)
            continue
        if k == "encoding" and v is not None and str(v).strip() != "":
            out[k] = _normalize_encoding(str(v))
            continue
        if k in ("port", "web_port", "upstream_port", "add_samples_queue_size", "bridge_api_port"):
            out[k] = int(v)
            continue
        if k == "async_message_interval":
            out[k] = float(v)
            continue
        if k in ("no_synthetic_logon", "persist_add_samples_queue"):
            out[k] = bool(v)
            continue
        if k == "instrument_long_connection":
            out["instrument_short_connection"] = not bool(v)
            continue
        if k == "upstream_heartbeat_interval":
            out[k] = float(v)
            continue
        if k == "upstream_auto_reconnect":
            out["no_upstream_auto_reconnect"] = not bool(v)
            continue
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else str(v)
    return out
