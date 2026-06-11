"""COMPAC 串口服务：监听、试样队列、状态查询与握手发送。"""
from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from .bridge_logging import get_logger
from .compac_protocol import (
    ACK,
    ENQ,
    NAK,
    CompacControlChars,
    CompacRecvBuffer,
    CompacSampleFields,
    CompacStatusMessage,
    build_sample_telegram,
    build_status_request,
    build_status_response,
    bytes_name,
    parse_sample_telegram,
    parse_status_response,
)
from .compac_serial_port import MemorySerialPort, SerialPortBase, SerialPortError, create_serial_port

_log = get_logger("compac")


@dataclass
class PendingCompacSample:
    entry_id: str
    received_at: float
    source: str
    sample_id: str
    sample_type: str


@dataclass
class CompacSerialConfig:
    enabled: bool = False
    port: str = "/dev/ttyUSB0"
    baud_rate: int = 9600
    data_bits: int = 8
    parity: str = "N"
    stop_bits: int = 1
    listen_enabled: bool = False
    timeout_seconds: float = 5.0
    retry_count: int = 5
    queue_max: int = 32
    recv_idle_clear_seconds: float = 30.0
    force_memory_port: bool = False
    verify_bct_cks: bool = True
    reply_a_request: bool = False
    reply_status_chars: str = "1000000000"
    reply_status_error: int = 0

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "port": self.port,
            "baudRate": self.baud_rate,
            "dataBits": self.data_bits,
            "parity": self.parity,
            "stopBits": self.stop_bits,
            "listenEnabled": self.listen_enabled,
            "timeoutSeconds": self.timeout_seconds,
            "retryCount": self.retry_count,
            "queueMax": self.queue_max,
            "recvIdleClearSeconds": self.recv_idle_clear_seconds,
            "verifyBctCks": self.verify_bct_cks,
            "replyARequest": self.reply_a_request,
            "replyStatusChars": self.reply_status_chars,
            "replyStatusError": self.reply_status_error,
        }


@dataclass
class CompacServiceState:
    port_open: bool = False
    listen_active: bool = False
    last_error: str = ""
    last_rx_at: float = 0.0
    last_tx_at: float = 0.0
    last_status: Optional[CompacStatusMessage] = None
    last_status_at: float = 0.0
    last_received_sample: Optional[CompacSampleFields] = None
    recv_buffer_bytes: int = 0
    telegrams_received: int = 0
    telegrams_sent: int = 0


class CompacSerialService:
    """Bridge 侧 COMPAC Client：向仪器发送试样指令、查询状态；可选监听仪器上行。"""

    def __init__(self, config: Optional[CompacSerialConfig] = None) -> None:
        self.config = config or CompacSerialConfig()
        self.ctrl = CompacControlChars()
        self._recv = CompacRecvBuffer(
            idle_clear_sec=self.config.recv_idle_clear_seconds,
            verify_bct_cks=self.config.verify_bct_cks,
        )
        self._queue: Deque[PendingCompacSample] = deque(maxlen=max(1, self.config.queue_max))
        self._state = CompacServiceState()
        self._port: Optional[SerialPortBase] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self._pending_ctrl: asyncio.Queue[int] = asyncio.Queue()
        self._pending_status_fut: Optional[asyncio.Future[CompacStatusMessage]] = None

    def state_snapshot(self) -> Dict[str, Any]:
        st = self._state
        status = None
        if st.last_status is not None:
            s = st.last_status
            status = {
                "statusChars": s.status_chars,
                "errorCode": s.error_code,
                "automaticMode": s.automatic_mode,
                "manualMode": s.manual_mode,
                "readyToStart": s.ready_to_start,
                "warning": s.warning,
                "error": s.error,
                "fetchedAt": st.last_status_at,
            }
        last_sample = None
        if st.last_received_sample is not None:
            ls = st.last_received_sample
            last_sample = {"sampleId": ls.sample_id, "sampleType": ls.sample_type}
        return {
            "portOpen": st.port_open,
            "listenActive": st.listen_active,
            "lastError": st.last_error,
            "lastRxAt": st.last_rx_at,
            "lastTxAt": st.last_tx_at,
            "recvBufferBytes": st.recv_buffer_bytes,
            "telegramsReceived": st.telegrams_received,
            "telegramsSent": st.telegrams_sent,
            "lastStatus": status,
            "lastReceivedSample": last_sample,
            "queueCount": len(self._queue),
            "queueMax": self.config.queue_max,
        }

    def pending_snapshot(self) -> List[PendingCompacSample]:
        return list(self._queue)

    def get_pending_by_ids(self, ids: set[str]) -> List[PendingCompacSample]:
        return [p for p in self._queue if p.entry_id in ids]

    def enqueue_sample(
        self,
        sample_id: str,
        sample_type: str,
        *,
        source: str = "api",
    ) -> PendingCompacSample:
        entry = PendingCompacSample(
            entry_id=secrets.token_hex(8),
            received_at=time.time(),
            source=source,
            sample_id=(sample_id or "")[:15],
            sample_type=(sample_type or "")[:20],
        )
        self._queue.append(entry)
        return entry

    def apply_config(self, cfg: CompacSerialConfig) -> None:
        self.config = cfg
        self._recv.idle_clear_sec = max(0.0, float(cfg.recv_idle_clear_seconds))
        self._recv.verify_bct_cks = bool(cfg.verify_bct_cks)
        while self._queue.maxlen and len(self._queue) > cfg.queue_max:
            self._queue.popleft()
        new_deque: Deque[PendingCompacSample] = deque(self._queue, maxlen=max(1, cfg.queue_max))
        self._queue = new_deque

    async def set_listen_enabled(self, enabled: bool) -> tuple[bool, str]:
        self.config.listen_enabled = bool(enabled)
        if enabled:
            ok, err = await self.open_port()
            if not ok:
                return False, err
            self._state.listen_active = True
            _log.info("COMPAC serial listen enabled")
            return True, ""
        self._state.listen_active = False
        _log.info("COMPAC serial listen disabled")
        return True, ""

    async def _ensure_reader(self) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            return
        self._reader_task = asyncio.create_task(self._reader_loop(), name="compac-reader")

    async def open_port(self) -> tuple[bool, str]:
        async with self._lock:
            if self._port is not None and self._port.is_open():
                self._state.port_open = True
                await self._ensure_reader()
                return True, ""
            try:
                self._port = create_serial_port(
                    self.config.port,
                    baud_rate=self.config.baud_rate,
                    data_bits=self.config.data_bits,
                    parity=self.config.parity,
                    stop_bits=self.config.stop_bits,
                    force_memory=self.config.force_memory_port,
                )
                self._port.open()
                self._state.port_open = True
                self._state.last_error = ""
                _log.info("COMPAC serial opened: %s @ %s", self.config.port, self.config.baud_rate)
                await self._ensure_reader()
                return True, ""
            except (SerialPortError, OSError) as e:
                self._state.port_open = False
                self._state.last_error = str(e)
                _log.warning("COMPAC serial open failed: %s", e)
                return False, str(e)

    async def close_port(self) -> None:
        await self._stop_reader()
        async with self._lock:
            if self._port is not None:
                self._port.close()
                self._port = None
            self._state.port_open = False
            self._state.listen_active = False

    async def _stop_reader(self) -> None:
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        await self.close_port()

    async def _reader_loop(self) -> None:
        try:
            while True:
                if self._port is None or not self._port.is_open():
                    await asyncio.sleep(0.2)
                    continue
                chunk = await asyncio.to_thread(self._port.read, 4096)
                if chunk:
                    await self._handle_rx(chunk)
                else:
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._state.last_error = str(e)
            _log.error("COMPAC reader loop: %s", e)

    async def _handle_rx(self, chunk: bytes) -> None:
        self._recv.append(chunk)
        self._state.last_rx_at = time.time()
        dr = self._recv.drain()
        self._state.recv_buffer_bytes = len(self._recv.buf)

        for ctrl in dr.control_bytes:
            if self.config.listen_enabled and ctrl == ENQ:
                await self._write(bytes([ACK]))
                continue
            await self._pending_ctrl.put(ctrl)

        for req_line in dr.status_requests:
            if self.config.reply_a_request:
                await self._reply_status_request()
            else:
                _log.debug("COMPAC A REQUEST ignored (reply_a_request=false)")

        for frame in dr.telegrams:
            fields, perr = parse_sample_telegram(
                frame, ctrl=self.ctrl, verify_bct_cks=self.config.verify_bct_cks
            )
            if fields is None:
                _log.warning("COMPAC bad telegram rx: %s", perr)
                if self.config.listen_enabled:
                    await self._write(bytes([NAK]))
                continue
            if self.config.listen_enabled:
                await self._write(bytes([ACK]))
            self._state.last_received_sample = fields
            self._state.telegrams_received += 1
            _log.info(
                "COMPAC sample rx id=%r type=%r",
                fields.sample_id,
                fields.sample_type,
            )

        for line in dr.status_lines:
            msg, serr = parse_status_response(line)
            if msg is None:
                _log.warning("COMPAC bad status rx: %s", serr)
                continue
            self._state.last_status = msg
            self._state.last_status_at = time.time()
            fut = self._pending_status_fut
            if fut is not None and not fut.done():
                fut.set_result(msg)

        for an in dr.anomalies:
            _log.debug("COMPAC recv anomaly %s: %s", an.kind, an.message)

    def _status_reply_payload(self) -> tuple[str, int]:
        if self._state.last_status is not None:
            return self._state.last_status.status_chars, self._state.last_status.error_code
        chars = (self.config.reply_status_chars or "1000000000")[:10].ljust(10)
        return chars, int(self.config.reply_status_error)

    async def _reply_status_request(self) -> None:
        chars, err = self._status_reply_payload()
        resp = build_status_response(chars, err, ctrl=self.ctrl)
        await self._write(resp)
        _log.info("COMPAC A REQUEST replied status=%r error=%s", chars, err)

    async def _write(self, data: bytes) -> None:
        if self._port is None or not self._port.is_open():
            raise SerialPortError("serial port not open")
        await asyncio.to_thread(self._port.write, data)
        self._state.last_tx_at = time.time()

    async def _wait_control(self, expected: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                b = await asyncio.wait_for(self._pending_ctrl.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
            if b == expected:
                return True
            if b == NAK and expected == ACK:
                return False
        return False

    async def _send_with_handshake(self, payload: bytes) -> tuple[bool, str]:
        """Client 侧握手：ENQ → ACK → DATA → ACK。"""
        retries = max(1, int(self.config.retry_count))
        timeout_s = max(0.5, float(self.config.timeout_seconds))
        for attempt in range(1, retries + 1):
            while not self._pending_ctrl.empty():
                try:
                    self._pending_ctrl.get_nowait()
                except asyncio.QueueEmpty:
                    break
            await self._write(bytes([ENQ]))
            if not await self._wait_control(ACK, timeout_s):
                _log.debug("COMPAC handshake ENQ no ACK attempt %s/%s", attempt, retries)
                continue
            await self._write(payload)
            if await self._wait_control(ACK, timeout_s):
                return True, ""
            _log.debug("COMPAC handshake DATA no ACK attempt %s/%s", attempt, retries)
        return False, f"握手失败（已重试 {retries} 次）"

    async def send_sample(
        self,
        sample_id: str,
        sample_type: str,
    ) -> Dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "error": "COMPAC 未启用"}
        ok, err = await self.open_port()
        if not ok:
            return {"ok": False, "error": err}
        telegram = build_sample_telegram(sample_id, sample_type, ctrl=self.ctrl)
        hs_ok, hs_err = await self._send_with_handshake(telegram)
        if not hs_ok:
            self._state.last_error = hs_err
            return {"ok": False, "error": hs_err}
        self._state.telegrams_sent += 1
        self._state.last_error = ""
        return {
            "ok": True,
            "error": "",
            "sampleId": sample_id,
            "sampleType": sample_type,
            "telegramHex": telegram.hex(),
        }

    async def query_status(self) -> Dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "error": "COMPAC 未启用"}
        ok, err = await self.open_port()
        if not ok:
            return {"ok": False, "error": err}
        req = build_status_request(ctrl=self.ctrl)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CompacStatusMessage] = loop.create_future()
        self._pending_status_fut = fut
        try:
            await self._write(req)
            timeout_s = max(0.5, float(self.config.timeout_seconds))
            try:
                msg = await asyncio.wait_for(fut, timeout=timeout_s)
            except asyncio.TimeoutError:
                self._state.last_error = "状态查询超时"
                return {"ok": False, "error": "状态查询超时"}
            return {
                "ok": True,
                "error": "",
                "statusChars": msg.status_chars,
                "errorCode": msg.error_code,
                "automaticMode": msg.automatic_mode,
                "manualMode": msg.manual_mode,
                "readyToStart": msg.ready_to_start,
                "warning": msg.warning,
                "error": msg.error,
            }
        finally:
            self._pending_status_fut = None

    async def send_queued(self, ids: set[str]) -> Dict[str, Any]:
        selected = self.get_pending_by_ids(ids)
        if not selected:
            return {"ok": False, "error": "未选择任何条目或 ID 无效", "results": []}
        results: List[Dict[str, Any]] = []
        for item in selected:
            r = await self.send_sample(item.sample_id, item.sample_type)
            results.append({"id": item.entry_id, **r})
        all_ok = all(r.get("ok") for r in results)
        return {"ok": all_ok, "results": results, "queueKept": True}

    async def bind_memory_peer(self, peer: MemorySerialPort) -> None:
        """测试：将本服务端口绑定到 MemorySerialPort 并启动读循环。"""
        self._port = peer
        peer.open()
        self._state.port_open = True
        await self._ensure_reader()
