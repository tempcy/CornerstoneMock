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
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

from cornerstone_cli.communications.tcp_engine import HEARTBEAT_XML

_COOKIE_ATTR_RE = re.compile(r'\bCookie="([^"]*)"', re.IGNORECASE)
_ROOT_TAG_RE = re.compile(r"<(\w+)")


def _normalize_encoding(value: str) -> str:
    v = value.strip().lower()
    if v in ("utf16", "utf-16", "unicode", "utf-16le", "utf-16-le"):
        return "utf-16-le"
    if v in ("utf8", "utf-8"):
        return "utf-8"
    if v in ("ascii",):
        return "ascii"
    raise argparse.ArgumentTypeError(f"不支持的 encoding: {value}（可选: utf16/utf8/ascii）")


_CLIENT_GONE_EXC = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


async def _safe_stream_drain(writer: asyncio.StreamWriter) -> bool:
    """向客户端写完后 drain；对端已断开时静默返回 False。"""
    try:
        await writer.drain()
        return True
    except _CLIENT_GONE_EXC:
        return False
    except OSError as e:
        if getattr(e, "winerror", None) in (64, 10054, 995):
            return False
        raise


async def _async_close_stream_writer(writer: Optional[asyncio.StreamWriter]) -> None:
    """``close()`` 后必须 ``await wait_closed()``，避免 Windows Proactor 在事件循环关闭后析构报警。"""
    if writer is None or writer.is_closing():
        return
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


def _frame(payload_text: str, encoding: str) -> bytes:
    payload = payload_text.encode(encoding, errors="strict")
    return struct.pack("<I", len(payload)) + payload


def _strip_xml_prefix(text: str) -> str:
    return (text or "").lstrip("\ufeff").lstrip()


def _normalize_frame_encoding(encoding: str) -> str:
    enc = (encoding or "").strip().lower().replace("_", "-")
    if enc in ("utf-16-le", "utf16", "utf-16", "unicode", "utf-16le"):
        return "utf-16-le"
    if enc == "utf-8":
        return "utf-8"
    if enc == "ascii":
        return "ascii"
    return enc


# 单帧 payload 上限（4 字节小端长度头之后的正文；过大视为长度头损坏）
MAX_FRAME_PAYLOAD_BYTES = 16 * 1024 * 1024


def validate_frame_length(length: int, encoding: str) -> Optional[str]:
    """校验长度头；有问题时返回原因字符串。"""
    if length <= 0:
        return "zero_length"
    if length > MAX_FRAME_PAYLOAD_BYTES:
        return "oversize"
    if _normalize_frame_encoding(encoding) == "utf-16-le" and (length % 2) != 0:
        return "utf16_payload_odd_bytes"
    return None


def _utf16le_xml_magic_at(payload_bytes: bytes, offset: int) -> bool:
    return offset + 2 <= len(payload_bytes) and payload_bytes[offset : offset + 2] == b"<\x00"


def _utf16le_inner_framed_at(payload_bytes: bytes, offset: int) -> bool:
    """offset 处为 4 字节 inner_len，且 offset+4 为 UTF-16 ``<``。"""
    n = len(payload_bytes)
    return offset + 6 <= n and _utf16le_xml_magic_at(payload_bytes, offset + 4)


def utf16le_inner_length_at(payload_bytes: bytes, offset: int = 0) -> Optional[int]:
    """
    若 ``payload[offset:offset+4]`` 为 inner 长度且 ``offset+4`` 起为 UTF-16 XML，返回 inner_len；
    否则返回 None（无 inner 前缀，按整段正文处理）。
    """
    if not _utf16le_inner_framed_at(payload_bytes, offset):
        return None
    inner_len = struct.unpack("<I", payload_bytes[offset : offset + 4])[0]
    if inner_len <= 0 or inner_len % 2 != 0:
        return None
    return inner_len


def inner_framed_total_bytes(inner_len: int) -> int:
    """完整 inner 帧占用的 TCP 正文字节数：4 + inner_len。"""
    return 4 + inner_len


def inner_framed_tcp_payload_complete(payload_bytes: bytes) -> bool:
    """
    仪器 inner 分包判定：``inner_len == len(payload) - 4`` 表示本 TCP 正文含完整一条 inner 帧。

    无 inner 前缀时视为完整（由外层 TCP 长度界定）。
    """
    inner_len = utf16le_inner_length_at(payload_bytes, 0)
    if inner_len is None:
        return True
    return inner_len == len(payload_bytes) - 4


def inner_framed_needs_more_tcp(payload_bytes: bytes) -> bool:
    """inner 前缀存在且正文不足 ``4 + inner_len``，需等待后续 TCP 包拼接。"""
    inner_len = utf16le_inner_length_at(payload_bytes, 0)
    if inner_len is None:
        return False
    return len(payload_bytes) < inner_framed_total_bytes(inner_len)


@dataclass(frozen=True)
class CornerstonePayloadSegment:
    """单段解包结果（用于日志诊断与后续解码）。"""

    data: bytes
    offset: int
    inner_length_header: Optional[int]
    inner_trusted: bool
    mode: str  # trusted_inner | untrusted_inner_remainder | raw_xml_tail | whole_payload


def segment_cornerstone_payload(
    payload_bytes: bytes, encoding: str
) -> List[CornerstonePayloadSegment]:
    """
    解包 TCP 长度帧正文（UTF-16-LE 仪器常见「内外双层」）。

    布局::

        [4 字节小端 inner_len][UTF-16 XML × inner_len][可选：下一段 …]

    外层 ``readexactly(outer_len)`` 已消费；``inner_len`` 为正文内子长度，不是 TCP 头。
    若 ``body_start + inner_len == n`` 或下一偏移处为 ``3c 00``（``<``），则信任 inner_len 并继续拆下一段。
    """
    enc = _normalize_frame_encoding(encoding)
    if enc != "utf-16-le" or len(payload_bytes) < 2:
        return [
            CornerstonePayloadSegment(
                data=payload_bytes,
                offset=0,
                inner_length_header=None,
                inner_trusted=False,
                mode="whole_payload",
            )
        ]

    segments: List[CornerstonePayloadSegment] = []
    off = 0
    n = len(payload_bytes)
    while off < n:
        if off + 6 <= n and _utf16le_xml_magic_at(payload_bytes, off + 4):
            inner_len = struct.unpack("<I", payload_bytes[off : off + 4])[0]
            body_start = off + 4
            next_off = body_start + inner_len
            trusted_inner = (
                inner_len > 0
                and inner_len % 2 == 0
                and next_off <= n
                and (
                    next_off == n
                    or _utf16le_xml_magic_at(payload_bytes, next_off)
                    or _utf16le_inner_framed_at(payload_bytes, next_off)
                )
            )
            if trusted_inner:
                segments.append(
                    CornerstonePayloadSegment(
                        data=payload_bytes[body_start : body_start + inner_len],
                        offset=off,
                        inner_length_header=inner_len,
                        inner_trusted=True,
                        mode="trusted_inner",
                    )
                )
                off = next_off
                continue
            segments.append(
                CornerstonePayloadSegment(
                    data=payload_bytes[body_start:],
                    offset=off,
                    inner_length_header=inner_len,
                    inner_trusted=False,
                    mode="untrusted_inner_remainder",
                )
            )
            break
        if _utf16le_xml_magic_at(payload_bytes, off):
            segments.append(
                CornerstonePayloadSegment(
                    data=payload_bytes[off:],
                    offset=off,
                    inner_length_header=None,
                    inner_trusted=False,
                    mode="raw_xml_tail",
                )
            )
            break
        off += 2
    if not segments:
        return [
            CornerstonePayloadSegment(
                data=payload_bytes,
                offset=0,
                inner_length_header=None,
                inner_trusted=False,
                mode="whole_payload",
            )
        ]
    return segments


def unwrap_cornerstone_payload_segments(payload_bytes: bytes, encoding: str) -> List[bytes]:
    return [s.data for s in segment_cornerstone_payload(payload_bytes, encoding)]


def format_payload_segmentation_diagnostics(
    payload_bytes: bytes,
    segments: Sequence[CornerstonePayloadSegment],
    *,
    encoding: str = "utf-16-le",
) -> str:
    """人类可读的拆包诊断（异常日志用，不截断）。"""
    n = len(payload_bytes)
    lines = [f"tcp_payload_bytes={n}"]
    if n >= 4 and _normalize_frame_encoding(encoding) == "utf-16-le":
        hdr = struct.unpack("<I", payload_bytes[0:4])[0]
        lines.append(f"payload[0:4]_as_inner_len={hdr} (compare to tcp_payload_bytes, not TCP header)")
    for i, seg in enumerate(segments):
        inner = (
            "—"
            if seg.inner_length_header is None
            else str(seg.inner_length_header)
        )
        lines.append(
            f"  seg[{i}] offset={seg.offset} inner_hdr={inner} trusted={seg.inner_trusted} "
            f"mode={seg.mode} seg_bytes={len(seg.data)}"
        )
        if seg.data and _utf16le_xml_magic_at(seg.data, 0):
            text, _ = decode_frame_payload_bytes(seg.data, encoding)
            tag = _root_tag(text or "")
            if tag:
                lines.append(f"    root_tag≈{tag!r}")
    return "\n".join(lines)


def decode_inbound_segment_bytes(seg_bytes: bytes, encoding: str) -> Tuple[Optional[str], Optional[str]]:
    """解码单段 UTF-16 正文；若段首误含 4 字节 inner_len 则再试剥离一次。"""
    text, err = decode_frame_payload_bytes(seg_bytes, encoding)
    if err is None and text and frame_xml_defect(text) is None:
        return text, None
    if len(seg_bytes) >= 6 and _utf16le_xml_magic_at(seg_bytes, 4):
        peeled, peel_err = decode_frame_payload_bytes(seg_bytes[4:], encoding)
        if peel_err is None and peeled:
            return peeled, None
    if err:
        return None, err
    return text, None


def frame_xml_routable(text: str) -> bool:
    """
    仪器应答在 ET 下可能无法严格解析（多根拼接、未闭合子树、字段内特殊字符），
    但只要根标签与 Cookie 可识别，仍应按 cookie 路由到 Web/客户端。
    """
    s = _strip_xml_prefix(text)
    if not s.startswith("<"):
        return False
    root_m = _ROOT_TAG_RE.match(s)
    if not root_m:
        return False
    tag = root_m.group(1)
    if tag == "Heartbeat":
        return True
    return _COOKIE_ATTR_RE.search(s) is not None


def split_concatenated_xml_documents(text: str) -> List[str]:
    """
    同一 segment 内多条根 XML（逐段试探 ET 可解析前缀；避免用 ``/><`` 正则切分，
    否则会误伤 ``<Prerequisite .../><Prerequisite`` 等合法子节点）。
    """
    s = _strip_xml_prefix(text)
    if not s:
        return []
    docs: List[str] = []
    i = 0
    while i < len(s):
        start = s.find("<", i)
        if start < 0:
            break
        parsed_end: Optional[int] = None
        for end in range(start + 2, len(s) + 1):
            chunk = s[start:end]
            if end < len(s) and not chunk.rstrip().endswith(">"):
                continue
            try:
                ET.fromstring(chunk)
                parsed_end = end
            except ET.ParseError:
                continue
        if parsed_end is None:
            docs.append(s[start:])
            break
        docs.append(s[start:parsed_end])
        i = parsed_end
    return docs


def decode_frame_payload_bytes(payload_bytes: bytes, encoding: str) -> Tuple[Optional[str], Optional[str]]:
    """按配置编码解码正文；失败返回 (None, reason)。"""
    enc = _normalize_frame_encoding(encoding)
    try:
        if enc == "utf-16-le":
            text = payload_bytes.decode("utf-16-le", errors="strict")
        elif enc == "utf-8":
            text = payload_bytes.decode("utf-8", errors="strict")
        elif enc == "ascii":
            text = payload_bytes.decode("ascii", errors="strict")
        else:
            text = payload_bytes.decode(encoding, errors="strict")
    except UnicodeDecodeError as e:
        return None, f"decode_error:{e}"
    return _strip_xml_prefix(text), None


def frame_xml_defect(text: str) -> Optional[str]:
    """
    判断长度帧正文是否为**完整** XML（勿把截断帧当前缀剥离后再解析）。

    典型截断：``Cookie="0a7d12342`` 无闭合引号/``/>``，或 ``<`` 多于 ``>``。
    """
    s = _strip_xml_prefix(text)
    if not s.startswith("<"):
        return "no_xml_start"
    if s.count("<") > s.count(">"):
        return "truncated_tags"
    if s.count('"') % 2 != 0:
        return "truncated_attribute"
    try:
        ET.fromstring(s)
    except ET.ParseError as e:
        return f"xml_parse_error:{e}"
    return None


def format_frame_hex(payload_bytes: bytes, *, limit: Optional[int] = 96) -> str:
    """十六进制转储；``limit is None`` 时输出全部字节（异常帧日志用）。"""
    if limit is None or len(payload_bytes) <= limit:
        return payload_bytes.hex()
    return payload_bytes[:limit].hex() + "+"


def format_frame_hex_preview(payload_bytes: bytes, limit: int = 96) -> str:
    return format_frame_hex(payload_bytes, limit=limit)


def _parse_cookie_from_payload(text: str) -> str:
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        root = ET.fromstring(stripped)
        return (root.attrib.get("Cookie") or "").strip()
    m = _COOKIE_ATTR_RE.search(stripped)
    return m.group(1).strip() if m else ""


def _root_tag(text: str) -> str:
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        return ET.fromstring(stripped).tag
    m = _ROOT_TAG_RE.match(stripped)
    return m.group(1) if m else ""


def inbound_xml_local_tag(text: str) -> str:
    """解析入站 XML 的本地根标签名（命名空间剥离）；解析失败返回空串。"""
    from .parsers import _xml_local_tag

    root_tag = _root_tag(text)
    if root_tag:
        return _xml_local_tag(root_tag)
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        return _xml_local_tag(ET.fromstring(stripped).tag)
    return ""


def _parse_instrument_info_fields(xml: str) -> Dict[str, str]:
    """解析 ``<InstrumentInfo>`` 下带 ``Label`` 的 ``<Field>``，供网页展示与版本摘要。"""
    fields: Dict[str, str] = {}
    try:
        root = ET.fromstring((xml or "").strip())
    except ET.ParseError:
        return fields
    if root.tag != "InstrumentInfo":
        return fields
    for field in root.findall("Field"):
        label = (field.attrib.get("Label") or "").strip()
        if label:
            fields[label] = (field.text or "").strip()
    return fields


def _synthetic_logon_success(cookie: str) -> str:
    root = ET.Element("Logon")
    if cookie:
        root.set("Cookie", cookie)
    root.set("ErrorCode", "0")
    root.set("ErrorMessage", "Success")
    return ET.tostring(root, encoding="unicode")


def _synthetic_add_samples_held(cookie: str) -> str:
    root = ET.Element("AddSamples")
    if cookie:
        root.set("Cookie", cookie)
    root.set("ErrorCode", "0")
    root.set("ErrorMessage", "HeldAtGateway")
    ET.SubElement(root, "GatewayNote").text = "Queued for web UI; not sent to Cornerstone yet."
    return ET.tostring(root, encoding="unicode")


def _add_samples_name_description(payload_xml: str) -> Tuple[str, str]:
    """从 AddSamples XML 提取用于列表展示的样品名称、说明（新 Set 的 Name/Description；否则 SetKey 与首条 Replicate 的 Comments）。"""
    name, desc = "", ""
    try:
        root = ET.fromstring(payload_xml)
    except ET.ParseError:
        return ("(XML 无法解析)", "")
    set_el = root.find("Set")
    if set_el is not None:
        for field in set_el.findall("Field"):
            fid = (field.attrib.get("Id") or "").strip()
            if fid == "Name":
                name = (field.text or "").strip()
            elif fid == "Description":
                desc = (field.text or "").strip()
    if not name:
        sk = root.find("SetKey")
        if sk is not None and (sk.text or "").strip():
            name = (sk.text or "").strip()
            if len(name) > 48:
                name = name[:45] + "..."
    if not desc:
        for rep in root.iter("Replicate"):
            for field in rep.findall("Field"):
                if (field.attrib.get("Id") or "").strip() == "Comments":
                    desc = (field.text or "").strip()
                    break
            if desc:
                break
    if not name:
        name = "—"
    if not desc:
        desc = "—"
    return name, desc


# ``from .protocol import *``：hub / gateway 使用的 _ 前缀辅助函数 + 少量公开常量
__all__ = [n for n in globals() if n.startswith("_") and not n.startswith("__")] + [
    "MAX_FRAME_PAYLOAD_BYTES",
    "inbound_xml_local_tag",
    "validate_frame_length",
    "CornerstonePayloadSegment",
    "segment_cornerstone_payload",
    "unwrap_cornerstone_payload_segments",
    "format_payload_segmentation_diagnostics",
    "format_frame_hex",
    "utf16le_inner_length_at",
    "inner_framed_total_bytes",
    "inner_framed_tcp_payload_complete",
    "inner_framed_needs_more_tcp",
    "split_concatenated_xml_documents",
    "decode_frame_payload_bytes",
    "decode_inbound_segment_bytes",
    "frame_xml_routable",
    "frame_xml_defect",
    "format_frame_hex_preview",
]
