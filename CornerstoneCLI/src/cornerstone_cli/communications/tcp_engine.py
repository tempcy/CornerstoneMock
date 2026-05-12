from __future__ import annotations

import asyncio
import contextlib
import secrets
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional
from xml.etree import ElementTree as ET

# 与 C# CommunicationEngine 一致：心跳命令
HEARTBEAT_XML = "<Heartbeat/>"


class TcpEncoding(str, Enum):
    utf16 = "utf-16-le"  # C# Encoding.Unicode
    utf8 = "utf-8"
    ascii = "ascii"


TrafficCallback = Callable[[str, str], None]
MessageCallback = Callable[[str], None]
DisconnectedCallback = Callable[[], None]


@dataclass(frozen=True)
class SendResult:
    cookie: str


class AsyncTcpCommunicationEngine:
    """
    Python 版通信引擎（对齐 C# `CommunicationEngine` 的 TCP 行为）：

    - 帧格式：int32_le(length) + payload bytes
    - payload 默认编码：UTF-16LE（可切换 utf8/ascii）
    - 若发送 XML，会在 root 上注入 Cookie/Culture
    - 接收端：XML/JSON/文本，尽量按 Cookie 路由到等待者，否则作为异步消息回调
    """

    def __init__(
        self,
        *,
        request_culture: str = "en-US",
        encoding: TcpEncoding = TcpEncoding.utf16,
        on_message: Optional[MessageCallback] = None,
        on_traffic: Optional[TrafficCallback] = None,
        on_disconnected: Optional[DisconnectedCallback] = None,
        heartbeat_interval_s: float = 0.0,
        heartbeat_idle_timeout_s: float = 0.0,
    ) -> None:
        self.request_culture = request_culture
        self.encoding = encoding
        self._on_message = on_message
        self._on_traffic = on_traffic
        self._on_disconnected = on_disconnected
        self.heartbeat_interval_s = heartbeat_interval_s
        self.heartbeat_idle_timeout_s = heartbeat_idle_timeout_s

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._send_lock = asyncio.Lock()
        self._last_received_at: float = 0.0

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, host: str, port: int) -> bool:
        await self.disconnect()
        try:
            self._reader, self._writer = await asyncio.open_connection(host, port)
        except Exception:
            await self.disconnect()
            return False

        self._last_received_at = time.monotonic()
        self._read_task = asyncio.create_task(self._read_loop(), name="cornerstone_tcp_read_loop")
        if self.heartbeat_interval_s > 0:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="cornerstone_tcp_heartbeat"
            )
        return True

    async def disconnect(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(Exception):
                await self._heartbeat_task
            self._heartbeat_task = None

        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(Exception):
                await self._read_task
            self._read_task = None

        had_connection = self._writer is not None
        if self._writer is not None:
            try:
                self._writer.close()
                with contextlib.suppress(Exception):
                    await self._writer.wait_closed()
            finally:
                self._writer = None
                self._reader = None

        # 取消所有等待中的请求
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()

        if had_connection and self._on_disconnected is not None:
            try:
                self._on_disconnected()
            except Exception:
                pass

    async def send_xml(
        self,
        xml: str,
        *,
        cookie: str = "",
        await_response: bool = True,
        timeout_s: float = 30.0,
    ) -> Optional[str]:
        cookie_to_use, payload = self._prepare_payload(xml, cookie=cookie)
        result = await self.send_raw(payload, cookie=cookie_to_use, await_response=await_response, timeout_s=timeout_s)
        return result

    async def send_raw(
        self,
        data: str,
        *,
        cookie: str = "",
        await_response: bool = True,
        timeout_s: float = 30.0,
    ) -> Optional[str]:
        if not self.connected or self._writer is None:
            raise RuntimeError("TCP 未连接。")

        fut: Optional[asyncio.Future[str]] = None
        if await_response:
            cookie = cookie or self._new_cookie()
            fut = asyncio.get_running_loop().create_future()
            self._pending[cookie] = fut

        raw = data.encode(self.encoding.value, errors="strict")
        frame = struct.pack("<I", len(raw)) + raw

        async with self._send_lock:
            try:
                if self._on_traffic:
                    self._on_traffic("OUT", data)
                self._writer.write(frame)
                await self._writer.drain()
            except Exception:
                await self.disconnect()
                raise

        if fut is None:
            return None

        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        finally:
            # 若超时/取消/完成，清理映射
            self._pending.pop(cookie, None)

    def _new_cookie(self) -> str:
        # C# 用 Guid；这里用 128-bit 随机十六进制即可
        return secrets.token_hex(16)

    def _prepare_payload(self, data: str, *, cookie: str = "") -> tuple[str, str]:
        data_stripped = data.lstrip()
        if not data_stripped.startswith("<"):
            return cookie or self._new_cookie(), data

        cookie_to_use = cookie or self._new_cookie()
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            # 不是合法 XML，按原样发送
            return cookie_to_use, data

        # 对齐 C#：root 加 Cookie/Culture
        root.set("Cookie", cookie_to_use)
        root.set("Culture", self.request_culture)
        payload = ET.tostring(root, encoding="unicode")
        return cookie_to_use, payload

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                header = await self._reader.readexactly(4)
            except asyncio.IncompleteReadError:
                await self.disconnect()
                return
            except asyncio.CancelledError:
                return

            (length,) = struct.unpack("<I", header)
            if length == 0:
                continue

            try:
                payload_bytes = await self._reader.readexactly(length)
            except asyncio.IncompleteReadError:
                await self.disconnect()
                return
            except asyncio.CancelledError:
                return

            try:
                text = payload_bytes.decode(self.encoding.value, errors="replace")
            except Exception:
                text = payload_bytes.decode("utf-8", errors="replace")

            self._last_received_at = time.monotonic()
            if self._on_traffic:
                self._on_traffic("IN", text)

            self._dispatch_incoming(text)

    async def _heartbeat_loop(self) -> None:
        """按 heartbeat_interval_s 发送 <Heartbeat/>，并用 heartbeat_idle_timeout_s 检测连接是否存活。"""
        interval = self.heartbeat_interval_s
        idle_timeout = self.heartbeat_idle_timeout_s
        while self.connected:
            await asyncio.sleep(interval)
            if not self.connected:
                return
            if idle_timeout > 0:
                idle = time.monotonic() - self._last_received_at
                if idle >= idle_timeout:
                    # 超过空闲超时未收到任何数据，认为连接已死
                    await self.disconnect()
                    return
            try:
                await self.send_xml(HEARTBEAT_XML, await_response=False, timeout_s=1.0)
            except (RuntimeError, asyncio.CancelledError):
                return
            except Exception:
                await self.disconnect()
                return

    def _dispatch_incoming(self, text: str) -> None:
        stripped = (text or "").lstrip()
        if not stripped:
            return

        # XML
        if stripped.startswith("<"):
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                self._publish_message(text)
                return

            cookie = root.attrib.get("Cookie", "")
            if root.tag != "CornerstoneMessage" and cookie and cookie in self._pending:
                fut = self._pending.get(cookie)
                if fut is not None and not fut.done():
                    fut.set_result(text)
                    return

            # 非命令响应/找不到 cookie：按异步消息处理
            self._publish_message(text)
            return

        # JSON：无法稳定从 payload 取 cookie，这里按“若仅有一个 pending，就归它”处理
        if stripped.startswith("{") or stripped.startswith("["):
            if len(self._pending) == 1:
                fut = next(iter(self._pending.values()))
                if not fut.done():
                    fut.set_result(text)
                    return
            self._publish_message(text)
            return

        # 纯文本：同上处理
        if len(self._pending) == 1:
            fut = next(iter(self._pending.values()))
            if not fut.done():
                fut.set_result(text)
                return
        self._publish_message(text)

    def _publish_message(self, text: str) -> None:
        if self._on_message is not None:
            self._on_message(text)
