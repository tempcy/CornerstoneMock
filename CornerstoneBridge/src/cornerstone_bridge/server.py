from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .config import load_bridge_config_defaults
from .gateway import _handle_client
from .http_api import handle_bridge_http
from .hub import GatewayHub
from .protocol import _normalize_encoding


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


async def run_bridge(
    *,
    listen_host: str,
    listen_port: int,
    api_host: str,
    api_port: int,
    web_host: str,
    web_port: int,
    upstream_host: str,
    upstream_port: int,
    encoding: str,
    add_samples_queue_size: int,
    synthetic_logon_after_first: bool,
    instrument_short_connection: bool,
    upstream_heartbeat_interval: float,
    upstream_auto_reconnect: bool,
    async_message_interval: float,
    web_user: str,
    web_password: str,
    privileged_add_samples_host: str,
    config_file_path: Optional[Path] = None,
) -> None:
    hub = GatewayHub(
        upstream_host=upstream_host,
        upstream_port=upstream_port,
        encoding=encoding,
        add_samples_queue_size=add_samples_queue_size,
        synthetic_logon_after_first=synthetic_logon_after_first,
        instrument_short_connection=instrument_short_connection,
        upstream_heartbeat_interval_s=upstream_heartbeat_interval,
        upstream_auto_reconnect=upstream_auto_reconnect,
        web_user=web_user,
        web_password=web_password,
        privileged_add_samples_host=privileged_add_samples_host,
        tcp_listen_host=listen_host,
        tcp_listen_port=listen_port,
        web_listen_host=web_host,
        web_listen_port=web_port,
        config_file_path=config_file_path,
    )

    async def client_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _handle_client(r, w, hub=hub, async_message_interval=async_message_interval)

    async def http_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await handle_bridge_http(r, w, hub=hub)

    srv_client = await asyncio.start_server(client_cb, listen_host, listen_port)
    srv_api = await asyncio.start_server(http_cb, api_host, api_port)

    c_addrs = ", ".join(str(s.getsockname()) for s in srv_client.sockets or [])
    a_addrs = ", ".join(str(s.getsockname()) for s in srv_api.sockets or [])
    print(f"[bridge] TCP clients: {c_addrs} (encoding={encoding})")
    print(f"[bridge] REST API: http://{api_host}:{api_port}/  ({a_addrs})")
    print(
        f"[bridge] Upstream Cornerstone: {upstream_host}:{upstream_port} ; "
        f"synthetic 2nd+ Logon={'on' if synthetic_logon_after_first else 'off'} ; "
        f"instrument API={'short TCP' if instrument_short_connection else 'long (reuse upstream)'} ; "
        f"upstream heartbeat={upstream_heartbeat_interval}s ; "
        f"upstream auto-reconnect={'on' if upstream_auto_reconnect else 'off'}"
    )
    if hub.web_user:
        print(
            f"[bridge] Web→upstream Logon user: {hub.web_user!r} "
            f"(password {'set' if hub.web_password else 'empty'})"
        )
    else:
        print(
            "[bridge] Web→upstream Logon: --web-user not set "
            "(web send will fail until configured or a TCP client logs upstream in)"
        )
    if hub._privileged_add_samples_host:
        print(
            f"[bridge] AddSamples 直通上位机 IP: {hub._privileged_add_samples_host!r} "
            f"(其余 TCP 客户端仍截留)"
        )

    async def _preconnect_upstream_long_instrument() -> None:
        if hub._instrument_short_connection:
            return
        try:
            await hub._ensure_upstream()
            print("[bridge] upstream TCP connected at startup (instrument long mode)")
        except Exception as e:
            print(f"[bridge] startup upstream TCP connect failed: {e}")
            return
        if hub.web_user and hub.web_password:
            ok, err = await hub._ensure_upstream_instrument_logon_for_web()
            if ok:
                print("[bridge] upstream web Logon completed at startup")
            else:
                print(f"[bridge] startup upstream web Logon failed: {err}")

    async with srv_client, srv_api:
        await _preconnect_upstream_long_instrument()
        try:
            await asyncio.gather(srv_client.serve_forever(), srv_api.serve_forever())
        except asyncio.CancelledError:
            pass
        finally:
            print("[bridge] shutting down: Logoff upstream and closing connections...")
            srv_client.close()
            srv_api.close()
            await hub.shutdown_gracefully()
            with contextlib.suppress(Exception):
                await srv_client.wait_closed()
                await srv_api.wait_closed()
            await _async_drain_remaining_tasks()


def _resolve_api_endpoint(args: argparse.Namespace) -> tuple[str, int]:
    api_host = (getattr(args, "bridge_api_host", None) or args.web_host or "127.0.0.1").strip()
    api_port = getattr(args, "bridge_api_port", None)
    if api_port is None:
        api_port = int(args.web_port) + 1
    return api_host, int(api_port)


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-c", "--config", type=str, default=None, metavar="PATH", help=argparse.SUPPRESS)
    pre_args, argv_rest = pre.parse_known_args()

    parser = argparse.ArgumentParser(
        prog="cornerstone-bridge",
        description="Cornerstone TCP 网关与对内 REST API（AddSamples 队列、instrument_rq 等）。",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="JSON 配置文件路径（命令行参数优先覆盖文件）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="TCP 客户端监听地址")
    parser.add_argument("--port", type=int, default=12345, help="TCP 客户端监听端口")
    parser.add_argument(
        "--bridge-api-host",
        default=None,
        help="对内 REST 监听地址（默认与 --web-host 相同）",
    )
    parser.add_argument(
        "--bridge-api-port",
        type=int,
        default=None,
        help="对内 REST 监听端口（默认 web_port+1）",
    )
    parser.add_argument("--web-host", default="127.0.0.1", help="配置/UI 中展示的 Web 地址")
    parser.add_argument("--web-port", type=int, default=8080, help="配置/UI 中展示的 Web 端口")
    parser.add_argument("--upstream-host", default="127.0.0.1", help="真实 Cornerstone 地址")
    parser.add_argument("--upstream-port", type=int, default=54321, help="真实 Cornerstone 端口")
    parser.add_argument("--encoding", type=_normalize_encoding, default="utf-16-le")
    parser.add_argument("--add-samples-queue-size", type=int, default=8)
    parser.add_argument("--no-synthetic-logon", action="store_true")
    parser.add_argument("--async-message-interval", type=float, default=0.0)
    parser.add_argument("--web-user", default="")
    parser.add_argument("--web-password", default="")
    parser.add_argument("--privileged-add-samples-host", default="", metavar="HOST")
    parser.add_argument("--instrument-short-connection", action="store_true")
    parser.add_argument("--upstream-heartbeat-interval", type=float, default=60.0, metavar="SEC")
    parser.add_argument("--no-upstream-auto-reconnect", action="store_true")

    if pre_args.config:
        cfg_path = Path(pre_args.config).expanduser()
        if not cfg_path.is_file():
            print(f"[cornerstone-bridge] 配置文件不存在: {cfg_path}", file=sys.stderr)
            return 2
        try:
            parser.set_defaults(**load_bridge_config_defaults(cfg_path))
        except (OSError, ValueError, json.JSONDecodeError, argparse.ArgumentTypeError) as e:
            print(f"[cornerstone-bridge] 读取配置失败: {e}", file=sys.stderr)
            return 2

    args = parser.parse_args(argv_rest)
    cfg_resolved: Optional[Path] = None
    if args.config:
        cfg_resolved = Path(args.config).expanduser().resolve()

    api_host, api_port = _resolve_api_endpoint(args)

    try:
        asyncio.run(
            run_bridge(
                listen_host=args.host,
                listen_port=args.port,
                api_host=api_host,
                api_port=api_port,
                web_host=args.web_host,
                web_port=args.web_port,
                upstream_host=args.upstream_host,
                upstream_port=args.upstream_port,
                encoding=args.encoding,
                add_samples_queue_size=args.add_samples_queue_size,
                synthetic_logon_after_first=not args.no_synthetic_logon,
                instrument_short_connection=args.instrument_short_connection,
                upstream_heartbeat_interval=args.upstream_heartbeat_interval,
                upstream_auto_reconnect=not args.no_upstream_auto_reconnect,
                async_message_interval=args.async_message_interval,
                web_user=args.web_user,
                web_password=args.web_password,
                privileged_add_samples_host=args.privileged_add_samples_host,
                config_file_path=cfg_resolved,
            )
        )
    except KeyboardInterrupt:
        print("\n[bridge] interrupted")
        return 130
    return 0
