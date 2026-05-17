from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from .protocol import _normalize_encoding


def load_bridge_config_defaults(config_path: Path) -> Dict[str, Any]:
    """读取 JSON 配置，键名与 ``cornerstone-bridge`` CLI dest 一致。"""
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
        if k == "no_synthetic_logon":
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
