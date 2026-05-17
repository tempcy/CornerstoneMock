"""
一键启动 cornerstone-bridge + cornerstone-web（本地开发）。

用法::

    python -m cornerstone_web.dev_web
    cornerstone-web-dev
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from cornerstone_bridge.config import load_bridge_config_defaults
from cornerstone_bridge.server import run_bridge

from .config import bridge_base_url_from_args
from .server import run_web


def _resolve_default_config() -> Optional[Path]:
    env = (os.environ.get("CORNERSTONE_WEB_CONFIG") or os.environ.get("CORNERSTONE_MOCK_CONFIG") or "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    cwd = Path.cwd()
    for name in (
        "cornerstone-web.config.json",
        "cornerstone-web.config.example.json",
        "cornerstone-mock.config.json",
        "cornerstone-mock.config.example.json",
    ):
        cand = cwd / name
        if cand.is_file():
            return cand
    here = Path(__file__).resolve()
    for name in ("cornerstone-web.config.example.json", "cornerstone-mock.config.example.json"):
        repo_example = here.parents[2] / name
        if repo_example.is_file():
            return repo_example
    return None


def _load_shared_config(cfg: Path) -> dict:
    """Bridge 配置已含 web_host / bridge_api_* 等；勿再调 load_web_config_defaults 以免误报忽略键。"""
    merged = load_bridge_config_defaults(cfg)
    merged["_config_path"] = cfg
    return merged


def main() -> int:
    cfg = _resolve_default_config()
    if cfg is None:
        print(
            "[cornerstone-web-dev] 未找到配置文件。请设置 CORNERSTONE_WEB_CONFIG，"
            "或在当前目录放置 cornerstone-web.config.json。",
            file=sys.stderr,
        )
        return 2

    m = _load_shared_config(cfg)
    cfg_path = m.pop("_config_path")

    web_host = str(m.get("web_host") or "127.0.0.1")
    web_port = int(m.get("web_port") or 8080)
    bridge_url = bridge_base_url_from_args(
        bridge_api_url=str(m.get("bridge_api_url") or ""),
        bridge_api_host=str(m.get("bridge_api_host") or web_host),
        bridge_api_port=int(m.get("bridge_api_port") or web_port + 1),
        web_host=web_host,
        web_port=web_port,
    )

    from urllib.parse import urlparse

    pu = urlparse(bridge_url)
    api_host = pu.hostname or "127.0.0.1"
    api_port = pu.port or (443 if pu.scheme == "https" else 80)

    async def _run() -> None:
        await asyncio.gather(
            run_bridge(
                listen_host=str(m.get("host") or "127.0.0.1"),
                listen_port=int(m.get("port") or 12345),
                api_host=api_host,
                api_port=api_port,
                web_host=web_host,
                web_port=web_port,
                upstream_host=str(m.get("upstream_host") or "127.0.0.1"),
                upstream_port=int(m.get("upstream_port") or 54321),
                encoding=str(m.get("encoding") or "utf-16-le"),
                add_samples_queue_size=int(m.get("add_samples_queue_size") or 8),
                synthetic_logon_after_first=not bool(m.get("no_synthetic_logon")),
                instrument_short_connection=bool(m.get("instrument_short_connection")),
                upstream_heartbeat_interval=float(m.get("upstream_heartbeat_interval") or 60.0),
                upstream_auto_reconnect=not bool(m.get("no_upstream_auto_reconnect")),
                async_message_interval=float(m.get("async_message_interval") or 0.0),
                web_user=str(m.get("web_user") or ""),
                web_password=str(m.get("web_password") or ""),
                privileged_add_samples_host=str(m.get("privileged_add_samples_host") or ""),
                config_file_path=cfg_path,
            ),
            run_web(web_host=web_host, web_port=web_port, bridge_base_url=bridge_url),
        )

    print(f"[cornerstone-web-dev] 使用配置: {cfg_path}")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[cornerstone-web-dev] interrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
