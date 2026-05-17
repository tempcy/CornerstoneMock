from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


def load_web_config_defaults(config_path: Path) -> Dict[str, Any]:
    allowed = {
        "web_host",
        "web_port",
        "bridge_api_host",
        "bridge_api_port",
        "bridge_api_url",
    }
    text = config_path.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点须为 JSON 对象 {...}")
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in allowed:
            print(f"[cornerstone-web] 配置文件忽略未知键: {k!r}", file=sys.stderr)
            continue
        if k in ("web_port", "bridge_api_port"):
            out[k] = int(v)
            continue
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else str(v)
    return out


def bridge_base_url_from_args(
    *,
    bridge_api_url: str,
    bridge_api_host: str,
    bridge_api_port: int,
    web_host: str,
    web_port: int,
) -> str:
    url = (bridge_api_url or "").strip().rstrip("/")
    if url:
        return url
    host = (bridge_api_host or web_host or "127.0.0.1").strip()
    port = bridge_api_port if bridge_api_port else int(web_port) + 1
    return f"http://{host}:{port}"
