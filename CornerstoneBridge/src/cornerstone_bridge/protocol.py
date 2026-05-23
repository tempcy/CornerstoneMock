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
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

from cornerstone_cli.communications.tcp_engine import HEARTBEAT_XML

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


def format_frame_hex_preview(payload_bytes: bytes, limit: int = 96) -> str:
    chunk = payload_bytes[:limit]
    suffix = "+" if len(payload_bytes) > limit else ""
    return chunk.hex() + suffix


def _parse_cookie_from_payload(text: str) -> str:
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        root = ET.fromstring(stripped)
        return (root.attrib.get("Cookie") or "").strip()
    return ""


def _root_tag(text: str) -> str:
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        return ET.fromstring(stripped).tag
    return ""


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
    "decode_frame_payload_bytes",
    "frame_xml_defect",
    "format_frame_hex_preview",
]
