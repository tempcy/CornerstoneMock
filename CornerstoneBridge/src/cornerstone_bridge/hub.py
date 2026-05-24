from __future__ import annotations

import argparse
import asyncio
import contextlib
import html
import json
import math
import re
import secrets
import struct
import threading
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

from cornerstone_cli.communications.tcp_engine import HEARTBEAT_XML

from .hub_types import PendingAddSamples, TcpClientSession, _FutureWaiter
from .protocol import *
from .parsers import *
from .queue_persistence import (
    load_add_samples_queue,
    resolve_queue_persist_path,
    save_add_samples_queue,
)

from .hub_helpers import *
from .bridge_logging import get_logger, log_gateway_xml, log_throttled_warning

_log = get_logger("gateway")

# 与官方 RemoteControlClient 一致：无 Cookie 的 CornerstoneMessage 走异步通道，不占用请求应答表
_UPSTREAM_ASYNC_FANOUT_TAGS = frozenset({"cornerstonemessage"})

_UPSTREAM_READ_DISCONNECT_EXC = (
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    OSError,
)


class GatewayHub:
    """
    多客户端 -> 单上游 Cornerstone：按应答中的 Cookie 将电文路由回对应客户端。
    - 首条 Logon 走上游；上游 ErrorCode=0 后，后续客户端 Logon 可合成成功（单机单会话）。
    - TCP ``<Logon>`` 若缺省或空的 ``<User>``/``<Password>``，在已配置 ``--web-user``/``--web-password``
      时用网关网页侧凭据补全后再转发上游；其它指令在已配置网页凭据时会先确保上游已网页 Logon，
      客户端可不自带账号即可经网关使用仪器命令。
    - AddSamples：默认截留进网页队列；若配置 ``privileged_add_samples_host``，来自该主机 IP 的
      AddSamples 直接转发上游；其它 IP 仍截留。
    - ``<RemoteControlState/>``：仅在上游 TCP **新建连接**后问询一次，用于网页展示，不参与直通判定。
    """

    def __init__(
        self,
        *,
        upstream_host: str,
        upstream_port: int,
        encoding: str,
        add_samples_queue_size: int,
        synthetic_logon_after_first: bool,
        instrument_short_connection: bool,
        upstream_heartbeat_interval_s: float,
        upstream_auto_reconnect: bool,
        upstream_inner_reassembly_timeout_s: float = 5.0,
        web_user: str,
        web_password: str,
        privileged_add_samples_host: str = "",
        request_culture: str = "en-US",
        tcp_listen_host: str = "",
        tcp_listen_port: int = 0,
        api_listen_host: str = "",
        api_listen_port: int = 0,
        web_listen_host: str = "",
        web_listen_port: int = 0,
        config_file_path: Optional[Union[Path, str]] = None,
        persist_add_samples_queue: bool = True,
        add_samples_queue_persist_file: str = "",
    ) -> None:
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self.encoding = encoding
        self._add_samples_max = max(1, int(add_samples_queue_size))
        self._synthetic_logon_after_first = synthetic_logon_after_first
        self._instrument_short_connection = bool(instrument_short_connection)
        self._upstream_heartbeat_interval_s = float(upstream_heartbeat_interval_s)
        self._upstream_auto_reconnect = bool(upstream_auto_reconnect)
        self._upstream_inner_reassembly_timeout_s = max(
            0.0, float(upstream_inner_reassembly_timeout_s)
        )
        self._upstream_reassembly_buf: bytes = b""
        self._upstream_reassembly_acc_outer: int = 0
        self._upstream_reassembly_deadline: float = 0.0
        self._upstream_reassembly_target_bytes: int = 0
        self.web_user = (web_user or "").strip()
        self.web_password = web_password or ""
        self._privileged_add_samples_host = (privileged_add_samples_host or "").strip()
        self.request_culture = (request_culture or "en-US").strip() or "en-US"

        self._upstream_reader: Optional[asyncio.StreamReader] = None
        self._upstream_writer: Optional[asyncio.StreamWriter] = None
        self._upstream_connect_lock = asyncio.Lock()
        self._write_upstream_lock = asyncio.Lock()

        self._cookie_to_target: Dict[str, Union[asyncio.StreamWriter, _FutureWaiter]] = {}
        self._cookie_lock = asyncio.Lock()

        self._logon_seen_upstream_success = False
        self._upstream_session_authenticated = False

        self._config_file_path: Optional[Path] = (
            Path(config_file_path).expanduser().resolve() if config_file_path else None
        )

        self._queue_persist_lock = threading.Lock()
        self._queue_persist_path = resolve_queue_persist_path(
            config_file_path=self._config_file_path,
            explicit_path=add_samples_queue_persist_file,
            persist_enabled=bool(persist_add_samples_queue),
        )
        restored = load_add_samples_queue(self._queue_persist_path)
        if len(restored) > self._add_samples_max:
            restored = restored[-self._add_samples_max :]
        self._pending_add_samples: deque[PendingAddSamples] = deque(
            restored, maxlen=self._add_samples_max
        )
        if restored and self._queue_persist_path is not None:
            _log.info(
                "已从磁盘恢复 %d 条 AddSamples 队列: %s",
                len(restored),
                self._queue_persist_path,
            )
            self._persist_add_samples_queue()

        self._upstream_reader_task: Optional[asyncio.Task[None]] = None
        self._upstream_heartbeat_task: Optional[asyncio.Task[None]] = None
        self._upstream_reconnect_task: Optional[asyncio.Task[None]] = None
        self._upstream_heartbeat_fail_streak = 0
        self._stale_recover_lock = asyncio.Lock()
        self._instrument_sidecar_lock = asyncio.Lock()
        self._tcp_clients_lock = asyncio.Lock()
        self._tcp_client_writers: Set[asyncio.StreamWriter] = set()
        self._tcp_sessions: Dict[int, TcpClientSession] = {}
        self._upstream_connection_enabled = True
        self._tcp_gateway_enabled = True

        self._tcp_listen_host = (tcp_listen_host or "").strip()
        self._tcp_listen_port = int(tcp_listen_port)
        self._api_listen_host = (api_listen_host or "").strip()
        self._api_listen_port = int(api_listen_port)
        self._web_listen_host = (web_listen_host or "").strip()
        self._web_listen_port = int(web_listen_port)
        self._last_upstream_heartbeat_reply_at = 0.0
        self._shutting_down = False

        self._rcs_lock = asyncio.Lock()
        self._remote_control_display: str = "—"
        self._remote_control_active: bool = False
        self._remote_control_last_err: str = ""

    def pending_snapshot(self) -> List[PendingAddSamples]:
        return sorted(self._pending_add_samples, key=lambda p: p.received_at, reverse=True)

    def is_tcp_gateway_enabled(self) -> bool:
        return bool(self._tcp_gateway_enabled)

    def is_upstream_connection_enabled(self) -> bool:
        return bool(self._upstream_connection_enabled)

    def _tcp_session(self, writer: asyncio.StreamWriter) -> Optional[TcpClientSession]:
        return self._tcp_sessions.get(id(writer))

    def on_client_rx(self, writer: asyncio.StreamWriter) -> None:
        s = self._tcp_session(writer)
        if s is not None:
            s.rx_frames += 1

    def on_client_tx(self, writer: asyncio.StreamWriter) -> None:
        s = self._tcp_session(writer)
        if s is not None:
            s.tx_frames += 1

    def on_client_logon_request(self, writer: asyncio.StreamWriter, xml_text: str) -> None:
        s = self._tcp_session(writer)
        if s is None:
            return
        s.logon_user = _logon_user_from_client_xml(xml_text)
        s.logon_authenticated = False

    def on_client_logon_response(self, writer: asyncio.StreamWriter, xml_text: str) -> None:
        s = self._tcp_session(writer)
        if s is None:
            return
        if _upstream_logon_response_ok(xml_text):
            s.logon_authenticated = True
        else:
            s.logon_authenticated = False

    async def register_tcp_client(self, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_s = str(peer)
        host = _peer_host_from_peername(peer)
        async with self._tcp_clients_lock:
            self._tcp_client_writers.add(writer)
            self._tcp_sessions[id(writer)] = TcpClientSession(
                writer=writer,
                peer=peer_s,
                peer_host=host,
                connected_at=time.time(),
                privileged=_peer_host_matches_privileged(
                    host, self._privileged_add_samples_host
                ),
            )

    async def unregister_tcp_client(self, writer: asyncio.StreamWriter) -> None:
        async with self._tcp_clients_lock:
            self._tcp_client_writers.discard(writer)
            self._tcp_sessions.pop(id(writer), None)

    async def close_all_tcp_clients(self) -> int:
        async with self._tcp_clients_lock:
            writers = list(self._tcp_client_writers)
        n = 0
        for w in writers:
            if not w.is_closing():
                await _async_close_stream_writer(w)
                n += 1
        return n

    async def set_tcp_gateway_enabled(self, enabled: bool) -> None:
        self._tcp_gateway_enabled = bool(enabled)
        if not self._tcp_gateway_enabled:
            closed = await self.close_all_tcp_clients()
            _log.info("TCP gateway disabled; closed %d client(s)", closed)
        else:
            _log.info("TCP gateway enabled (accepting new clients)")

    async def set_upstream_connection_enabled(self, enabled: bool) -> None:
        self._upstream_connection_enabled = bool(enabled)
        if not self._upstream_connection_enabled:
            await self._drop_upstream_transport()
            _log.info("upstream connection disabled by operator")
            return
        _log.info("upstream connection enabled by operator")
        if not self._instrument_short_connection:
            with contextlib.suppress(Exception):
                await self._ensure_upstream()

    async def tcp_clients_snapshot(self) -> List[Dict[str, Any]]:
        """当前已连接的 TCP 远程客户端（供管理界面 /api/monitor）。"""
        now = time.time()
        async with self._tcp_clients_lock:
            sessions = list(self._tcp_sessions.values())
        out: List[Dict[str, Any]] = []
        for s in sessions:
            if s.writer.is_closing():
                continue
            dur = max(0.0, now - s.connected_at)
            if s.logon_authenticated:
                user_disp = s.logon_user or "(已登录)"
            else:
                user_disp = "未登录"
            out.append(
                {
                    "peer": s.peer,
                    "connectedAt": s.connected_at,
                    "connectedSeconds": round(dur, 1),
                    "privileged": s.privileged,
                    "logonUser": user_disp,
                    "rxFrames": s.rx_frames,
                    "txFrames": s.tx_frames,
                }
            )
        return out

    async def _broadcast_upstream_async_to_tcp_clients(self, text: str) -> int:
        """将仪器主动推送的 XML 广播给当前已连接的 TCP 客户端（对齐 C# MessageDataEvent）。"""
        frame = _frame(text, self.encoding)
        async with self._tcp_clients_lock:
            targets = list(self._tcp_client_writers)
        delivered = 0
        for w in targets:
            if w.is_closing():
                continue
            try:
                w.write(frame)
                if await _safe_stream_drain(w):
                    self.on_client_tx(w)
                    delivered += 1
            except _UPSTREAM_READ_DISCONNECT_EXC:
                continue
            except Exception as e:
                _log.warning("async fan-out to TCP client failed: %s", e)
        return delivered

    async def _handle_upstream_unrouted_frame(self, text: str, *, cookie: str, tag: str) -> None:
        """
        上游入站帧无法按 Cookie 路由时的策略（保持读循环与业务在线）：
        - Heartbeat：忽略
        - CornerstoneMessage 等异步通知：广播给 TCP 客户端
        - 其它 orphan：仅记录，不断开上游
        """
        local = inbound_xml_local_tag(text) or _xml_local_tag(tag)
        if local.lower() == "heartbeat":
            return
        if local.lower() in _UPSTREAM_ASYNC_FANOUT_TAGS:
            n = await self._broadcast_upstream_async_to_tcp_clients(text)
            _log.info(
                "upstream async tag=%r cookie=%r bytes=%d -> %d TCP client(s)",
                local,
                cookie,
                len(text),
                n,
            )
            return
        preview = (text or "").strip()
        if len(preview) > 800:
            preview = preview[:800] + "…"
        _log.warning(
            "orphan upstream response cookie=%r tag=%r bytes=%d payload=%r",
            cookie,
            tag or local or "?",
            len(text),
            preview,
        )

    def _persist_add_samples_queue(self) -> None:
        if self._queue_persist_path is None:
            return
        with self._queue_persist_lock:
            save_add_samples_queue(
                self._queue_persist_path, list(self._pending_add_samples)
            )

    def enqueue_add_samples(self, item: PendingAddSamples) -> None:
        """截留的 AddSamples 入队并写入磁盘（Bridge 重启后可恢复）。"""
        self._pending_add_samples.append(item)
        self._persist_add_samples_queue()

    def get_pending_by_ids(self, ids: Set[str]) -> List[PendingAddSamples]:
        """按 ID 返回队列中的条目（不从队列移除）。"""
        return [p for p in self._pending_add_samples if p.entry_id in ids]

    def remove_pending_by_ids(self, ids: Set[str]) -> List[PendingAddSamples]:
        kept: List[PendingAddSamples] = []
        selected: List[PendingAddSamples] = []
        for p in self._pending_add_samples:
            if p.entry_id in ids:
                selected.append(p)
            else:
                kept.append(p)
        self._pending_add_samples.clear()
        self._pending_add_samples.extend(kept)
        self._persist_add_samples_queue()
        return selected

    def set_add_samples_queue_max(self, n: int) -> None:
        n = max(1, min(int(n), 50_000))
        items: List[PendingAddSamples] = list(self._pending_add_samples)
        while len(items) > n:
            items.pop(0)
        self._add_samples_max = n
        self._pending_add_samples = deque(items, maxlen=n)
        self._persist_add_samples_queue()

    async def _send_upstream_logoff(self) -> None:
        """向上游 Cornerstone 发送 ``<Logoff/>``（程序退出或主动释放会话）。"""
        if not self._upstream_session_authenticated:
            return
        uw = self._upstream_writer
        if uw is None or uw.is_closing():
            self._upstream_session_authenticated = False
            return
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        logoff_cookie = secrets.token_hex(16)
        payload = self._inject_cookie_culture("<Logoff/>", logoff_cookie)
        await self._register(logoff_cookie, _FutureWaiter(fut))
        try:
            async with self._write_upstream_lock:
                uw = self._upstream_writer
                if uw is None or uw.is_closing():
                    return
                _log.info("upstream Logoff cookie=%r", logoff_cookie)
                uw.write(_frame(payload, self.encoding))
                await uw.drain()
            await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            _log.warning("upstream Logoff wait timeout (proceeding to disconnect)")
        except (asyncio.CancelledError, OSError, RuntimeError):
            pass
        except Exception as e:
            _log.warning("upstream Logoff error: %s", e)
        finally:
            async with self._cookie_lock:
                self._cookie_to_target.pop(logoff_cookie, None)
            self._upstream_session_authenticated = False
            self._logon_seen_upstream_success = False

    async def shutdown_gracefully(self) -> None:
        """主动退出：停止上游心跳/重连，Logoff 仪器会话，断开上游 TCP。"""
        self._persist_add_samples_queue()
        self._shutting_down = True
        self._upstream_auto_reconnect = False
        await self._stop_upstream_heartbeat()
        rct = self._upstream_reconnect_task
        if rct is not None and not rct.done():
            rct.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rct
        self._upstream_reconnect_task = None
        await self._send_upstream_logoff()
        await self._force_close_upstream()

    async def _drop_upstream_transport(self) -> None:
        """关闭上游 TCP 与读循环；不清除 Cookie 路由表（客户端仍可在重连后重试）。"""
        await self._stop_upstream_heartbeat()
        t = self._upstream_reader_task
        self._upstream_reader_task = None
        if t is not None and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        async with self._upstream_connect_lock:
            uw = self._upstream_writer
            self._upstream_reader = None
            self._upstream_writer = None
        await _async_close_stream_writer(uw)
        self._reset_upstream_reassembly_hold()
        self._logon_seen_upstream_success = False
        self._upstream_session_authenticated = False

    async def _force_close_upstream(self) -> None:
        rct = self._upstream_reconnect_task
        if rct is not None and not rct.done():
            rct.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rct
        self._upstream_reconnect_task = None
        await self._drop_upstream_transport()
        async with self._cookie_lock:
            self._cookie_to_target.clear()

    async def reconnect_upstream_with_current_target(self) -> Tuple[bool, str]:
        """断开并重连到当前 ``_upstream_host``/``_upstream_port``（修改上游后调用）。"""
        try:
            await self._force_close_upstream()
            await self._ensure_upstream()
            self._schedule_remote_control_state_probe_after_connect()
            return True, ""
        except Exception as e:
            return False, str(e)

    async def _stop_upstream_heartbeat(self) -> None:
        t = self._upstream_heartbeat_task
        self._upstream_heartbeat_task = None
        if t is None:
            return
        if not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    def _start_upstream_heartbeat(self) -> None:
        if self._upstream_heartbeat_interval_s <= 0:
            return
        if self._upstream_heartbeat_task is not None and not self._upstream_heartbeat_task.done():
            return
        self._upstream_heartbeat_task = asyncio.create_task(
            self._upstream_heartbeat_loop(), name="gateway_upstream_heartbeat"
        )

    async def _upstream_heartbeat_loop(self) -> None:
        interval = max(float(self._upstream_heartbeat_interval_s), 0.5)
        while True:
            await asyncio.sleep(interval)
            if not self._upstream_transport_usable():
                return
            await self._send_upstream_heartbeat_once()

    def _schedule_upstream_reconnect(self) -> None:
        if self._shutting_down or not self._upstream_auto_reconnect:
            return
        if not self._upstream_connection_enabled:
            return
        if self._upstream_reconnect_task is not None and not self._upstream_reconnect_task.done():
            return
        self._upstream_reconnect_task = asyncio.create_task(
            self._upstream_reconnect_worker(), name="gateway_upstream_reconnect"
        )

    def _should_recycle_upstream_after_heartbeat_failure(self) -> bool:
        """连续心跳无应答或距上次成功过久时，回收僵死 TCP 并触发 auto-reconnect。"""
        interval = max(float(self._upstream_heartbeat_interval_s), 0.5)
        last_ok = self._last_upstream_heartbeat_reply_at
        stale_s = max(3.0 * interval, 90.0)
        if last_ok > 0 and (time.time() - last_ok) > stale_s:
            return True
        return self._upstream_heartbeat_fail_streak >= 2

    def _log_upstream_bad_frame(
        self,
        reason: str,
        *,
        declared_length: int,
        payload_bytes: bytes,
        text_preview: str = "",
        segmentation: str = "",
        segment_index: Optional[int] = None,
        segment_bytes: Optional[bytes] = None,
    ) -> None:
        seg_note = ""
        if segment_index is not None:
            seg_note = f" segment_index={segment_index} segment_bytes={len(segment_bytes or b'')}"
        if payload_bytes:
            hex_dump = format_frame_hex(payload_bytes, limit=None)
            text_out = (text_preview or "").strip() or "(empty)"
        else:
            hex_dump = "(empty payload)"
            text_out = (text_preview or "").strip() or "(empty)"
        _log.error(
            "upstream invalid frame (%s): tcp_outer_len=%d payload_len=%d%s\n"
            "%s\n"
            "hex=%s\n"
            "text=%s",
            reason,
            declared_length,
            len(payload_bytes),
            seg_note,
            segmentation or "(no segmentation diagnostics)",
            hex_dump,
            text_out,
        )

    def _log_reassembly_abandoned(self, reason: str) -> None:
        buf = self._upstream_reassembly_buf
        seg_diag = ""
        if buf:
            seg_meta = segment_cornerstone_payload(buf, self.encoding)
            seg_diag = format_payload_segmentation_diagnostics(
                buf, seg_meta, encoding=self.encoding
            )
        inner = utf16le_inner_length_at(buf, 0) if buf else None
        _log.error(
            "upstream reassembly %s: target=%d have=%d tcp_outer_sum=%d inner_hdr=%s\n"
            "%s\n"
            "hex=%s",
            reason,
            self._upstream_reassembly_target_bytes,
            len(buf),
            self._upstream_reassembly_acc_outer,
            inner if inner is not None else "—",
            seg_diag or "(no buffer)",
            format_frame_hex(buf, limit=None) if buf else "(empty)",
        )

    async def _recover_stale_upstream(self) -> None:
        """Heartbeat 判定上游无应答：关闭读循环与 TCP，由 auto-reconnect 重建会话。"""
        if self._shutting_down:
            return
        async with self._stale_recover_lock:
            if not self._upstream_transport_usable():
                return
            _log.warning(
                "upstream Heartbeat 无应答，回收僵死 TCP（连续失败 %d 次）",
                self._upstream_heartbeat_fail_streak,
            )
            self._upstream_heartbeat_fail_streak = 0
            await self._drop_upstream_transport()
            self._schedule_upstream_reconnect()

    async def _send_upstream_heartbeat_once(self) -> None:
        if self._upstream_writer is None or self._upstream_writer.is_closing():
            return
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        hb_cookie = secrets.token_hex(8)
        text = self._inject_cookie_culture(HEARTBEAT_XML, hb_cookie)
        await self._register(hb_cookie, _FutureWaiter(fut))
        try:
            async with self._write_upstream_lock:
                uw = self._upstream_writer
                if uw is None or uw.is_closing():
                    async with self._cookie_lock:
                        self._cookie_to_target.pop(hb_cookie, None)
                    return
                uw.write(_frame(text, self.encoding))
                await uw.drain()
            await asyncio.wait_for(fut, timeout=15.0)
            self._last_upstream_heartbeat_reply_at = time.time()
            self._upstream_heartbeat_fail_streak = 0
        except asyncio.TimeoutError:
            async with self._cookie_lock:
                self._cookie_to_target.pop(hb_cookie, None)
            self._upstream_heartbeat_fail_streak += 1
            log_throttled_warning(_log, "upstream_heartbeat_timeout", "upstream Heartbeat wait timeout")
            if self._should_recycle_upstream_after_heartbeat_failure():
                asyncio.create_task(
                    self._recover_stale_upstream(), name="gateway_upstream_stale_recover"
                )
        except (asyncio.CancelledError, OSError, RuntimeError):
            async with self._cookie_lock:
                self._cookie_to_target.pop(hb_cookie, None)
        except Exception as e:
            async with self._cookie_lock:
                self._cookie_to_target.pop(hb_cookie, None)
            _log.warning("upstream Heartbeat error: %s", e)

    async def _upstream_reconnect_worker(self) -> None:
        if not self._upstream_auto_reconnect:
            return
        delay = 1.0
        while True:
            await asyncio.sleep(delay)
            if not self._upstream_connection_enabled:
                return
            try:
                async with self._upstream_connect_lock:
                    w = self._upstream_writer
                    if w is not None and not w.is_closing():
                        return
                await self._ensure_upstream()
                _log.info("upstream reconnected after drop")
                if self.web_user and self.web_password:
                    ok, err = await self._ensure_upstream_instrument_logon_for_web()
                    if not ok:
                        _log.warning("post-reconnect web Logon: %s", err)
                return
            except asyncio.CancelledError:
                return
            except Exception as ex:
                log_throttled_warning(
                    _log,
                    "upstream_reconnect_failed",
                    "upstream reconnect attempt failed: %s (next in %.0fs)",
                    ex,
                    min(delay * 2, 60.0),
                )
                delay = min(delay * 2, 60.0)

    def _upstream_transport_usable(self) -> bool:
        w = self._upstream_writer
        t = self._upstream_reader_task
        return (
            w is not None
            and not w.is_closing()
            and self._upstream_reader is not None
            and t is not None
            and not t.done()
        )

    async def _ensure_upstream(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if not self._upstream_connection_enabled:
            raise ConnectionError("upstream connection disabled by operator")
        created_new = False
        async with self._upstream_connect_lock:
            if self._upstream_transport_usable():
                assert self._upstream_reader is not None
                return self._upstream_reader, self._upstream_writer
        if self._upstream_writer is not None or self._upstream_reader_task is not None:
            _log.warning("upstream transport stale after drop; reconnecting")
            await self._drop_upstream_transport()
        async with self._upstream_connect_lock:
            _log.info(
                "connecting upstream %s:%s (encoding=%s)",
                self._upstream_host,
                self._upstream_port,
                self.encoding,
            )
            r, w = await asyncio.open_connection(self._upstream_host, self._upstream_port)
            self._upstream_reader = r
            self._upstream_writer = w
            self._upstream_reader_task = asyncio.create_task(
                self._upstream_read_loop(), name="gateway_upstream_read"
            )
            created_new = True
        if created_new:
            self._start_upstream_heartbeat()
            self._schedule_remote_control_state_probe_after_connect()
        assert self._upstream_reader is not None and self._upstream_writer is not None
        return self._upstream_reader, self._upstream_writer

    async def _dispatch_upstream_xml_text(self, text: str) -> None:
        cookie = _parse_cookie_from_payload(text)
        tag = _root_tag(text)
        if tag == "Logon":
            ec = ""
            with contextlib.suppress(ET.ParseError):
                root = ET.fromstring(_strip_xml_prefix(text))
                ec = (root.attrib.get("ErrorCode") or "").strip()
            if ec == "0":
                self._logon_seen_upstream_success = True
                self._upstream_session_authenticated = True

        log_gateway_xml(_log, "upstream IN", text, cookie=cookie)
        async with self._cookie_lock:
            target = self._cookie_to_target.pop(cookie, None) if cookie else None
        if target is None:
            await self._handle_upstream_unrouted_frame(text, cookie=cookie, tag=tag)
            return
        if isinstance(target, _FutureWaiter):
            if not target.fut.done():
                target.fut.set_result(text)
            return
        if target.is_closing():
            return
        try:
            self.on_client_tx(target)
            if tag == "Logon":
                self.on_client_logon_response(target, text)
            target.write(_frame(text, self.encoding))
            await target.drain()
        except Exception as e:
            _log.warning("failed to deliver to client: %s", e)

    async def _process_upstream_payload_bytes(self, payload_bytes: bytes, *, outer_length: int) -> bool:
        """
        解析一条外层 TCP 长度帧。返回 True 表示应断开并重连（整帧无法挽救的格式错误）。
        """
        enc = self.encoding
        seg_meta = segment_cornerstone_payload(payload_bytes, enc)
        segments = [s.data for s in seg_meta]
        seg_diag = format_payload_segmentation_diagnostics(
            payload_bytes, seg_meta, encoding=enc
        )
        if len(segments) > 1 or (
            len(payload_bytes) >= 6 and payload_bytes[4:6] == b"<\x00"
        ):
            _log.warning(
                "upstream tcp_outer_len=%d split into %d segment(s) (inner uint32 length)\n%s",
                outer_length,
                len(segments),
                seg_diag,
            )
        dispatched = False
        for seg_i, seg_bytes in enumerate(segments):
            text, decode_err = decode_inbound_segment_bytes(seg_bytes, enc)
            if decode_err:
                self._log_upstream_bad_frame(
                    decode_err,
                    declared_length=outer_length,
                    payload_bytes=payload_bytes,
                    segmentation=seg_diag,
                    segment_index=seg_i,
                    segment_bytes=seg_bytes,
                    text_preview=text or "",
                )
                return True
            for doc in split_concatenated_xml_documents(text or ""):
                xml_err = frame_xml_defect(doc)
                if xml_err:
                    if frame_xml_routable(doc):
                        log_throttled_warning(
                            _log,
                            "upstream_lenient_xml_route",
                            "upstream xml not strictly valid (%s); routing by cookie/tag outer_len=%d",
                            xml_err,
                            outer_length,
                        )
                        dispatched = True
                        await self._dispatch_upstream_xml_text(doc)
                        continue
                    self._log_upstream_bad_frame(
                        xml_err,
                        declared_length=outer_length,
                        payload_bytes=payload_bytes,
                        text_preview=doc,
                        segmentation=seg_diag,
                        segment_index=seg_i,
                        segment_bytes=seg_bytes,
                    )
                    if not dispatched:
                        return True
                    _log.warning(
                        "upstream trailing incomplete xml (%s) after prior good doc(s); "
                        "tcp_outer_len=%d segment_index=%d\n%s\n"
                        "trailing_text=%s",
                        xml_err,
                        outer_length,
                        seg_i,
                        seg_diag,
                        doc,
                    )
                    continue
                dispatched = True
                await self._dispatch_upstream_xml_text(doc)
        return False

    async def _read_one_upstream_tcp_payload(self) -> Tuple[bytes, int]:
        """读取一条外层 TCP 长度帧，返回 (正文, tcp_outer_len)。"""
        assert self._upstream_reader is not None
        enc = self.encoding
        header = await self._upstream_reader.readexactly(4)
        (length,) = struct.unpack("<I", header)
        length_err = validate_frame_length(length, enc)
        if length_err:
            raise ValueError(f"length_header:{length_err}:{length}")
        payload_bytes = await self._upstream_reader.readexactly(length)
        if len(payload_bytes) != length:
            raise ValueError(f"short_read:{length}:{len(payload_bytes)}")
        return payload_bytes, length

    def _reset_upstream_reassembly_hold(self) -> None:
        self._upstream_reassembly_buf = b""
        self._upstream_reassembly_acc_outer = 0
        self._upstream_reassembly_deadline = 0.0
        self._upstream_reassembly_target_bytes = 0

    def _reassembly_seconds_left(self) -> float:
        if self._upstream_reassembly_deadline <= 0:
            return 0.0
        return max(0.0, self._upstream_reassembly_deadline - time.monotonic())

    def _reassembly_deadline_expired(self) -> bool:
        if self._upstream_reassembly_deadline <= 0:
            return False
        return time.monotonic() > self._upstream_reassembly_deadline

    def _hold_upstream_reassembly(
        self, buf: bytes, acc_tcp_outer: int, *, target_bytes: int = 0
    ) -> None:
        self._upstream_reassembly_buf = buf
        self._upstream_reassembly_acc_outer = acc_tcp_outer
        self._upstream_reassembly_target_bytes = max(0, int(target_bytes))
        if self._upstream_reassembly_deadline <= 0:
            self._upstream_reassembly_deadline = (
                time.monotonic() + self._upstream_inner_reassembly_timeout_s
            )

    def _upstream_payload_xml_complete(self, payload_bytes: bytes) -> bool:
        text, err = decode_frame_payload_bytes(payload_bytes, self.encoding)
        if err is not None or not text:
            return False
        return frame_xml_defect(text) is None

    def _finish_reassembly_buffer_deferred(
        self, buf: bytes, acc_tcp_outer: int
    ) -> Optional[Tuple[bytes, int]]:
        """无 inner 头的 UTF-16 XML：未闭合则暂存，由下一条 TCP 读循环续拼（不阻塞读循环）。"""
        if not _utf16le_xml_magic_at(buf, 0):
            self._upstream_reassembly_deadline = 0.0
            return buf, acc_tcp_outer
        if self._upstream_payload_xml_complete(buf):
            self._upstream_reassembly_deadline = 0.0
            return buf, acc_tcp_outer
        if self._upstream_inner_reassembly_timeout_s <= 0:
            return buf, acc_tcp_outer
        if self._reassembly_deadline_expired():
            self._hold_upstream_reassembly(buf, acc_tcp_outer, target_bytes=0)
            self._log_reassembly_abandoned("xml_tail_deadline")
            self._reset_upstream_reassembly_hold()
            return buf, acc_tcp_outer
        self._hold_upstream_reassembly(buf, acc_tcp_outer, target_bytes=0)
        _log.info(
            "upstream xml tail reassembly waiting: have=%d bytes timeout_in=%.1fs "
            "(raw socket read, not TCP length header)",
            len(buf),
            self._reassembly_seconds_left(),
        )
        return None

    async def _iter_reassembled_upstream_payloads(
        self, chunk: bytes, tcp_outer_len: int
    ) -> List[Tuple[bytes, int]]:
        """
        inner 帧跨多条 TCP 外层包时，在读循环多次调用间累积缓冲（非阻塞等待）。

        完整判定：``inner_len == len(accumulated_payload) - 4``。
        超时内未凑齐则丢弃缓冲并打日志，不把半帧交给 XML 解析。
        """
        out: List[Tuple[bytes, int]] = []

        if self._upstream_reassembly_buf and self._reassembly_deadline_expired():
            self._log_reassembly_abandoned("deadline_before_chunk")
            self._reset_upstream_reassembly_hold()

        buf = self._upstream_reassembly_buf + chunk
        acc_tcp_outer = self._upstream_reassembly_acc_outer + tcp_outer_len
        self._upstream_reassembly_buf = b""
        self._upstream_reassembly_acc_outer = 0

        while buf:
            inner_len = utf16le_inner_length_at(buf, 0)
            if inner_len is None:
                finished = self._finish_reassembly_buffer_deferred(buf, acc_tcp_outer)
                if finished is not None:
                    out.append(finished)
                return out

            need = inner_framed_total_bytes(inner_len)
            if len(buf) < need:
                if self._upstream_inner_reassembly_timeout_s <= 0:
                    out.append((buf, acc_tcp_outer))
                    return out
                if self._reassembly_deadline_expired():
                    self._hold_upstream_reassembly(buf, acc_tcp_outer, target_bytes=need)
                    self._log_reassembly_abandoned("inner_deadline")
                    self._reset_upstream_reassembly_hold()
                    return out
                self._hold_upstream_reassembly(buf, acc_tcp_outer, target_bytes=need)
                _log.info(
                    "upstream inner reassembly waiting: inner_hdr=%d have=%d need=%d "
                    "timeout_in=%.1fs (raw socket continuation, not TCP length header)",
                    inner_len,
                    len(buf),
                    need,
                    self._reassembly_seconds_left(),
                )
                return out

            self._upstream_reassembly_deadline = 0.0
            if not inner_framed_tcp_payload_complete(buf[:need]):
                _log.warning(
                    "upstream inner slice length mismatch after reassembly inner_hdr=%d slice=%d",
                    inner_len,
                    len(buf[:need]),
                )
            out.append((buf[:need], acc_tcp_outer))
            buf = buf[need:]
            acc_tcp_outer = 0
            if not buf:
                return out
            continue

        return out

    async def _dispatch_reassembled_frames(
        self, frames: List[Tuple[bytes, int]]
    ) -> bool:
        """处理已拼好的帧；返回 True 表示应断开上游读循环。"""
        for frame_bytes, frame_outer in frames:
            if await self._process_upstream_payload_bytes(
                frame_bytes, outer_length=frame_outer
            ):
                return True
        return False

    async def _read_inner_continuation_bytes(self, remaining: int, timeout_s: float) -> Optional[bool]:
        """
        读取 inner 帧续包。返回 None=超时需继续等待；False=已追加；True=连接断开。
        """
        assert self._upstream_reader is not None
        if remaining <= 0:
            return False
        try:
            prefix = await asyncio.wait_for(
                self._upstream_reader.readexactly(4), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            return None
        except asyncio.IncompleteReadError:
            return True

        (next_len,) = struct.unpack("<I", prefix)
        length_err = validate_frame_length(next_len, self.encoding)
        if length_err is None and next_len > 0 and abs(next_len - remaining) <= 64:
            try:
                body = await asyncio.wait_for(
                    self._upstream_reader.readexactly(next_len),
                    timeout=self._reassembly_seconds_left(),
                )
            except asyncio.TimeoutError:
                self._upstream_reassembly_buf += prefix
                return None
            except asyncio.IncompleteReadError:
                return True
            self._upstream_reassembly_buf += body
            self._upstream_reassembly_acc_outer += 4 + next_len
            _log.info(
                "upstream inner continuation framed tcp_outer=%d have=%d",
                next_len,
                len(self._upstream_reassembly_buf),
            )
            return False

        self._upstream_reassembly_buf += prefix
        still = remaining - 4
        if still > 0:
            try:
                rest = await asyncio.wait_for(
                    self._upstream_reader.readexactly(still),
                    timeout=self._reassembly_seconds_left(),
                )
            except asyncio.TimeoutError:
                return None
            except asyncio.IncompleteReadError:
                return True
            self._upstream_reassembly_buf += rest
        _log.info(
            "upstream inner continuation raw have=%d (prefix was not a TCP length header)",
            len(self._upstream_reassembly_buf),
        )
        return False

    async def _advance_upstream_reassembly(self) -> bool:
        """
        缓冲中 inner/XML 未凑齐时：从 socket 续读（先探测 framed，否则按 raw 字节拼接）。
        返回 True 表示读循环应断开。
        """
        assert self._upstream_reader is not None
        if not self._upstream_reassembly_buf:
            return False
        if self._reassembly_deadline_expired():
            self._log_reassembly_abandoned("deadline")
            self._reset_upstream_reassembly_hold()
            return False

        target = self._upstream_reassembly_target_bytes
        timeout_s = self._reassembly_seconds_left()
        if timeout_s <= 0:
            self._log_reassembly_abandoned("deadline")
            self._reset_upstream_reassembly_hold()
            return False

        if target > 0:
            remaining = target - len(self._upstream_reassembly_buf)
            if remaining <= 0:
                buf = self._upstream_reassembly_buf
                acc = self._upstream_reassembly_acc_outer
                self._reset_upstream_reassembly_hold()
                frames = await self._iter_reassembled_upstream_payloads(buf, acc)
                return await self._dispatch_reassembled_frames(frames)

            read_ok = await self._read_inner_continuation_bytes(remaining, timeout_s)
            if read_ok is None:
                _log.info(
                    "upstream inner reassembly still waiting: have=%d need=%d timeout_in=%.1fs",
                    len(self._upstream_reassembly_buf),
                    target,
                    self._reassembly_seconds_left(),
                )
                return False
            if read_ok is True:
                return True

            if len(self._upstream_reassembly_buf) >= target:
                buf = self._upstream_reassembly_buf
                acc = self._upstream_reassembly_acc_outer
                self._reset_upstream_reassembly_hold()
                frames = await self._iter_reassembled_upstream_payloads(buf, acc)
                return await self._dispatch_reassembled_frames(frames)
            return False

        # XML 尾段：无固定 target，读一块 raw 并尝试完成
        try:
            chunk = await asyncio.wait_for(
                self._upstream_reader.read(4096),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            _log.info(
                "upstream xml tail still waiting: have=%d timeout_in=%.1fs",
                have,
                self._reassembly_seconds_left(),
            )
            return False
        except asyncio.IncompleteReadError:
            return True
        if not chunk:
            return True
        self._upstream_reassembly_buf += chunk
        if self._upstream_payload_xml_complete(self._upstream_reassembly_buf):
            buf = self._upstream_reassembly_buf
            acc = self._upstream_reassembly_acc_outer
            self._reset_upstream_reassembly_hold()
            frames = await self._iter_reassembled_upstream_payloads(buf, acc)
            return await self._dispatch_reassembled_frames(frames)
        _log.info(
            "upstream xml tail still waiting: have=%d timeout_in=%.1fs",
            len(self._upstream_reassembly_buf),
            self._reassembly_seconds_left(),
        )
        return False

    async def _upstream_read_loop(self) -> None:
        assert self._upstream_reader is not None
        try:
            while self._upstream_reader is not None:
                if self._upstream_reassembly_buf:
                    if await self._advance_upstream_reassembly():
                        break
                    continue

                try:
                    payload_bytes, length = await self._read_one_upstream_tcp_payload()
                except (asyncio.IncompleteReadError, asyncio.CancelledError):
                    break
                except ValueError as ex:
                    err = str(ex)
                    if err.startswith("length_header:"):
                        parts = err.split(":")
                        declared = int(parts[2]) if len(parts) > 2 else 0
                        reason = parts[1] if len(parts) > 1 else err
                        if self._upstream_reassembly_buf:
                            self._log_reassembly_abandoned(
                                f"length_header_while_holding:{reason}"
                            )
                            self._reset_upstream_reassembly_hold()
                        self._log_upstream_bad_frame(
                            f"length_header:{reason}",
                            declared_length=declared,
                            payload_bytes=b"",
                            text_preview=f"tcp_length_header_hex={format_frame_hex(struct.pack('<I', declared), limit=None) if declared else ''}",
                        )
                    break
                except _UPSTREAM_READ_DISCONNECT_EXC as ex:
                    _log.warning("upstream read disconnected: %s", ex)
                    break

                try:
                    complete_frames = await self._iter_reassembled_upstream_payloads(
                        payload_bytes, length
                    )
                except (asyncio.IncompleteReadError, asyncio.CancelledError):
                    break
                except _UPSTREAM_READ_DISCONNECT_EXC as ex:
                    _log.warning("upstream read disconnected: %s", ex)
                    break

                if await self._dispatch_reassembled_frames(complete_frames):
                    break
        except Exception as ex:
            if not isinstance(ex, asyncio.CancelledError):
                _log.error("upstream read loop error: %s", ex)
        finally:
            _log.info("upstream read loop ended")
            if not self._shutting_down:
                await self._drop_upstream_transport()
                self._schedule_upstream_reconnect()

    def _inject_cookie_culture(self, xml: str, cookie: str) -> str:
        s = (xml or "").lstrip()
        if not s.startswith("<"):
            return xml
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return xml
        if cookie:
            root.set("Cookie", cookie)
        root.set("Culture", self.request_culture)
        return ET.tostring(root, encoding="unicode")

    async def _ensure_upstream_instrument_logon_for_web(self) -> Tuple[bool, str]:
        """网页发往仪器前：若上游会话尚未登录，则用 --web-user/--web-password 发 Logon。"""
        if self._upstream_session_authenticated:
            return True, ""
        async with self._instrument_sidecar_lock:
            if self._upstream_session_authenticated:
                return True, ""
            if not self.web_user or not self.web_password:
                return (
                    False,
                    "网页发往仪器前需要先登录上游会话：请使用启动参数 --web-user 与 --web-password 配置仪器远程账号（与 cornerstone-cli tcp logon 一致）。",
                )
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[str] = loop.create_future()
            logon_cookie = secrets.token_hex(16)
            raw_logon = _web_logon_xml(self.web_user, self.web_password)
            payload = self._inject_cookie_culture(raw_logon, logon_cookie)
            await self._register(logon_cookie, _FutureWaiter(fut))
            try:
                await self._ensure_upstream()
                uw = self._upstream_writer
                assert uw is not None
                async with self._write_upstream_lock:
                    _log.info("web upstream Logon cookie=%r", logon_cookie)
                    uw.write(_frame(payload, self.encoding))
                    await uw.drain()
                resp = await asyncio.wait_for(fut, timeout=60.0)
            except asyncio.TimeoutError:
                async with self._cookie_lock:
                    self._cookie_to_target.pop(logon_cookie, None)
                return False, "上游 Logon 等待应答超时。"
            except OSError as e:
                async with self._cookie_lock:
                    self._cookie_to_target.pop(logon_cookie, None)
                return False, f"上游连接错误: {e}"
            except Exception as e:
                async with self._cookie_lock:
                    self._cookie_to_target.pop(logon_cookie, None)
                return False, str(e)
            if _upstream_logon_response_ok(resp):
                self._upstream_session_authenticated = True
                self._logon_seen_upstream_success = True
                return True, ""
            return False, f"上游 Logon 未成功: {(resp or '')[:800]}"

    async def _register(self, cookie: str, target: Union[asyncio.StreamWriter, _FutureWaiter]) -> None:
        if not cookie:
            return
        async with self._cookie_lock:
            self._cookie_to_target[cookie] = target

    async def forward_client_frame(self, text: str, client_writer: asyncio.StreamWriter) -> None:
        tag_name = _xml_local_tag(_root_tag(text))
        if not self._upstream_connection_enabled:
            sess = self._tcp_session(client_writer)
            _log.warning(
                "upstream paused; drop client frame tag=%s peer=%s",
                tag_name,
                sess.peer if sess else "?",
            )
            return
        if tag_name == "Logon":
            text = _logon_merge_web_credentials(text, self.web_user, self.web_password)
        elif self.web_user and self.web_password:
            ok, err = await self._ensure_upstream_instrument_logon_for_web()
            if not ok:
                _log.warning("TCP→upstream: 上游网页账号登录未就绪（%s），仍尝试转发", err)
        cookie = _parse_cookie_from_payload(text)
        if not cookie:
            cookie = secrets.token_hex(16)
            text = self._inject_cookie_culture(text, cookie)
        await self._register(cookie, client_writer)
        await self._ensure_upstream()
        uw = self._upstream_writer
        assert uw is not None
        async with self._write_upstream_lock:
            log_gateway_xml(_log, "upstream OUT", text, cookie=cookie)
            uw.write(_frame(text, self.encoding))
            await uw.drain()

    @staticmethod
    def _instrument_response_dict(resp: str) -> Dict[str, Any]:
        r = (resp or "").strip()
        if not r:
            return {"ok": False, "error": "无应答", "xml": "", "rootTag": ""}
        try:
            root = ET.fromstring(r)
        except ET.ParseError:
            return {"ok": False, "error": "应答非合法 XML", "xml": r[:4000], "rootTag": ""}
        ec = (root.attrib.get("ErrorCode") or "").strip()
        if ec != "0":
            return {
                "ok": False,
                "error": f"{root.tag} ErrorCode={ec} {root.attrib.get('ErrorMessage', '')}".strip(),
                "xml": r[:8000],
                "rootTag": root.tag,
            }
        return {"ok": True, "error": "", "xml": r, "rootTag": root.tag}

    async def _instrument_rq_upstream_long(self, command_xml: str, *, timeout_s: float) -> Dict[str, Any]:
        """经网关已建立的 Cornerstone 上游连接发 Remote Query（与网页 AddSamples 同路径）。"""
        if not self.web_user or not self.web_password:
            return {"ok": False, "error": "未配置 --web-user / --web-password", "xml": "", "rootTag": ""}
        ok, err = await self._ensure_upstream_instrument_logon_for_web()
        if not ok:
            return {"ok": False, "error": err, "xml": "", "rootTag": ""}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        web_cookie = secrets.token_hex(16)
        try:
            ET.fromstring((command_xml or "").strip())
        except ET.ParseError as e:
            return {"ok": False, "error": f"无效 XML: {e}", "xml": "", "rootTag": ""}
        text = self._inject_cookie_culture((command_xml or "").strip(), web_cookie)
        await self._register(web_cookie, _FutureWaiter(fut))
        resp = ""
        try:
            await self._ensure_upstream()
            uw = self._upstream_writer
            assert uw is not None
            async with self._write_upstream_lock:
                log_gateway_xml(_log, "web instrument_rq OUT", text, cookie=web_cookie, web_rq=True)
                uw.write(_frame(text, self.encoding))
                await uw.drain()
            resp = await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            async with self._cookie_lock:
                self._cookie_to_target.pop(web_cookie, None)
            return {"ok": False, "error": "上游等待应答超时", "xml": "", "rootTag": ""}
        except OSError as e:
            async with self._cookie_lock:
                self._cookie_to_target.pop(web_cookie, None)
            return {"ok": False, "error": f"上游: {e}", "xml": "", "rootTag": ""}
        except Exception as e:
            async with self._cookie_lock:
                self._cookie_to_target.pop(web_cookie, None)
            return {"ok": False, "error": str(e), "xml": (resp or "")[:4000], "rootTag": ""}
        return GatewayHub._instrument_response_dict(resp)

    async def _instrument_rq_tcp_short(self, command_xml: str, *, timeout_s: float) -> Dict[str, Any]:
        """独立 TCP 会话：Logon + 一条命令（与 cornerstone-cli 一致）；与上游长连接并存。"""
        from cornerstone_cli.cli import _tcp_ensure_logon
        from cornerstone_cli.communications.tcp_engine import AsyncTcpCommunicationEngine, TcpEncoding

        if self.encoding == "utf-8":
            enc_enum = TcpEncoding.utf8
        elif self.encoding == "ascii":
            enc_enum = TcpEncoding.ascii
        else:
            enc_enum = TcpEncoding.utf16

        resp = ""
        try:
            engine = AsyncTcpCommunicationEngine(
                request_culture=self.request_culture,
                encoding=enc_enum,
            )
            try:
                if not await engine.connect(self._upstream_host, self._upstream_port):
                    return {"ok": False, "error": "连接仪器失败", "xml": "", "rootTag": ""}
                if not await _tcp_ensure_logon(
                    engine, self.web_user, self.web_password, timeout_s=60.0
                ):
                    return {"ok": False, "error": "仪器 Logon 失败", "xml": "", "rootTag": ""}
                resp = await engine.send_xml(command_xml, timeout_s=timeout_s) or ""
            finally:
                await engine.disconnect()
        except Exception as ex:
            return {"ok": False, "error": str(ex), "xml": resp[:4000], "rootTag": ""}
        return GatewayHub._instrument_response_dict(resp)

    async def forward_add_samples_web(self, payload_xml: str) -> str:
        ok, err = await self._ensure_upstream_instrument_logon_for_web()
        if not ok:
            return f"<Error>{_xml_escape(err)}</Error>"

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        web_cookie = secrets.token_hex(16)
        try:
            root = ET.fromstring(payload_xml)
        except ET.ParseError as e:
            return f"<Error>Invalid XML: {e}</Error>"
        root.set("Cookie", web_cookie)
        root.set("Culture", self.request_culture)
        text = ET.tostring(root, encoding="unicode")
        await self._register(web_cookie, _FutureWaiter(fut))
        try:
            await self._ensure_upstream()
            uw = self._upstream_writer
            assert uw is not None
            async with self._write_upstream_lock:
                _log.info("web OUT AddSamples cookie=%r", web_cookie)
                uw.write(_frame(text, self.encoding))
                await uw.drain()
            return await asyncio.wait_for(fut, timeout=120.0)
        except asyncio.TimeoutError:
            async with self._cookie_lock:
                self._cookie_to_target.pop(web_cookie, None)
            return "<Error>Timeout waiting for upstream</Error>"
        except OSError as e:
            async with self._cookie_lock:
                self._cookie_to_target.pop(web_cookie, None)
            return f"<Error>upstream: {e}</Error>"
        except Exception as e:
            async with self._cookie_lock:
                self._cookie_to_target.pop(web_cookie, None)
            return f"<Error>{e}</Error>"

    async def instrument_rq(self, command_xml: str, *, timeout_s: float = 120.0) -> Dict[str, Any]:
        """
        网页 /api/instrument/* 发往仪器的 Remote Query。

        - **长连接（默认）**：复用网关与 Cornerstone 的上游 TCP，Cookie 路由应答（与网页 AddSamples 同路径）。
        - **短连接**：每次新建 ``AsyncTcpCommunicationEngine`` 连接并 Logon（与 cornerstone-cli 一致）；若仪器仅允许单会话且网关已占长连接，可能失败。
        """
        if not self.web_user or not self.web_password:
            return {"ok": False, "error": "未配置 --web-user / --web-password", "xml": "", "rootTag": ""}
        if self._instrument_short_connection:
            async with self._instrument_sidecar_lock:
                return await self._instrument_rq_tcp_short(command_xml, timeout_s=timeout_s)
        return await self._instrument_rq_upstream_long(command_xml, timeout_s=timeout_s)

    def upstream_connected(self) -> bool:
        if not self._upstream_transport_usable():
            return False
        if self._upstream_heartbeat_interval_s <= 0:
            return True
        last_ok = self._last_upstream_heartbeat_reply_at
        if last_ok <= 0:
            return True
        interval = max(float(self._upstream_heartbeat_interval_s), 0.5)
        return (time.time() - last_ok) <= max(3.0 * interval, 90.0)

    def _schedule_remote_control_state_probe_after_connect(self) -> None:
        """上游新 TCP 建立后异步问询 ``<RemoteControlState/>``（避免在 ``instrument_rq`` 持锁栈内嵌套调用）。"""

        async def _runner() -> None:
            await asyncio.sleep(0.25)
            try:
                await self.probe_remote_control_state_after_upstream_connected()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                _log.warning("RemoteControlState after upstream connect: %s", ex)

        try:
            asyncio.create_task(_runner(), name="gateway_rcs_after_upstream_connect")
        except RuntimeError:
            pass

    async def probe_remote_control_state_after_upstream_connected(self) -> None:
        """上游连接或重连后问询一次 ``<RemoteControlState/>``，仅更新网页展示缓存。"""
        if not self.web_user or not self.web_password:
            async with self._rcs_lock:
                self._remote_control_last_err = "未配置 --web-user / --web-password，无法问询 RemoteControlState"
                self._remote_control_display = "—"
                self._remote_control_active = False
            return
        if not self.upstream_connected():
            return
        try:
            r = await self.instrument_rq("<RemoteControlState/>", timeout_s=15.0)
            ok, active, display, _host_xml, err = _interpret_remote_control_instrument_result(r)
            async with self._rcs_lock:
                if not ok:
                    self._remote_control_last_err = err
                    self._remote_control_display = "—"
                    self._remote_control_active = False
                    return
                self._remote_control_last_err = ""
                self._remote_control_display = (display or "—")[:80]
                self._remote_control_active = active
        except Exception as ex:
            async with self._rcs_lock:
                self._remote_control_last_err = str(ex)[:300]
                self._remote_control_display = "—"
                self._remote_control_active = False

    async def fetch_instrument_info_json(self) -> Dict[str, Any]:
        r = await self.instrument_rq("<InstrumentInfo/>", timeout_s=60.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "xml": (r.get("xml") or "")[:8000],
                "fields": {},
                "versionSummary": "",
            }
        xml = r.get("xml") or ""
        fields = _parse_instrument_info_fields(xml)
        parts = [fields.get("Product", ""), fields.get("Serial", ""), fields.get("Version", "")]
        version_summary = " ".join(p for p in parts if p).strip()
        return {"ok": True, "error": "", "xml": xml, "fields": fields, "versionSummary": version_summary}

    async def fetch_ambients_json_api(self) -> Dict[str, Any]:
        r = await self.instrument_rq("<Ambients/>", timeout_s=90.0)
        if not r["ok"]:
            return {"ok": False, "error": r["error"], "items": [], "rawPreview": (r.get("xml") or "")[:1500]}
        try:
            root = ET.fromstring((r.get("xml") or "").strip())
        except ET.ParseError:
            return {"ok": False, "error": "解析 Ambients 失败", "items": [], "rawPreview": (r.get("xml") or "")[:800]}
        return {
            "ok": True,
            "items": _parse_ambients_items_from_root(root),
            "fetchedAt": time.time(),
        }

    async def fetch_digital_io_json(self) -> Dict[str, Any]:
        """诊断：``<Solenoids/>``（数字输出）与 ``<Switches/>``（数字输入）。"""
        sol_r = await self.instrument_rq("<Solenoids/>", timeout_s=90.0)
        sw_r = await self.instrument_rq("<Switches/>", timeout_s=90.0)
        sol_items: List[Dict[str, Any]] = []
        sw_items: List[Dict[str, Any]] = []
        sol_err = (sol_r.get("error") or "") if not sol_r.get("ok") else ""
        sw_err = (sw_r.get("error") or "") if not sw_r.get("ok") else ""

        if sol_r.get("ok"):
            sol_items, perr = _parse_bit_io_rows(sol_r.get("xml") or "", "Solenoids", "Solenoid")
            if perr:
                sol_err = perr
                sol_items = []
        if sw_r.get("ok"):
            sw_items, perr = _parse_bit_io_rows(sw_r.get("xml") or "", "Switches", "Switch")
            if perr:
                sw_err = perr
                sw_items = []

        for it in sol_items:
            it["iconKind"] = _solenoid_icon_kind(str(it.get("label") or ""), str(it.get("name") or ""))

        for it in sw_items:
            it["displayKind"] = _switch_display_kind(
                str(it.get("name") or ""),
                str(it.get("label") or ""),
                bool(it.get("on")),
            )

        valve_r = await self.instrument_rq("<ValveStates/>", timeout_s=90.0)
        valve_err = (valve_r.get("error") or "") if not valve_r.get("ok") else ""
        valve_state_display = ""
        if valve_r.get("ok"):
            valve_items, vperr = _parse_valve_states(valve_r.get("xml") or "")
            if vperr:
                valve_err = vperr
            else:
                for vs in valve_items:
                    if vs.get("active"):
                        valve_state_display = (vs.get("displayName") or vs.get("name") or "").strip()
                        break

        errs = [e for e in (sol_err, sw_err) if e]
        return {
            "ok": not bool(errs) and bool(sol_r.get("ok")) and bool(sw_r.get("ok")),
            "error": "; ".join(errs) if errs else "",
            "solenoidsError": sol_err,
            "switchesError": sw_err,
            "valveStateError": valve_err,
            "valveStateDisplay": valve_state_display,
            "solenoids": sol_items,
            "switches": sw_items,
            "fetchedAt": time.time(),
        }

    async def fetch_maintenance_counters_json(self) -> Dict[str, Any]:
        """仪器维护计数器：``<Counters/>``。"""
        r = await self.instrument_rq("<Counters/>", timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        items, perr = _parse_maintenance_counters(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        return {"ok": True, "error": "", "items": items, "fetchedAt": time.time()}

    async def fetch_automation_status_json(self) -> Dict[str, Any]:
        """仪器自动状态：``<AutomationStatus/>``。"""
        r = await self.instrument_rq("<AutomationStatus/>", timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "automation": {},
                "rows": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        parsed, perr = _parse_automation_status(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "automation": {},
                "rows": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        return {
            "ok": True,
            "error": "",
            "automation": {k: v for k, v in parsed.items() if k != "rows"},
            "rows": parsed.get("rows") or [],
            "fetchedAt": time.time(),
        }

    async def fetch_system_parameters_json(self) -> Dict[str, Any]:
        """仪器系统参数：``<SystemParameters/>``。"""
        r = await self.instrument_rq("<SystemParameters/>", timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "sections": [],
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        parsed, perr = _parse_system_parameters(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "sections": [],
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        return {
            "ok": True,
            "error": "",
            "sections": parsed.get("sections") or [],
            "fetchedAt": time.time(),
        }

    async def fetch_counter_detail_json(self, counter_key: str) -> Dict[str, Any]:
        """Remote Query：``<Counter Key=\"…\"/>`` 单条详情。"""
        from cornerstone_cli.cli import _build_attr_xml

        k = (counter_key or "").strip()
        if not k:
            return {"ok": False, "error": "缺少 key", "counter": {}, "rawPreview": ""}
        xml = _build_attr_xml("Counter", {"Key": k})
        r = await self.instrument_rq(xml, timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "counter": {},
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        detail, perr = _parse_counter_detail(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "counter": {},
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        return {"ok": True, "error": "", "counter": detail, "fetchedAt": time.time()}

    async def fetch_transports_list_json(self) -> Dict[str, Any]:
        """Remote Query：``<Transports/>`` 传送格式列表。"""
        r = await self.instrument_rq("<Transports/>", timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        items, perr = _parse_transports_list(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        return {"ok": True, "error": "", "items": items, "fetchedAt": time.time()}

    async def fetch_transport_detail_json(self, transport_key: str) -> Dict[str, Any]:
        """Remote Query：``<Transport Key=\"…\"/>`` 单条详情。"""
        from cornerstone_cli.cli import _build_attr_xml

        k = (transport_key or "").strip()
        if not k:
            return {"ok": False, "error": "缺少 key", "transport": {}, "rawPreview": ""}
        xml = _build_attr_xml("Transport", {"Key": k})
        r = await self.instrument_rq(xml, timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "transport": {},
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        detail, perr = _parse_transport_detail(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "transport": {},
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        return {"ok": True, "error": "", "transport": detail, "fetchedAt": time.time()}

    async def fetch_methods_list_json(self) -> Dict[str, Any]:
        """Remote Query：``<Methods/>`` 方法列表。"""
        r = await self.instrument_rq("<Methods/>", timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        items, perr = _parse_methods_list(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        return {"ok": True, "error": "", "items": items, "fetchedAt": time.time()}

    async def fetch_method_detail_json(self, method_key: str) -> Dict[str, Any]:
        """Remote Query：``<Method Key=\"…\"/>`` 单条详情。"""
        from cornerstone_cli.cli import _build_attr_xml

        k = (method_key or "").strip()
        if not k:
            return {"ok": False, "error": "缺少 key", "method": {}, "rawPreview": ""}
        xml = _build_attr_xml("Method", {"Key": k})
        r = await self.instrument_rq(xml, timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "method": {},
                "rawPreview": (r.get("xml") or "")[:8000],
            }
        detail, perr = _parse_method_detail(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "method": {},
                "rawPreview": (r.get("xml") or "")[:8000],
            }
        return {"ok": True, "error": "", "method": detail, "fetchedAt": time.time()}

    async def fetch_standards_list_json(self) -> Dict[str, Any]:
        """Remote Query：``<Standards/>`` 标样列表。"""
        r = await self.instrument_rq("<Standards/>", timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        items, perr = _parse_standards_list(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "items": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        return {"ok": True, "error": "", "items": items, "fetchedAt": time.time()}

    async def fetch_standard_detail_json(self, standard_key: str) -> Dict[str, Any]:
        """Remote Query：``<Standard Key=\"…\"/>`` 单条详情。"""
        from cornerstone_cli.cli import _build_attr_xml

        k = (standard_key or "").strip()
        if not k:
            return {"ok": False, "error": "缺少 key", "standard": {}, "rawPreview": ""}
        xml = _build_attr_xml("Standard", {"Key": k})
        r = await self.instrument_rq(xml, timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "standard": {},
                "rawPreview": (r.get("xml") or "")[:8000],
            }
        detail, perr = _parse_standard_detail(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "standard": {},
                "rawPreview": (r.get("xml") or "")[:8000],
            }
        return {"ok": True, "error": "", "standard": detail, "fetchedAt": time.time()}

    async def fetch_status_widgets_json(self) -> Dict[str, Any]:
        """``Status``：仅请求 gauges（Widgets），不包含系统检查 / 漏气检查结果。"""
        from cornerstone_cli.cli import _build_attr_xml

        xml = _build_attr_xml(
            "Status",
            {
                "IncludeGauges": True,
                "IncludeSystemCheckResults": False,
                "IncludeLeakCheckResults": False,
            },
        )
        r = await self.instrument_rq(xml, timeout_s=90.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "widgets": [],
                "rawPreview": (r.get("xml") or "")[:2000],
            }
        widgets = _parse_status_widgets(r.get("xml") or "")
        return {"ok": True, "widgets": widgets, "fetchedAt": time.time()}

    async def fetch_status_check_json(self) -> Dict[str, Any]:
        """``Status``：Elements / Odometers / 系统检查 / 漏气检查（不含 gauges）。"""
        from cornerstone_cli.cli import _build_attr_xml

        xml = _build_attr_xml(
            "Status",
            {
                "IncludeGauges": False,
                "IncludeSystemCheckResults": True,
                "IncludeLeakCheckResults": True,
            },
        )
        r = await self.instrument_rq(xml, timeout_s=90.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "elements": [],
                "odometers": [],
                "systemCheck": {},
                "leakChecks": [],
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        payload, perr = _status_check_payload_from_xml(r.get("xml") or "")
        if perr:
            return {
                "ok": False,
                "error": perr,
                "elements": [],
                "odometers": [],
                "systemCheck": {},
                "leakChecks": [],
                "rawPreview": (r.get("xml") or "")[:4000],
            }
        return {
            "ok": True,
            "error": "",
            "elements": payload.get("elements") or [],
            "odometers": payload.get("odometers") or [],
            "systemCheck": payload.get("systemCheck") or {},
            "leakChecks": payload.get("leakChecks") or [],
            "fetchedAt": time.time(),
        }

    async def fetch_sets_json(self, filter_key: str, number: int, start_at: int) -> Dict[str, Any]:
        xml = _build_sets_query_xml(filter_key, int(number), int(start_at))
        r = await self.instrument_rq(xml, timeout_s=120.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "items": [],
                "analyteDefs": [],
                "window": {},
                "pagination": {},
                "rawPreview": (r.get("xml") or "")[:2000],
            }
        try:
            items, analyte_defs, win = _parse_sets_response(r["xml"])
        except ET.ParseError as ex:
            return {
                "ok": False,
                "error": f"解析 Sets 失败: {ex}",
                "items": [],
                "analyteDefs": [],
                "window": {},
                "pagination": {},
            }
        pag = _sets_pagination(win, int(number))
        return {
            "ok": True,
            "items": items,
            "analyteDefs": analyte_defs,
            "window": win,
            "pagination": pag,
            "fetchedAt": time.time(),
        }

    async def fetch_remote_import_sets_json(self) -> Dict[str, Any]:
        """RSL ``LastRemoteAddedSets`` 取 Key，再 RQ ``SetsEx`` 批量取 set 概要（供网页「远程录入 Sets」）。"""
        empty: Dict[str, Any] = {
            "items": [],
            "analyteDefs": [],
            "window": {},
            "pagination": {},
            "keys": [],
        }
        r1 = await self.instrument_rq("<LastRemoteAddedSets/>", timeout_s=120.0)
        if not r1["ok"]:
            return {**empty, "ok": False, "error": r1["error"], "rawPreview": (r1.get("xml") or "")[:2000]}
        try:
            keys = _parse_last_remote_added_set_keys(r1["xml"])
        except ET.ParseError as ex:
            return {**empty, "ok": False, "error": f"解析 LastRemoteAddedSets 失败: {ex}"}
        if not keys:
            return {
                **empty,
                "ok": False,
                "error": "LastRemoteAddedSets 未返回任何 Set Key（请先通过 RSL 添加样品）",
            }
        from cornerstone_cli.cli import _build_sets_ex_xml

        r2 = await self.instrument_rq(_build_sets_ex_xml(keys), timeout_s=180.0)
        if not r2["ok"]:
            return {
                **empty,
                "ok": False,
                "error": r2["error"],
                "keys": keys,
                "rawPreview": (r2.get("xml") or "")[:2000],
            }
        try:
            items, analyte_defs = _parse_sets_ex_response(r2["xml"])
        except ET.ParseError as ex:
            return {
                **empty,
                "ok": False,
                "error": f"解析 SetsEx 失败: {ex}",
                "keys": keys,
            }
        return {
            "ok": True,
            "error": "",
            "items": items,
            "analyteDefs": analyte_defs,
            "window": {},
            "pagination": {},
            "keys": keys,
            "source": "LastRemoteAddedSets+SetsEx",
            "fetchedAt": time.time(),
        }

    async def fetch_set_reps_json(self, set_key: str, *, include_detail: bool, tag: int) -> Dict[str, Any]:
        from cornerstone_cli.cli import _build_attr_xml

        if not (set_key or "").strip():
            return {"ok": False, "error": "缺少 set_key", "replicates": []}
        xml = _build_attr_xml(
            "SetReps",
            {
                "Key": set_key.strip(),
                "IncludeDetailData": include_detail,
                "Tag": int(tag),
            },
        )
        r = await self.instrument_rq(xml, timeout_s=180.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "replicates": [],
                "repAnalyteColumns": [],
                "elementStats": [],
                "rawPreview": (r.get("xml") or "")[:3000],
            }
        try:
            reps = _parse_set_reps_replicates(r["xml"])
        except ET.ParseError as ex:
            return {
                "ok": False,
                "error": f"解析 SetReps 失败: {ex}",
                "replicates": [],
                "repAnalyteColumns": [],
                "elementStats": [],
            }
        rep_cols = _rep_analyte_columns_from_first_replicate(r["xml"])
        stats = _element_stats_for_replicates(reps, rep_cols)
        return {
            "ok": True,
            "replicates": reps,
            "repAnalyteColumns": rep_cols,
            "elementStats": stats,
            "fetchedAt": time.time(),
            "rawXmlTruncated": (r["xml"][:4000] + ("..." if len(r["xml"]) > 4000 else "")),
        }

    async def fetch_rep_plot_json(self, set_key: str, tag: str) -> Dict[str, Any]:
        from cornerstone_cli.cli import _build_attr_xml

        if not (set_key or "").strip() or tag is None or str(tag).strip() == "":
            return {
                "ok": False,
                "error": "缺少 set_key 或 tag",
                "hasImage": False,
                "hasSeries": False,
                "hasAnalytePlotSeries": False,
                "series": [],
                "analytePlotSeries": [],
            }
        xml = _build_attr_xml("RepPlot", {"SetKey": set_key.strip(), "Tag": str(tag).strip()})
        r = await self.instrument_rq(xml, timeout_s=180.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "hasImage": False,
                "hasSeries": False,
                "hasAnalytePlotSeries": False,
                "series": [],
                "analytePlotSeries": [],
                "xmlPreview": (r.get("xml") or "")[:2500],
            }
        mime, b64 = _extract_embedded_image_from_xml(r["xml"])
        analyte_series = _parse_rep_plot_analyte_series(r["xml"])
        plot_series = _parse_rep_plot_series(r["xml"]) if not analyte_series else []
        return {
            "ok": True,
            "hasImage": bool(b64),
            "imageMime": mime,
            "imageBase64": b64,
            "hasAnalytePlotSeries": bool(analyte_series),
            "analytePlotSeries": analyte_series,
            "hasSeries": bool(analyte_series) or bool(plot_series),
            "series": plot_series,
            "xmlPreview": ("" if b64 else (r["xml"][:3500] + ("..." if len(r["xml"]) > 3500 else ""))),
            "fetchedAt": time.time(),
        }

    async def fetch_rep_detail_json(self, set_key: str, tag: str) -> Dict[str, Any]:
        from cornerstone_cli.cli import _build_attr_xml

        if not (set_key or "").strip() or tag is None or str(tag).strip() == "":
            return {
                "ok": False,
                "error": "缺少 set_key 或 tag",
                "errorCode": "",
                "errorMessage": "",
                "tag": "",
                "detailFields": [],
            }
        xml = _build_attr_xml("RepDetail", {"SetKey": set_key.strip(), "Tag": str(tag).strip()})
        r = await self.instrument_rq(xml, timeout_s=180.0)
        if not r["ok"]:
            return {
                "ok": False,
                "error": r["error"],
                "errorCode": "",
                "errorMessage": "",
                "tag": str(tag).strip(),
                "detailFields": [],
                "xmlPreview": (r.get("xml") or "")[:2500],
            }
        parsed = _parse_rep_detail_fields(r["xml"])
        parsed["fetchedAt"] = time.time()
        rx = r.get("xml") or ""
        parsed["rawXmlTruncated"] = rx[:4000] + ("..." if len(rx) > 4000 else "")
        if not parsed.get("ok") and r.get("xml"):
            parsed.setdefault("xmlPreview", rx[:2500])
        return parsed

    async def fetch_set_collection_stats_json(self, set_key: str) -> Dict[str, Any]:
        r = await self.fetch_set_reps_json(set_key, include_detail=True, tag=-1)
        if not r["ok"]:
            return r
        reps = r.get("replicates") or []
        agg = _aggregate_replicate_field_stats(reps)
        return {
            "ok": True,
            "setKey": set_key.strip(),
            "replicateCount": agg["replicateCount"],
            "fieldStats": agg["fieldStats"],
            "tags": [str(x.get("tag", "")) for x in reps],
            "fetchedAt": time.time(),
        }
