"""COMPAC 串口协议：试样电文编解码、状态查询报文、接收缓冲与校验。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# 缺省控制字符（ASCII）
ENQ = 0x05
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03
SP = 0x20
SOH = 0x01
CR = 0x0D
LF = 0x0A

TELEGRAM_DATA_LEN = 37
TELEGRAM_FRAME_LEN = 1 + 4 + TELEGRAM_DATA_LEN + 3 + 1  # STX+BCT+DATA+CKS+ETX

_STATUS_REQUEST_RE = re.compile(rb"\x01AREQUEST\r\n", re.ASCII)
_STATUS_LINE_RE = re.compile(
    rb"\x01A([\x20-\x7E]{10})(\d{1,2})\r\n",
    re.ASCII,
)


@dataclass(frozen=True)
class CompacControlChars:
    """可自定义的控制字符（缺省见模块常量）。"""

    enq: int = ENQ
    ack: int = ACK
    nak: int = NAK
    stx: int = STX
    etx: int = ETX
    sp: int = SP
    soh: int = SOH


@dataclass(frozen=True)
class CompacSampleFields:
    sample_id: str
    sample_type: str


@dataclass(frozen=True)
class CompacStatusMessage:
    """仪器 STATUS 应答解析结果（10 位状态字符 A–J + 错误码）。"""

    raw_line: bytes
    status_chars: str
    error_code: int
    automatic_mode: bool
    manual_mode: bool
    ready_to_start: bool
    warning: bool
    error: bool

    @classmethod
    def from_parsed(cls, raw: bytes, chars: str, err: int) -> "CompacStatusMessage":
        padded = (chars + "          ")[:10]
        return cls(
            raw_line=raw,
            status_chars=padded,
            error_code=err,
            automatic_mode=padded[0] == "1",
            manual_mode=padded[1] == "1",
            ready_to_start=padded[2] == "1",
            warning=padded[3] == "1",
            error=padded[4] == "1",
        )


@dataclass(frozen=True)
class CompacRecvAnomaly:
    kind: str
    message: str
    raw: bytes


@dataclass
class CompacDrainResult:
    telegrams: List[bytes] = field(default_factory=list)
    status_lines: List[bytes] = field(default_factory=list)
    status_requests: List[bytes] = field(default_factory=list)
    control_bytes: List[int] = field(default_factory=list)
    anomalies: List[CompacRecvAnomaly] = field(default_factory=list)
    bytes_discarded: int = 0


@dataclass
class CompacRecvBuffer:
    """
    串口字节流顺序缓冲：粘包/拆包下提取完整 COMPAC 电文或 STATUS 行。

    无法识别的字节在 drain 时丢弃直至同步到 STX、SOH 或单字节控制符。
    """

    buf: bytes = b""
    ctrl: CompacControlChars = field(default_factory=CompacControlChars)
    verify_bct_cks: bool = True
    idle_clear_sec: float = 30.0
    last_append_at: float = 0.0

    def clear(self) -> None:
        self.buf = b""

    def append(self, chunk: bytes, *, now: Optional[float] = None) -> bool:
        if not chunk:
            return False
        ts = time.monotonic() if now is None else now
        cleared = False
        if (
            self.buf
            and self.last_append_at > 0
            and self.idle_clear_sec > 0
            and (ts - self.last_append_at) > self.idle_clear_sec
        ):
            self.clear()
            cleared = True
        self.buf += chunk
        self.last_append_at = ts
        return cleared

    def drain(self) -> CompacDrainResult:
        out = CompacDrainResult()
        ctrl_set = {
            self.ctrl.enq,
            self.ctrl.ack,
            self.ctrl.nak,
        }
        while self.buf:
            b0 = self.buf[0]

            if b0 in ctrl_set and len(self.buf) == 1:
                out.control_bytes.append(b0)
                self.buf = b""
                break
            if b0 in ctrl_set:
                out.control_bytes.append(b0)
                self.buf = self.buf[1:]
                continue

            if b0 == self.ctrl.stx:
                if len(self.buf) < TELEGRAM_FRAME_LEN:
                    break
                frame = self.buf[:TELEGRAM_FRAME_LEN]
                ok, err = validate_telegram(
                    frame, ctrl=self.ctrl, verify_bct_cks=self.verify_bct_cks
                )
                if ok:
                    out.telegrams.append(frame)
                    self.buf = self.buf[TELEGRAM_FRAME_LEN:]
                    continue
                out.anomalies.append(
                    CompacRecvAnomaly(kind="bad_telegram", message=err or "invalid", raw=frame)
                )
                out.bytes_discarded += 1
                self.buf = self.buf[1:]
                continue

            if b0 == self.ctrl.soh:
                req = _STATUS_REQUEST_RE.match(self.buf)
                if req is not None:
                    line = req.group(0)
                    out.status_requests.append(line)
                    self.buf = self.buf[len(line) :]
                    continue
                m = _STATUS_LINE_RE.search(self.buf)
                if m is None:
                    if CR in self.buf[:64] and LF in self.buf[:64]:
                        end = self.buf.find(LF, 0, 64)
                        if end >= 0:
                            bad = self.buf[: end + 1]
                            out.anomalies.append(
                                CompacRecvAnomaly(
                                    kind="bad_status_line",
                                    message="status line parse failed",
                                    raw=bad,
                                )
                            )
                            out.bytes_discarded += len(bad)
                            self.buf = self.buf[end + 1 :]
                            continue
                    break
                line = m.group(0)
                out.status_lines.append(line)
                self.buf = self.buf[len(line) :]
                continue

            out.bytes_discarded += 1
            self.buf = self.buf[1:]

        return out


def _pad_field(text: str, width: int) -> str:
    s = (text or "")[:width]
    return s + " " * (width - len(s))


def build_sample_data(sample_id: str, sample_type: str, *, sp: int = SP) -> bytes:
    """构造 37 字节 DATA：SampleID(15)+SP+SampleType(20)+SP。"""
    sid = _pad_field(sample_id, 15)
    stype = _pad_field(sample_type, 20)
    raw = f"{sid}{chr(sp)}{stype}{chr(sp)}"
    if len(raw) != TELEGRAM_DATA_LEN:
        raise ValueError(f"DATA length must be {TELEGRAM_DATA_LEN}, got {len(raw)}")
    return raw.encode("ascii")


def _compute_bct(data: bytes, stx: int) -> str:
    """BCT = STX+BCT+DATA 范围内各字符 ASCII 十进制之和（4 位十进制，迭代收敛）。"""
    bct_str = "0000"
    for _ in range(12):
        body = bytes([stx]) + bct_str.encode("ascii") + data
        new_bct = f"{sum(body):04d}"
        if new_bct == bct_str:
            break
        bct_str = new_bct
    return bct_str


def _compute_cks(stx_bct_data: bytes) -> str:
    total = sum(stx_bct_data) % 255
    return f"{total:03d}"


def build_sample_telegram(
    sample_id: str,
    sample_type: str,
    *,
    ctrl: CompacControlChars = CompacControlChars(),
) -> bytes:
    """编码完整试样电文 STX+BCT+DATA+CKS+ETX。"""
    data = build_sample_data(sample_id, sample_type, sp=ctrl.sp)
    bct = _compute_bct(data, ctrl.stx)
    stx_bct_data = bytes([ctrl.stx]) + bct.encode("ascii") + data
    cks = _compute_cks(stx_bct_data)
    return stx_bct_data + cks.encode("ascii") + bytes([ctrl.etx])


def parse_sample_telegram(
    frame: bytes,
    *,
    ctrl: CompacControlChars = CompacControlChars(),
    verify_bct_cks: bool = True,
) -> Tuple[Optional[CompacSampleFields], Optional[str]]:
    ok, err = validate_telegram(frame, ctrl=ctrl, verify_bct_cks=verify_bct_cks)
    if not ok:
        return None, err
    data = frame[5 : 5 + TELEGRAM_DATA_LEN].decode("ascii")
    sample_id = data[0:15].rstrip()
    sample_type = data[16:36].rstrip()
    return CompacSampleFields(sample_id=sample_id, sample_type=sample_type), None


def validate_telegram(
    frame: bytes,
    *,
    ctrl: CompacControlChars = CompacControlChars(),
    verify_bct_cks: bool = True,
) -> Tuple[bool, Optional[str]]:
    if len(frame) != TELEGRAM_FRAME_LEN:
        return False, f"length:{len(frame)}"
    if frame[0] != ctrl.stx or frame[-1] != ctrl.etx:
        return False, "stx_etx"
    bct_str = frame[1:5].decode("ascii", errors="replace")
    if not bct_str.isdigit():
        return False, "bct_not_digit"
    cks_str = frame[5 + TELEGRAM_DATA_LEN : 5 + TELEGRAM_DATA_LEN + 3].decode(
        "ascii", errors="replace"
    )
    if not cks_str.isdigit():
        return False, "cks_not_digit"
    if not verify_bct_cks:
        return True, None
    stx_bct_data = frame[: 5 + TELEGRAM_DATA_LEN]
    expect_bct = _compute_bct(frame[5 : 5 + TELEGRAM_DATA_LEN], ctrl.stx)
    if bct_str != expect_bct:
        return False, f"bct_mismatch:{bct_str}!={expect_bct}"
    expect_cks = _compute_cks(stx_bct_data)
    if cks_str != expect_cks:
        return False, f"cks_mismatch:{cks_str}!={expect_cks}"
    return True, None


def build_status_request(*, ctrl: CompacControlChars = CompacControlChars()) -> bytes:
    """SOH + AREQUEST + CR + LF（A 与 REQUEST 之间无空格）。"""
    return bytes([ctrl.soh]) + b"AREQUEST\r\n"


def build_status_response(
    status_chars: str,
    error_code: int,
    *,
    ctrl: CompacControlChars = CompacControlChars(),
) -> bytes:
    """SOH + A + StatusMessage(10) + Error# + CR + LF（字段间无空格）。"""
    chars = (status_chars or "")[:10].ljust(10)
    err = max(0, min(99, int(error_code)))
    return bytes([ctrl.soh]) + f"A{chars}{err}\r\n".encode("ascii")


def is_status_request(line: bytes) -> bool:
    return _STATUS_REQUEST_RE.fullmatch(line) is not None


def parse_status_response(line: bytes) -> Tuple[Optional[CompacStatusMessage], Optional[str]]:
    m = _STATUS_LINE_RE.fullmatch(line)
    if m is None:
        return None, "status_line_format"
    chars = m.group(1).decode("ascii")
    err = int(m.group(2))
    return CompacStatusMessage.from_parsed(line, chars, err), None


def bytes_name(b: int) -> str:
    return {ENQ: "ENQ", ACK: "ACK", NAK: "NAK", STX: "STX", ETX: "ETX", SOH: "SOH"}.get(
        b, f"0x{b:02X}"
    )
