from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Optional

from .config import (
    bridge_base_url_from_args,
    load_web_config_defaults,
    resolve_explicit_config_path,
    resolve_web_config_path,
)
from .http_server import handle_web_http


async def _async_drain_remaining_tasks() -> None:
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if not pending:
        await asyncio.sleep(0)
        return
    for t in pending:
        t.cancel()
    with contextlib.suppress(Exception):
        await asyncio.gather(*pending, return_exceptions=True)
    await asyncio.sleep(0)


async def run_web(*, web_host: str, web_port: int, bridge_base_url: str) -> None:
    async def http_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await handle_web_http(r, w, bridge_base_url=bridge_base_url)

    srv = await asyncio.start_server(http_cb, web_host, web_port)
    addrs = ", ".join(str(s.getsockname()) for s in srv.sockets or [])
    print(f"[web] UI: http://{web_host}:{web_port}/  ({addrs})")
    print(f"[web] Bridge API proxy → {bridge_base_url}")

    async with srv:
        try:
            await srv.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            srv.close()
            with contextlib.suppress(Exception):
                await srv.wait_closed()
            await _async_drain_remaining_tasks()


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-c", "--config", type=str, default=None, metavar="PATH", help=argparse.SUPPRESS)
    pre_args, argv_rest = pre.parse_known_args()

    parser = argparse.ArgumentParser(
        prog="cornerstone-web",
        description="Cornerstone Web UI（静态页 + 将 /api/* 代理到 cornerstone-bridge）。",
    )
    parser.add_argument("-c", "--config", type=str, default=None, metavar="PATH")
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--bridge-api-url", default="", help="Bridge 根 URL，如 http://127.0.0.1:8081")
    parser.add_argument("--bridge-api-host", default=None)
    parser.add_argument("--bridge-api-port", type=int, default=None)

    if pre_args.config:
        cfg_path = resolve_explicit_config_path(pre_args.config)
        if cfg_path is None:
            tried = Path(pre_args.config).expanduser()
            hint = Path(__file__).resolve().parents[2] / "cornerstone-web.config.json"
            print(
                f"[cornerstone-web] 配置文件不存在: {tried}\n"
                f"  当前目录: {Path.cwd()}\n"
                f"  可尝试: {hint}\n"
                f"  或省略 -c（将自动查找 Web 包内配置）",
                file=sys.stderr,
            )
            return 2
        try:
            parser.set_defaults(**load_web_config_defaults(cfg_path))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"[cornerstone-web] 读取配置失败: {e}", file=sys.stderr)
            return 2
    elif (auto_cfg := resolve_web_config_path()) is not None:
        try:
            parser.set_defaults(**load_web_config_defaults(auto_cfg))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"[cornerstone-web] 读取配置失败: {e}", file=sys.stderr)
            return 2

    args = parser.parse_args(argv_rest)
    bridge_url = bridge_base_url_from_args(
        bridge_api_url=getattr(args, "bridge_api_url", "") or "",
        bridge_api_host=(args.bridge_api_host or args.web_host or "127.0.0.1"),
        bridge_api_port=args.bridge_api_port if args.bridge_api_port is not None else int(args.web_port) + 1,
        web_host=args.web_host,
        web_port=args.web_port,
    )

    try:
        asyncio.run(run_web(web_host=args.web_host, web_port=args.web_port, bridge_base_url=bridge_url))
    except KeyboardInterrupt:
        print("\n[web] interrupted")
        return 130
    return 0
