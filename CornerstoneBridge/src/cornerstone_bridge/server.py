from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .bridge_logging import setup_bridge_logging
from .config import (
    load_bridge_config_defaults,
    resolve_bridge_config_path,
    resolve_explicit_config_path,
)
from .gateway import _handle_client
from .http_api import handle_bridge_http
from .asyncio_util import async_yield_shutdown
from .hub import GatewayHub
from .protocol import _normalize_encoding

_bridge_log = None


def _bridge_logger():
    from .bridge_logging import get_logger

    global _bridge_log
    if _bridge_log is None:
        _bridge_log = get_logger("server")
    return _bridge_log


def _apply_logging_from_args(
    args: argparse.Namespace,
    *,
    config_dir: Optional[Path] = None,
) -> None:
    max_mb = float(getattr(args, "log_file_max_mb", 2.0) or 2.0)
    setup_bridge_logging(
        log_level=str(getattr(args, "log_level", "info") or "info"),
        log_verbose_gateway=bool(getattr(args, "log_verbose_gateway", False)),
        log_file=str(getattr(args, "log_file", "") or ""),
        log_file_level=str(getattr(args, "log_file_level", "info") or "info"),
        log_file_max_bytes=int(max_mb * 1024 * 1024),
        log_file_backup_count=int(getattr(args, "log_file_backup_count", 3) or 3),
        log_throttle_interval_s=float(getattr(args, "log_throttle_interval_s", 300.0) or 300.0),
        config_dir=config_dir,
    )


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
    persist_add_samples_queue: bool = True,
    add_samples_queue_persist_file: str = "",
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
        persist_add_samples_queue=persist_add_samples_queue,
        add_samples_queue_persist_file=add_samples_queue_persist_file,
    )

    async def client_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _handle_client(r, w, hub=hub, async_message_interval=async_message_interval)

    async def http_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            await handle_bridge_http(r, w, hub=hub)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        except OSError as e:
            if getattr(e, "winerror", None) not in (64, 10054, 995):
                _bridge_logger().error("http handler: %s", e)
        except Exception as e:
            _bridge_logger().error("http handler: %s", e)

    srv_client = await asyncio.start_server(client_cb, listen_host, listen_port)
    srv_api = await asyncio.start_server(http_cb, api_host, api_port)

    c_addrs = ", ".join(str(s.getsockname()) for s in srv_client.sockets or [])
    a_addrs = ", ".join(str(s.getsockname()) for s in srv_api.sockets or [])
    log = _bridge_logger()
    log.info("TCP 网关监听 (客户端连这里): %s (encoding=%s)", c_addrs, encoding)
    log.info("REST API: http://%s:%s/ (%s)", api_host, api_port, a_addrs)
    log.info(
        "上游仪器 (Bridge 主动连): %s:%s ; synthetic 2nd+ Logon=%s ; "
        "instrument API=%s ; upstream heartbeat=%ss ; upstream auto-reconnect=%s",
        upstream_host,
        upstream_port,
        "on" if synthetic_logon_after_first else "off",
        "short TCP" if instrument_short_connection else "long (reuse upstream)",
        upstream_heartbeat_interval,
        "on" if upstream_auto_reconnect else "off",
    )
    if hub.web_user:
        log.info(
            "Web→upstream Logon user: %r (password %s)",
            hub.web_user,
            "set" if hub.web_password else "empty",
        )
    else:
        log.warning(
            "Web→upstream Logon: --web-user not set "
            "(web send will fail until configured or a TCP client logs upstream in)"
        )
    if hub._privileged_add_samples_host:
        log.info(
            "AddSamples 直通上位机 IP: %r (其余 TCP 客户端仍截留)",
            hub._privileged_add_samples_host,
        )
    if hub._queue_persist_path is not None:
        log.info("AddSamples 队列持久化: %s", hub._queue_persist_path)

    async def _preconnect_upstream_long_instrument() -> None:
        if hub._instrument_short_connection:
            return
        try:
            await hub._ensure_upstream()
            log.info("upstream TCP connected at startup (instrument long mode)")
        except Exception as e:
            log.warning("startup upstream TCP connect failed: %s", e)
            return
        if hub.web_user and hub.web_password:
            ok, err = await hub._ensure_upstream_instrument_logon_for_web()
            if ok:
                log.info("upstream web Logon completed at startup")
            else:
                log.warning("startup upstream web Logon failed: %s", err)

    async with srv_client, srv_api:
        await _preconnect_upstream_long_instrument()
        try:
            await asyncio.gather(srv_client.serve_forever(), srv_api.serve_forever())
        except asyncio.CancelledError:
            pass
        finally:
            _bridge_logger().info("shutting down: Logoff upstream and closing connections...")
            srv_client.close()
            srv_api.close()
            await hub.shutdown_gracefully()
            with contextlib.suppress(Exception):
                await srv_client.wait_closed()
                await srv_api.wait_closed()
            await async_yield_shutdown()


def _bridge_defaults_for_parser(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """JSON 键名 → argparse ``set_defaults``（含 ``persist_add_samples_queue``）。"""
    out = dict(cfg)
    if out.pop("persist_add_samples_queue", True) is False:
        out["no_persist_add_samples_queue"] = True
    return out


def _resolve_api_endpoint(args: argparse.Namespace) -> tuple[str, int]:
    api_host = (getattr(args, "bridge_api_host", None) or args.web_host or "127.0.0.1").strip()
    api_port = getattr(args, "bridge_api_port", None)
    if api_port is None:
        api_port = int(args.web_port) + 1
    return api_host, int(api_port)


def main() -> int:
    from cornerstone_cli.console_io import configure_stdio_utf8
    from cornerstone_cli.single_instance import ensure_single_instance

    configure_stdio_utf8()
    ensure_single_instance("cornerstone-bridge", log_prefix="bridge")

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
    parser.add_argument("--host", default="0.0.0.0", help="TCP 网关监听地址（远程客户端/CLI 连此端口）")
    parser.add_argument("--port", type=int, default=54321, help="TCP 网关监听端口")
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
    parser.add_argument("--upstream-host", default="127.0.0.1", help="真实 Cornerstone 仪器地址")
    parser.add_argument("--upstream-port", type=int, default=12345, help="真实 Cornerstone 仪器端口")
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
    parser.add_argument(
        "--no-persist-add-samples-queue",
        action="store_true",
        help="不将截留的 AddSamples 队列写入磁盘（默认开启持久化）",
    )
    parser.add_argument(
        "--add-samples-queue-persist-file",
        default="",
        metavar="PATH",
        help=(
            "队列缓存 JSON 路径（默认 %%APPDATA%%\\CornerstoneMock\\"
            "cornerstone-bridge.add-samples-queue.json）"
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error"],
        help="控制台日志级别（默认 info；配置文件可覆盖）",
    )
    parser.add_argument(
        "--log-verbose-gateway",
        action="store_true",
        help="控制台输出 RQ 类 INFO（Status/Prerequisites 等；默认不写文件）",
    )

    cfg_resolved: Optional[Path] = None
    if pre_args.config:
        cfg_path = resolve_explicit_config_path(pre_args.config)
        if cfg_path is None:
            tried = Path(pre_args.config).expanduser()
            hint = Path(__file__).resolve().parents[2] / "cornerstone-bridge.config.json"
            print(
                f"[cornerstone-bridge] 配置文件不存在: {tried}\n"
                f"  当前目录: {Path.cwd()}\n"
                f"  可尝试: {hint}\n"
                f"  或在 CornerstoneWeb 下: ..\\CornerstoneBridge\\cornerstone-bridge.config.json\n"
                f"  也可省略 -c（将自动查找 Bridge 包内配置）",
                file=sys.stderr,
            )
            return 2
        cfg_resolved = cfg_path
        try:
            parser.set_defaults(**_bridge_defaults_for_parser(load_bridge_config_defaults(cfg_path)))
        except (OSError, ValueError, json.JSONDecodeError, argparse.ArgumentTypeError) as e:
            print(f"[cornerstone-bridge] 读取配置失败: {e}", file=sys.stderr)
            return 2
    elif (auto_cfg := resolve_bridge_config_path()) is not None:
        cfg_resolved = auto_cfg.resolve()
        try:
            parser.set_defaults(**_bridge_defaults_for_parser(load_bridge_config_defaults(auto_cfg)))
        except (OSError, ValueError, json.JSONDecodeError, argparse.ArgumentTypeError) as e:
            print(f"[cornerstone-bridge] 读取配置失败: {e}", file=sys.stderr)
            return 2

    args = parser.parse_args(argv_rest)
    if getattr(args, "log_level", None) is None:
        args.log_level = "info"
    if args.config:
        cfg_resolved = Path(args.config).expanduser().resolve()
    config_dir = cfg_resolved.parent if cfg_resolved is not None else None
    _apply_logging_from_args(args, config_dir=config_dir)
    log = _bridge_logger()
    if cfg_resolved is not None:
        log.info("配置文件: %s", cfg_resolved)
    else:
        log.warning("未加载配置文件（使用命令行默认 port=54321 / upstream_port=12345）")

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
                persist_add_samples_queue=(
                    False
                    if getattr(args, "no_persist_add_samples_queue", False)
                    else bool(getattr(args, "persist_add_samples_queue", True))
                ),
                add_samples_queue_persist_file=str(
                    getattr(args, "add_samples_queue_persist_file", "") or ""
                ),
            )
        )
    except KeyboardInterrupt:
        _bridge_logger().info("interrupted")
        return 130
    return 0
