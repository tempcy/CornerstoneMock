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


def _parse_cookie_from_payload(text: str) -> str:
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        root = ET.fromstring(text)
        return (root.attrib.get("Cookie") or "").strip()
    return ""


def _root_tag(text: str) -> str:
    stripped = _strip_xml_prefix(text)
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        return ET.fromstring(text).tag
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


# ``from .protocol import *`` 需导出以下划线开头的符号（hub / gateway 使用）
__all__ = [n for n in globals() if n.startswith("_") and not n.startswith("__")]
