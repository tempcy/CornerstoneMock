from __future__ import annotations

import asyncio
import html
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .hub import GatewayHub
from .hub_types import PendingAddSamples
from .bridge_logging import get_log_verbose_gateway, set_console_log_level, set_log_verbose_gateway
from .protocol import _async_close_stream_writer, _safe_stream_drain

async def _http_send(
    writer: asyncio.StreamWriter,
    status: int,
    body: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    reason = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        500: "Internal Server Error",
    }.get(status, "OK")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    )
    writer.write(head.encode("latin-1", errors="replace") + body)
    await _safe_stream_drain(writer)


def _hub_settings_public_dict(hub: GatewayHub) -> Dict[str, Any]:
    """网页「网关配置」表单用字段（不含密码明文）。"""
    return {
        "tcpListenHost": hub._tcp_listen_host,
        "tcpListenPort": hub._tcp_listen_port,
        "webListenHost": hub._web_listen_host,
        "webListenPort": hub._web_listen_port,
        "upstreamHost": hub._upstream_host,
        "upstreamPort": hub._upstream_port,
        "webUser": hub.web_user,
        "webPasswordSet": bool(hub.web_password),
        "privilegedAddSamplesHost": hub._privileged_add_samples_host,
        "queueMax": hub._add_samples_max,
        "encoding": hub.encoding,
        "configFile": str(hub._config_file_path) if hub._config_file_path else "",
    }


def _persist_hub_settings_to_config(hub: GatewayHub) -> Tuple[bool, str]:
    """将当前 Hub 状态合并写入 ``-c`` 指定的 JSON（保留文件中其它键）。"""
    if hub._config_file_path is None:
        return False, "未使用 --config 启动，无法写回文件"
    try:
        p = Path(hub._config_file_path)
        data: Dict[str, Any] = {}
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        data["host"] = hub._tcp_listen_host
        data["port"] = int(hub._tcp_listen_port)
        data["web_host"] = hub._web_listen_host
        data["web_port"] = int(hub._web_listen_port)
        data["upstream_host"] = hub._upstream_host
        data["upstream_port"] = int(hub._upstream_port)
        data["web_user"] = hub.web_user
        data["web_password"] = hub.web_password
        data["encoding"] = hub.encoding
        data["add_samples_queue_size"] = hub._add_samples_max
        data["privileged_add_samples_host"] = hub._privileged_add_samples_host
        data["log_verbose_gateway"] = get_log_verbose_gateway()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


def _q_int(q: Dict[str, str], key: str, default: int) -> int:
    v = (q.get(key) or "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _q_bool(q: Dict[str, str], key: str, default: bool) -> bool:
    v = (q.get(key) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _queue_item_to_api_dict(p: PendingAddSamples) -> Dict[str, Any]:
    return {
        "id": p.entry_id,
        "receivedAt": p.received_at,
        "receivedAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.received_at)),
        "peer": p.source_peer,
        "sampleName": p.sample_name,
        "sampleDescription": p.sample_description,
        "xml": p.payload_xml,
    }

async def handle_bridge_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    hub: GatewayHub,
) -> None:
    try:
        first = await reader.read(65536)
        if not first:
            return
        header_end = first.find(b"\r\n\r\n")
        body = b""
        if header_end >= 0:
            header_blob = first[:header_end].decode("latin-1", errors="replace")
            body = first[header_end + 4 :]
            first_line = header_blob.split("\r\n", 1)[0]
            headers: Dict[str, str] = {}
            for hl in header_blob.split("\r\n")[1:]:
                if ":" in hl:
                    k, v = hl.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            cl = int(headers.get("content-length", "0") or "0")
            while len(body) < cl:
                chunk = await reader.read(cl - len(body))
                if not chunk:
                    break
                body += chunk
        else:
            first_line = first.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")

        parts = first_line.split()
        method = parts[0].upper() if parts else "GET"
        raw_target = parts[1] if len(parts) > 1 else "/"
        path_only, _, qstr = raw_target.partition("?")
        path = path_only.split("#", 1)[0]
        qparams: Dict[str, str] = dict(urllib.parse.parse_qsl(qstr, keep_blank_values=True)) if qstr else {}

        # 静态资源（SPA）
        if method == "GET" and path == "/api/queue":
            items = [_queue_item_to_api_dict(p) for p in hub.pending_snapshot()]
            payload = json.dumps({"ok": True, "items": items}, ensure_ascii=False).encode("utf-8")
            await _http_send(writer, 200, payload, "application/json; charset=utf-8")
            return

        if method == "GET" and path == "/api/config":
            tcp_listen = (
                f"{hub._tcp_listen_host}:{hub._tcp_listen_port}"
                if hub._tcp_listen_port
                else ""
            )
            web_ln = (
                f"{hub._web_listen_host}:{hub._web_listen_port}"
                if hub._web_listen_port
                else ""
            )
            cfg = {
                "ok": True,
                "hasWebCredentials": bool(hub.web_user and hub.web_password),
                "queueMax": hub._add_samples_max,
                "queueCurrent": len(hub.pending_snapshot()),
                "upstream": f"{hub._upstream_host}:{hub._upstream_port}",
                "tcpListen": tcp_listen,
                "webListen": web_ln,
                "webUser": hub.web_user or "",
                "encoding": hub.encoding,
                "instrumentLongConnection": not hub._instrument_short_connection,
                "upstreamHeartbeatInterval": hub._upstream_heartbeat_interval_s,
                "upstreamAutoReconnect": hub._upstream_auto_reconnect,
                "privilegedAddSamplesHost": hub._privileged_add_samples_host,
                "remoteControlState": hub._remote_control_display,
                "remoteControlStateError": hub._remote_control_last_err,
                "configFile": str(hub._config_file_path) if hub._config_file_path else "",
            }
            await _http_send(
                writer,
                200,
                json.dumps(cfg, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings":
            pl = {
                "ok": True,
                "queueCurrent": len(hub.pending_snapshot()),
                **_hub_settings_public_dict(hub),
            }
            await _http_send(
                writer,
                200,
                json.dumps(pl, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/transports":
            data = await hub.fetch_transports_list_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/transport":
            tk = (qparams.get("key") or "").strip()
            data = await hub.fetch_transport_detail_json(tk)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/methods":
            data = await hub.fetch_methods_list_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/method":
            mk = (qparams.get("key") or "").strip()
            data = await hub.fetch_method_detail_json(mk)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/standards":
            data = await hub.fetch_standards_list_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/standard":
            sk = (qparams.get("key") or "").strip()
            data = await hub.fetch_standard_detail_json(sk)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "PUT" and path == "/api/settings":
            try:
                obj = json.loads(body.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "无效 JSON"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            if not isinstance(obj, dict):
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "请求体须为 JSON 对象"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            restart_required = False
            notes: List[str] = []
            upstream_addr_changed = False
            if "tcpListenHost" in obj:
                v = str(obj.get("tcpListenHost") or "").strip()
                if v != hub._tcp_listen_host:
                    hub._tcp_listen_host = v
                    restart_required = True
            if "tcpListenPort" in obj:
                try:
                    p = int(obj["tcpListenPort"])
                except (TypeError, ValueError, KeyError):
                    p = hub._tcp_listen_port
                p = max(1, min(65535, p))
                if p != hub._tcp_listen_port:
                    hub._tcp_listen_port = p
                    restart_required = True
            if "webListenHost" in obj:
                v = str(obj.get("webListenHost") or "").strip()
                if v != hub._web_listen_host:
                    hub._web_listen_host = v
                    restart_required = True
            if "webListenPort" in obj:
                try:
                    p = int(obj["webListenPort"])
                except (TypeError, ValueError, KeyError):
                    p = hub._web_listen_port
                p = max(1, min(65535, p))
                if p != hub._web_listen_port:
                    hub._web_listen_port = p
                    restart_required = True
            if restart_required:
                notes.append("客户端监听或网页监听地址已更改：须重启 cornerstone-bridge 进程后方可生效。")
            if "upstreamHost" in obj:
                nh = str(obj.get("upstreamHost") or "").strip()
                if nh != hub._upstream_host:
                    hub._upstream_host = nh
                    upstream_addr_changed = True
            if "upstreamPort" in obj:
                try:
                    np = int(obj["upstreamPort"])
                except (TypeError, ValueError, KeyError):
                    np = hub._upstream_port
                np = max(1, min(65535, np))
                if np != hub._upstream_port:
                    hub._upstream_port = np
                    upstream_addr_changed = True
            if "webUser" in obj:
                hub.web_user = str(obj.get("webUser") or "").strip()
            if "webPassword" in obj and obj["webPassword"] is not None:
                hub.web_password = str(obj["webPassword"])
            if "privilegedAddSamplesHost" in obj:
                hub._privileged_add_samples_host = str(obj.get("privilegedAddSamplesHost") or "").strip()
            if "queueMax" in obj:
                try:
                    hub.set_add_samples_queue_max(int(obj["queueMax"]))
                except (TypeError, ValueError, KeyError):
                    pass
            if "logVerboseGateway" in obj:
                set_log_verbose_gateway(bool(obj.get("logVerboseGateway")))
                notes.append("详细网关日志（含 RQ 类 XML）已即时生效。")
            if "logLevel" in obj:
                set_console_log_level(str(obj.get("logLevel") or "info"))
                notes.append("控制台 log_level 已即时生效。")
            reco_ok = True
            reco_err = ""
            if upstream_addr_changed:
                reco_ok, reco_err = await hub.reconnect_upstream_with_current_target()
                if reco_ok:
                    notes.append("上游 TCP 已按新地址重连。")
                else:
                    notes.append(f"上游重连失败: {reco_err}")
            persist_ok = False
            persist_err = ""
            want_persist = bool(obj.get("persistToConfigFile", True))
            if want_persist and hub._config_file_path is not None:
                persist_ok, persist_err = _persist_hub_settings_to_config(hub)
            elif want_persist:
                persist_err = "未使用 --config 启动，跳过写回文件"
            out = {
                "ok": reco_ok,
                "restartRequired": restart_required,
                "upstreamReconnectOk": reco_ok,
                "upstreamReconnectError": reco_err,
                "persistOk": persist_ok,
                "persistError": persist_err,
                "notes": notes,
                "settings": {**_hub_settings_public_dict(hub), "queueCurrent": len(hub.pending_snapshot())},
            }
            await _http_send(
                writer,
                200,
                json.dumps(out, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/status":
            st = {
                "ok": True,
                "upstreamConnected": hub.upstream_connected(),
                "lastHeartbeatReplyAt": hub._last_upstream_heartbeat_reply_at,
                "queueCount": len(hub.pending_snapshot()),
                "queueMax": hub._add_samples_max,
                "remoteControlState": hub._remote_control_display,
                "privilegedAddSamplesHost": hub._privileged_add_samples_host,
                "remoteControlStateError": hub._remote_control_last_err,
            }
            await _http_send(
                writer,
                200,
                json.dumps(st, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/monitor":
            tcp_clients = await hub.tcp_clients_snapshot()
            tcp_listen = (
                f"{hub._tcp_listen_host}:{hub._tcp_listen_port}"
                if hub._tcp_listen_port
                else ""
            )
            api_listen = (
                f"{hub._api_listen_host}:{hub._api_listen_port}"
                if hub._api_listen_port
                else ""
            )
            web_listen = (
                f"{hub._web_listen_host}:{hub._web_listen_port}"
                if hub._web_listen_port
                else ""
            )
            snap = hub.pending_snapshot()
            mon = {
                "ok": True,
                "tcpListen": tcp_listen,
                "apiListen": api_listen,
                "webListen": web_listen,
                "upstream": {
                    "host": hub._upstream_host,
                    "port": hub._upstream_port,
                    "enabled": hub.is_upstream_connection_enabled(),
                    "connected": hub.upstream_connected(),
                    "lastHeartbeatReplyAt": hub._last_upstream_heartbeat_reply_at,
                    "autoReconnect": hub._upstream_auto_reconnect,
                    "heartbeatIntervalS": hub._upstream_heartbeat_interval_s,
                },
                "tcpGateway": {
                    "listen": tcp_listen,
                    "enabled": hub.is_tcp_gateway_enabled(),
                },
                "tcpClients": tcp_clients,
                "tcpClientCount": len(tcp_clients),
                "queue": {
                    "current": len(snap),
                    "max": hub._add_samples_max,
                },
                "remoteControl": {
                    "state": hub._remote_control_display,
                    "error": hub._remote_control_last_err,
                },
                "instrumentLongConnection": not hub._instrument_short_connection,
                "encoding": hub.encoding,
                "logVerboseGateway": get_log_verbose_gateway(),
                "privilegedAddSamplesHost": hub._privileged_add_samples_host,
                "configFile": str(hub._config_file_path) if hub._config_file_path else "",
            }
            await _http_send(
                writer,
                200,
                json.dumps(mon, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "PUT" and path == "/api/connections":
            try:
                obj = json.loads(body.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "无效 JSON"}, ensure_ascii=False).encode(
                        "utf-8"
                    ),
                    "application/json; charset=utf-8",
                )
                return
            if not isinstance(obj, dict):
                await _http_send(
                    writer,
                    400,
                    json.dumps(
                        {"ok": False, "error": "请求体须为 JSON 对象"}, ensure_ascii=False
                    ).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            notes: List[str] = []
            if "upstreamEnabled" in obj:
                en = bool(obj.get("upstreamEnabled"))
                await hub.set_upstream_connection_enabled(en)
                notes.append("上游仪器连接已" + ("启用" if en else "断开"))
            if "tcpGatewayEnabled" in obj:
                en = bool(obj.get("tcpGatewayEnabled"))
                await hub.set_tcp_gateway_enabled(en)
                notes.append("TCP 网关已" + ("启用" if en else "关闭（已断开现有客户端）"))
            out = {
                "ok": True,
                "notes": notes,
                "upstreamEnabled": hub.is_upstream_connection_enabled(),
                "tcpGatewayEnabled": hub.is_tcp_gateway_enabled(),
            }
            await _http_send(
                writer,
                200,
                json.dumps(out, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/instrument-info":
            data = await hub.fetch_instrument_info_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/counters":
            data = await hub.fetch_maintenance_counters_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/automation-status":
            data = await hub.fetch_automation_status_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/system-parameters":
            data = await hub.fetch_system_parameters_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/counter":
            ck = (qparams.get("key") or "").strip()
            data = await hub.fetch_counter_detail_json(ck)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/environment/ambients":
            data = await hub.fetch_ambients_json_api()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/diagnostic/digital-io":
            data = await hub.fetch_digital_io_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/diagnostic/status-check":
            data = await hub.fetch_status_check_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/sets":
            n = _q_int(qparams, "number", 10)
            sa = _q_int(qparams, "start_at", -1)
            fk = (qparams.get("filter_key") or "").strip()
            # 留空时与常见 CLI ``--filter-key 0`` 一致；多数仪器对 FilterKey="" 与 ``0`` 语义不同，前者常无数据。
            if fk == "":
                fk = "0"
            data = await hub.fetch_sets_json(fk, n, sa)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/remote-import-sets":
            data = await hub.fetch_remote_import_sets_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/set-reps":
            sk = (qparams.get("set_key") or "").strip()
            inc = _q_bool(qparams, "include_detail", True)
            tg = _q_int(qparams, "tag", -1)
            data = await hub.fetch_set_reps_json(sk, include_detail=inc, tag=tg)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/rep-plot":
            sk = (qparams.get("set_key") or "").strip()
            tg = (qparams.get("tag") or "").strip()
            data = await hub.fetch_rep_plot_json(sk, tg)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/rep-detail":
            sk = (qparams.get("set_key") or "").strip()
            tg = (qparams.get("tag") or "").strip()
            data = await hub.fetch_rep_detail_json(sk, tg)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/status-widgets":
            data = await hub.fetch_status_widgets_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/set-stats":
            sk = (qparams.get("set_key") or "").strip()
            if not sk:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "缺少 set_key"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            data = await hub.fetch_set_collection_stats_json(sk)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "POST" and path == "/api/queue/send":
            try:
                obj = json.loads(body.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "无效 JSON"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            ids = set(obj.get("ids") or [])
            selected = hub.get_pending_by_ids(ids)
            results: List[Dict[str, Any]] = []
            for p in selected:
                r = await hub.forward_add_samples_web(p.payload_xml)
                results.append({"id": p.entry_id, "upstreamResponse": r})
            out = {"ok": True, "results": results, "queueKept": True}
            if not selected:
                out = {"ok": False, "error": "未选择任何条目或 ID 无效", "results": []}
            await _http_send(
                writer,
                200,
                json.dumps(out, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        await _http_send(writer, 404, b"Not Found", "text/plain; charset=utf-8")
    finally:
        await _async_close_stream_writer(writer)
