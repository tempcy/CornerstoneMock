"""
一键启动 cornerstone-bridge + cornerstone-web（本地开发）。

用法::

    python -m cornerstone_web.dev_web
    cornerstone-web-dev

配置：``CornerstoneBridge/cornerstone-bridge.config.json``（网关/上游/REST）
+ ``CornerstoneWeb/cornerstone-web.config.json``（浏览器与 API 代理）。
若仅有旧版合并的 ``cornerstone-web.config.json``，将自动兼作 Bridge 配置并提示拆分。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Optional, Tuple

from cornerstone_bridge.config import (
    load_bridge_config_defaults,
    merge_web_config_into_bridge,
    resolve_dev_bridge_config_path,
)
from cornerstone_bridge.server import run_bridge

from .config import (
    bridge_base_url_from_args,
    load_web_config_defaults,
    resolve_dev_web_config_path,
)
from .server import run_web


def _resolve_dev_config_paths() -> Tuple[Path, Path, bool]:
    """
    返回 (bridge_config_path, web_config_path, legacy_combined)。

    legacy_combined：仅找到旧版「单文件含 Bridge+Web」时为 True。
    """
    bridge_path = resolve_dev_bridge_config_path()
    web_path = resolve_dev_web_config_path()
    legacy = False

    if bridge_path is None and web_path is not None:
        bridge_path = web_path
        legacy = True
    if bridge_path is None:
        raise FileNotFoundError(
            "未找到 Bridge 配置。请设置 CORNERSTONE_BRIDGE_CONFIG，"
            "或在当前目录 / CornerstoneBridge 下放置 cornerstone-bridge.config.json。"
        )
    if web_path is None:
        web_path = bridge_path
    return bridge_path, web_path, legacy


def _load_dev_merged_config(
    bridge_path: Path, web_path: Path
) -> tuple[dict, Path]:
    bridge_m = load_bridge_config_defaults(bridge_path)
    web_m = (
        load_web_config_defaults(web_path)
        if web_path.is_file()
        else {}
    )
    merged = merge_web_config_into_bridge(bridge_m, web_m)
    merged["_config_path"] = bridge_path
    return merged, bridge_path


def main() -> int:
    try:
        bridge_path, web_path, legacy = _resolve_dev_config_paths()
    except FileNotFoundError as e:
        print(f"[cornerstone-web-dev] {e}", file=sys.stderr)
        return 2

    if legacy:
        print(
            "[cornerstone-web-dev] 未找到 cornerstone-bridge.config.json，"
            f"正从 {web_path.name} 读取 Bridge 字段（建议拆分为 Bridge + Web 两份配置）。",
            file=sys.stderr,
        )

    try:
        m, cfg_path = _load_dev_merged_config(bridge_path, web_path)
    except (OSError, ValueError) as e:
        print(f"[cornerstone-web-dev] 读取配置失败: {e}", file=sys.stderr)
        return 2

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
        bridge_task = asyncio.create_task(
            run_bridge(
                listen_host=str(m.get("host") or "127.0.0.1"),
                listen_port=int(m.get("port") or 54321),
                api_host=api_host,
                api_port=api_port,
                web_host=web_host,
                web_port=web_port,
                upstream_host=str(m.get("upstream_host") or "127.0.0.1"),
                upstream_port=int(m.get("upstream_port") or 12345),
                encoding=str(m.get("encoding") or "utf-16-le"),
                add_samples_queue_size=int(m.get("add_samples_queue_size") or 8),
                synthetic_logon_after_first=not bool(m.get("no_synthetic_logon")),
                instrument_short_connection=bool(m.get("instrument_short_connection")),
                upstream_heartbeat_interval=float(m.get("upstream_heartbeat_interval") or 60.0),
                upstream_auto_reconnect=not bool(m.get("no_upstream_auto_reconnect")),
                upstream_inner_reassembly_timeout=float(
                    m.get("upstream_inner_reassembly_timeout") or 5.0
                ),
                async_message_interval=float(m.get("async_message_interval") or 0.0),
                web_user=str(m.get("web_user") or ""),
                web_password=str(m.get("web_password") or ""),
                privileged_add_samples_host=str(m.get("privileged_add_samples_host") or ""),
                config_file_path=cfg_path,
            ),
            name="dev_bridge",
        )
        web_task = asyncio.create_task(
            run_web(web_host=web_host, web_port=web_port, bridge_base_url=bridge_url),
            name="dev_web",
        )
        try:
            await asyncio.gather(bridge_task, web_task)
        except asyncio.CancelledError:
            pass
        finally:
            for t in (bridge_task, web_task):
                if not t.done():
                    t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(bridge_task, web_task, return_exceptions=True)

    print(f"[cornerstone-web-dev] Bridge 配置: {bridge_path.resolve()}")
    if web_path.resolve() != bridge_path.resolve():
        print(f"[cornerstone-web-dev] Web 配置: {web_path.resolve()}")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[cornerstone-web-dev] interrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
