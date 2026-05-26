"""上游 TCP 正文（outer payload）统一缓冲与 inner_len 解包。"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .protocol import (
    MAX_FRAME_PAYLOAD_BYTES,
    decode_frame_payload_bytes,
    frame_xml_defect,
    _utf16le_inner_framed_at,
    _utf16le_xml_magic_at,
)

# inner 正文（UTF-16 XML）单条上限，与外层 TCP 帧上限一致
DEFAULT_MAX_INNER_LEN = MAX_FRAME_PAYLOAD_BYTES


@dataclass(frozen=True)
class DrainAnomaly:
    """单条解包异常（已丢弃该条或已清空缓冲）。"""

    kind: str
    message: str
    raw: bytes


@dataclass
class DrainResult:
    """一次 drain 产出的完整 inner 正文（不含 4 字节 inner 头）。"""

    packets: List[bytes] = field(default_factory=list)
    anomalies: List[DrainAnomaly] = field(default_factory=list)
    buffer_cleared: bool = False


@dataclass
class UpstreamRecvBuffer:
    """
    仪器上行字节流缓冲：在 outer TCP 帧 payload 上按 ``[inner_len][utf-16 xml]`` 循环切分。

    - 粘包：一次 append 可 drain 多条
    - 拆包：不完整时保留 buf，由 ``needs_more`` / ``bytes_needed`` 驱动续读
    - 断流：超过 ``idle_clear_sec`` 未 append 则下次 append 前清空
    """

    buf: bytes = b""
    max_inner_len: int = DEFAULT_MAX_INNER_LEN
    idle_clear_sec: float = 30.0
    incomplete_timeout_s: float = 5.0
    last_append_at: float = 0.0
    incomplete_deadline: float = 0.0

    def clear(self) -> None:
        self.buf = b""
        self.incomplete_deadline = 0.0

    def append(self, chunk: bytes, *, now: Optional[float] = None) -> bool:
        """
        追加 outer payload 字节。若距上次 append 超过空闲阈值则先清空。

        返回 True 表示因空闲执行了 clear。
        """
        if not chunk:
            return False
        ts = time.monotonic() if now is None else now
        cleared_idle = False
        if (
            self.buf
            and self.last_append_at > 0
            and self.idle_clear_sec > 0
            and (ts - self.last_append_at) > self.idle_clear_sec
        ):
            self.clear()
            cleared_idle = True
        self.buf += chunk
        self.last_append_at = ts
        return cleared_idle

    def needs_more(self) -> bool:
        """缓冲区内有未凑齐的 inner 帧（含仅收到部分长度头）。"""
        n = len(self.buf)
        if n == 0:
            return False
        if n < 4:
            return True
        inner_len, err = _peek_inner_len(self.buf, 0, self.max_inner_len)
        if err is not None:
            return False
        return n < 4 + inner_len

    def bytes_needed(self) -> int:
        """凑齐当前帧还需的字节数；无有效 inner 头时返回 0。"""
        n = len(self.buf)
        if n < 4:
            return max(0, 4 - n)
        inner_len, err = _peek_inner_len(self.buf, 0, self.max_inner_len)
        if err is not None:
            return 0
        return max(0, (4 + inner_len) - n)

    def incomplete_expired(self, *, now: Optional[float] = None) -> bool:
        if self.incomplete_deadline <= 0 or self.incomplete_timeout_s <= 0:
            return False
        ts = time.monotonic() if now is None else now
        return ts > self.incomplete_deadline

    def incomplete_seconds_left(self, *, now: Optional[float] = None) -> float:
        if self.incomplete_deadline <= 0:
            return 0.0
        ts = time.monotonic() if now is None else now
        return max(0.0, self.incomplete_deadline - ts)

    def _touch_incomplete_deadline(self) -> None:
        if self.incomplete_timeout_s <= 0:
            self.incomplete_deadline = 0.0
            return
        if self.incomplete_deadline <= 0:
            self.incomplete_deadline = time.monotonic() + self.incomplete_timeout_s

    def drain(self) -> DrainResult:
        """从缓冲头部循环取出所有完整的 inner 正文（仅 XML 字节，无 inner 头）。"""
        out = DrainResult()
        while True:
            n = len(self.buf)
            if n < 2:
                if n > 0:
                    self._touch_incomplete_deadline()
                break

            # 无 inner 头、直接 UTF-16 XML（Logon 应答等）；残缺尾段（如 248=inner152+RCS92）须续包
            if _utf16le_xml_magic_at(self.buf, 0) and not _utf16le_inner_framed_at(
                self.buf, 0
            ):
                if _raw_utf16_xml_complete(self.buf):
                    out.packets.append(self.buf)
                    self.buf = b""
                    self.incomplete_deadline = 0.0
                    continue
                self._touch_incomplete_deadline()
                break

            if n < 4:
                self._touch_incomplete_deadline()
                break

            inner_len, err = _peek_inner_len(self.buf, 0, self.max_inner_len)
            if err is not None:
                out.anomalies.append(
                    DrainAnomaly(
                        kind="sync_lost",
                        message=err,
                        raw=bytes(self.buf),
                    )
                )
                self.buf = b""
                self.incomplete_deadline = 0.0
                out.buffer_cleared = True
                break

            total = 4 + inner_len
            if n < total:
                self._touch_incomplete_deadline()
                break

            packet = self.buf[4:total]
            self.buf = self.buf[total:]
            self.incomplete_deadline = 0.0
            out.packets.append(packet)
            if not self.buf:
                break
        return out


def _raw_utf16_xml_complete(buf: bytes) -> bool:
    """缓冲区内无 inner 头的 UTF-16 XML 是否已闭合（勿把 RCS 前缀当整包）。"""
    text, err = decode_frame_payload_bytes(buf, "utf-16-le")
    if err or not text:
        return False
    return frame_xml_defect(text) is None


def _peek_inner_len(
    buf: bytes, offset: int, max_inner_len: int = DEFAULT_MAX_INNER_LEN
) -> Tuple[int, Optional[str]]:
    """校验 offset 处 inner 头；成功返回 (inner_len, None)。"""
    if offset + 4 > len(buf):
        return 0, "short_header"
    inner_len = struct.unpack("<I", buf[offset : offset + 4])[0]
    if inner_len <= 0:
        return inner_len, f"invalid_inner_len:{inner_len}"
    if inner_len % 2 != 0:
        return inner_len, f"inner_len_odd:{inner_len}"
    if inner_len > max_inner_len:
        return inner_len, f"inner_len_oversize:{inner_len}"
    if offset + 6 > len(buf):
        return inner_len, None
    if not _utf16le_xml_magic_at(buf, offset + 4):
        return inner_len, "inner_xml_magic_mismatch"
    return inner_len, None
