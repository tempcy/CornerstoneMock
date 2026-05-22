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

from .hub_types import PendingAddSamples, _FutureWaiter
from .protocol import *
from .parsers import *
from .queue_persistence import (
    load_add_samples_queue,
    resolve_queue_persist_path,
    save_add_samples_queue,
)

from .hub_helpers import *

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
        web_user: str,
        web_password: str,
        privileged_add_samples_host: str = "",
        request_culture: str = "en-US",
        tcp_listen_host: str = "",
        tcp_listen_port: int = 0,
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
            print(
                f"[bridge] 已从磁盘恢复 {len(restored)} 条 AddSamples 队列: {self._queue_persist_path}"
            )
            self._persist_add_samples_queue()

        self._upstream_reader_task: Optional[asyncio.Task[None]] = None
        self._upstream_heartbeat_task: Optional[asyncio.Task[None]] = None
        self._upstream_reconnect_task: Optional[asyncio.Task[None]] = None
        self._instrument_sidecar_lock = asyncio.Lock()

        self._tcp_listen_host = (tcp_listen_host or "").strip()
        self._tcp_listen_port = int(tcp_listen_port)
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
                print(f"[gateway] upstream Logoff (cookie={logoff_cookie!r})")
                uw.write(_frame(payload, self.encoding))
                await uw.drain()
            await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            print("[gateway] upstream Logoff wait timeout (proceeding to disconnect)")
        except (asyncio.CancelledError, OSError, RuntimeError):
            pass
        except Exception as e:
            print(f"[gateway] upstream Logoff error: {e}")
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
            w = self._upstream_writer
            if w is None or w.is_closing():
                return
            await self._send_upstream_heartbeat_once()

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
        except asyncio.TimeoutError:
            async with self._cookie_lock:
                self._cookie_to_target.pop(hb_cookie, None)
            print("[gateway] upstream Heartbeat wait timeout")
        except (asyncio.CancelledError, OSError, RuntimeError):
            async with self._cookie_lock:
                self._cookie_to_target.pop(hb_cookie, None)
        except Exception as e:
            async with self._cookie_lock:
                self._cookie_to_target.pop(hb_cookie, None)
            print(f"[gateway] upstream Heartbeat error: {e}")

    async def _upstream_reconnect_worker(self) -> None:
        if not self._upstream_auto_reconnect:
            return
        delay = 1.0
        while True:
            await asyncio.sleep(delay)
            try:
                async with self._upstream_connect_lock:
                    w = self._upstream_writer
                    if w is not None and not w.is_closing():
                        return
                await self._ensure_upstream()
                print("[gateway] upstream reconnected after drop")
                if self.web_user and self.web_password:
                    ok, err = await self._ensure_upstream_instrument_logon_for_web()
                    if not ok:
                        print(f"[gateway] post-reconnect web Logon: {err}")
                return
            except asyncio.CancelledError:
                return
            except Exception as ex:
                print(
                    f"[gateway] upstream reconnect attempt failed: {ex} "
                    f"(next in {min(delay * 2, 60.0):.0f}s)"
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
        created_new = False
        async with self._upstream_connect_lock:
            if self._upstream_transport_usable():
                assert self._upstream_reader is not None
                return self._upstream_reader, self._upstream_writer
        if self._upstream_writer is not None or self._upstream_reader_task is not None:
            print("[gateway] upstream transport stale after drop; reconnecting")
            await self._drop_upstream_transport()
        async with self._upstream_connect_lock:
            print(
                f"[gateway] connecting upstream {self._upstream_host}:{self._upstream_port} "
                f"(encoding={self.encoding})"
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

    async def _upstream_read_loop(self) -> None:
        assert self._upstream_reader is not None
        enc = self.encoding
        try:
            while self._upstream_reader is not None:
                try:
                    header = await self._upstream_reader.readexactly(4)
                except (asyncio.IncompleteReadError, asyncio.CancelledError):
                    break
                except _UPSTREAM_READ_DISCONNECT_EXC as ex:
                    print(f"[gateway] upstream read disconnected: {ex}")
                    break
                (length,) = struct.unpack("<I", header)
                if length == 0:
                    continue
                try:
                    payload_bytes = await self._upstream_reader.readexactly(length)
                except (asyncio.IncompleteReadError, asyncio.CancelledError):
                    break
                except _UPSTREAM_READ_DISCONNECT_EXC as ex:
                    print(f"[gateway] upstream read disconnected: {ex}")
                    break
                text = payload_bytes.decode(enc, errors="replace")
                cookie = _parse_cookie_from_payload(text)
                tag = _root_tag(text)
                if tag == "Logon":
                    ec = ""
                    with contextlib.suppress(ET.ParseError):
                        root = ET.fromstring(text)
                        ec = (root.attrib.get("ErrorCode") or "").strip()
                    if ec == "0":
                        self._logon_seen_upstream_success = True
                        self._upstream_session_authenticated = True

                print(
                    f"[gateway] upstream IN (cookie={cookie!r}): {text[:500]}{'...' if len(text) > 500 else ''}"
                )
                async with self._cookie_lock:
                    target = self._cookie_to_target.pop(cookie, None) if cookie else None
                if target is None:
                    if tag and "heartbeat" in str(tag).lower():
                        continue
                    print(f"[gateway] orphan upstream response (cookie={cookie!r})")
                    continue
                if isinstance(target, _FutureWaiter):
                    if not target.fut.done():
                        target.fut.set_result(text)
                    continue
                if target.is_closing():
                    continue
                try:
                    target.write(_frame(text, enc))
                    await target.drain()
                except Exception as e:
                    print(f"[gateway] failed to deliver to client: {e}")
        except Exception as ex:
            if not isinstance(ex, asyncio.CancelledError):
                print(f"[gateway] upstream read loop error: {ex}")
        finally:
            print("[gateway] upstream read loop ended")
            if self._shutting_down:
                return
            await self._drop_upstream_transport()
            if self._upstream_auto_reconnect:
                if self._upstream_reconnect_task is None or self._upstream_reconnect_task.done():
                    self._upstream_reconnect_task = asyncio.create_task(
                        self._upstream_reconnect_worker(), name="gateway_upstream_reconnect"
                    )

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
                print(f"[gateway] web upstream Logon (cookie={logon_cookie!r})")
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
        if tag_name == "Logon":
            text = _logon_merge_web_credentials(text, self.web_user, self.web_password)
        elif self.web_user and self.web_password:
            ok, err = await self._ensure_upstream_instrument_logon_for_web()
            if not ok:
                print(f"[gateway] TCP→upstream: 上游网页账号登录未就绪（{err}），仍尝试转发。")
        cookie = _parse_cookie_from_payload(text)
        if not cookie:
            cookie = secrets.token_hex(16)
            text = self._inject_cookie_culture(text, cookie)
        await self._register(cookie, client_writer)
        await self._ensure_upstream()
        uw = self._upstream_writer
        assert uw is not None
        async with self._write_upstream_lock:
            print(
                f"[gateway] upstream OUT (cookie={cookie!r}): {text[:500]}{'...' if len(text) > 500 else ''}"
            )
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
                print(
                    f"[gateway] web upstream instrument_rq long (cookie={web_cookie!r}): "
                    f"{text[:400]}{'...' if len(text) > 400 else ''}"
                )
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
                print(f"[gateway] web OUT AddSamples (cookie={web_cookie!r})")
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
        async with self._instrument_sidecar_lock:
            if self._instrument_short_connection:
                return await self._instrument_rq_tcp_short(command_xml, timeout_s=timeout_s)
            return await self._instrument_rq_upstream_long(command_xml, timeout_s=timeout_s)

    def upstream_connected(self) -> bool:
        w = self._upstream_writer
        return w is not None and not w.is_closing()

    def _schedule_remote_control_state_probe_after_connect(self) -> None:
        """上游新 TCP 建立后异步问询 ``<RemoteControlState/>``（避免在 ``instrument_rq`` 持锁栈内嵌套调用）。"""

        async def _runner() -> None:
            await asyncio.sleep(0.25)
            try:
                await self.probe_remote_control_state_after_upstream_connected()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                print(f"[gateway] RemoteControlState after upstream connect: {ex}")

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
