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

from .parsers import _xml_local_tag
from .hub_types import PendingAddSamples

def _web_logon_xml(user: str, password: str) -> str:
    return (
        f"<Logon><User>{_xml_escape(user)}</User>"
        f"<Password>{_xml_escape(password)}</Password></Logon>"
    )


def _logon_merge_web_credentials(xml_text: str, web_user: str, web_password: str) -> str:
    """
    TCP 客户端 ``<Logon>`` 若未带 ``User`` / ``Password``（缺省或文本为空），
    则用网关网页侧配置的账号密码补全后再转发上游；客户端无需自带凭据。
    """
    s = (xml_text or "").strip()
    if not s.startswith("<"):
        return xml_text
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return xml_text
    if _xml_local_tag(root.tag) != "Logon":
        return xml_text
    wu = (web_user or "").strip()
    wp = web_password or ""
    if not wu and not wp:
        return xml_text
    user_el: Optional[ET.Element] = None
    pwd_el: Optional[ET.Element] = None
    for el in root:
        lt = _xml_local_tag(el.tag)
        if lt == "User":
            user_el = el
        elif lt == "Password":
            pwd_el = el
    u = ((user_el.text if user_el is not None else None) or "").strip()
    p = ((pwd_el.text if pwd_el is not None else None) or "").strip()
    changed = False
    if not u and wu:
        if user_el is None:
            user_el = ET.SubElement(root, "User")
        user_el.text = wu
        changed = True
    if not p and wp != "":
        if pwd_el is None:
            pwd_el = ET.SubElement(root, "Password")
        pwd_el.text = wp
        changed = True
    if not changed:
        return xml_text
    return ET.tostring(root, encoding="unicode")


def _logon_user_from_client_xml(xml_text: str) -> str:
    s = (xml_text or "").strip()
    if not s.startswith("<"):
        return ""
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return ""
    if _xml_local_tag(root.tag) != "Logon":
        return ""
    for el in root:
        if _xml_local_tag(el.tag) == "User":
            return ((el.text if el is not None else None) or "").strip()
    return ""


def _upstream_xml_error_code(resp: Optional[str]) -> Optional[str]:
    """解析 XML 根 ``ErrorCode``；无属性或非法 XML 时返回 None。"""
    if not resp or not (resp or "").strip().startswith("<"):
        return None
    try:
        root = ET.fromstring(resp.strip())
    except ET.ParseError:
        return None
    ec = (root.attrib.get("ErrorCode") or "").strip()
    return ec if ec else None


def _upstream_heartbeat_response_ok(resp: Optional[str]) -> bool:
    if not resp or not (resp or "").strip().startswith("<"):
        return False
    try:
        root = ET.fromstring(resp.strip())
    except ET.ParseError:
        return False
    if _xml_local_tag(root.tag).lower() != "heartbeat":
        return False
    ec = (root.attrib.get("ErrorCode") or "").strip()
    return ec == "" or ec == "0"


def _upstream_logon_response_ok(resp: Optional[str]) -> bool:
    if not resp or not (resp or "").strip().startswith("<"):
        return False
    try:
        root = ET.fromstring(resp.strip())
    except ET.ParseError:
        return False
    if root.tag != "Logon":
        return False
    ec = (root.attrib.get("ErrorCode") or "").strip()
    if ec != "0":
        return False
    em = (root.attrib.get("ErrorMessage") or "").strip()
    return em == "" or em.lower() == "success"


def _peer_host_from_peername(peer: object) -> str:
    """从 ``writer.get_extra_info('peername')`` 得到对端主机（不含端口），用于 AddSamples 直通白名单。"""
    if peer is None:
        return ""
    if isinstance(peer, (list, tuple)) and len(peer) >= 1:
        return str(peer[0] or "").strip()
    if isinstance(peer, str):
        return peer.strip()
    return str(peer).strip()


def _parse_remote_control_state_xml(xml: str) -> Tuple[bool, bool, str, str]:
    """
    解析 ``<RemoteControlState>`` 应答。

    返回 ``(recognized, active, display_text, host_from_xml)``。
    """
    xml = (xml or "").strip()
    if not xml.startswith("<"):
        return False, False, "—", ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return False, False, "—", ""
    if root.tag != "RemoteControlState":
        return False, False, "—", ""
    ec = (root.attrib.get("ErrorCode") or "").strip()
    if ec and ec != "0":
        em = (root.attrib.get("ErrorMessage") or "").strip()
        return True, False, f"E{ec}:{em[:48]}", ""
    body = (root.text or "").strip()
    display = body if body else "—"
    active = body.lower() in ("true", "1", "yes")
    host = (
        (root.attrib.get("Host") or root.attrib.get("RemoteHost") or root.attrib.get("ClientIp") or "")
        .strip()
    )
    if not host:
        for el in root:
            if el.tag in ("Host", "RemoteHost", "ClientIp", "ClientAddress"):
                t = (el.text or "").strip()
                if t:
                    host = t
                    break
    return True, active, display, host


def _interpret_remote_control_instrument_result(r: Dict[str, Any]) -> Tuple[bool, bool, str, str, str]:
    """``(ok, active, display, host_from_xml, error)``。"""
    if not r.get("ok"):
        return False, False, "—", "", ((r.get("error") or "instrument_rq failed"))[:500]
    rec, active, display, host = _parse_remote_control_state_xml(r.get("xml") or "")
    if not rec:
        return False, False, "—", "", "RemoteControlState 解析失败"
    return True, active, display, host, ""


def _peer_host_matches_privileged(peer_host: str, privileged: str) -> bool:
    """比较 TCP 对端主机与配置的上位机直通地址（忽略大小写、首尾空白）。"""
    a = _normalize_host_for_policy(peer_host)
    b = _normalize_host_for_policy(privileged)
    return bool(a) and bool(b) and a == b


def _normalize_host_for_policy(host: str) -> str:
    """IP/主机名策略比较用规范化（去首尾空白、小写）。"""
    return (host or "").strip().lower()


def _is_valid_policy_host(h: str) -> bool:
    """拒绝 merge-config 等误写的 ``[]`` / JSON 片段等非 IP 主机名。"""
    if not h:
        return False
    if h in ("[]", "{}", "null"):
        return False
    if h.startswith("[") or h.startswith("{"):
        return False
    return bool(re.match(r"^[\w.\-:]+$", h))


def _parse_host_list(value: object) -> List[str]:
    """从配置值解析 IP 列表（TOML 数组、JSON 字符串或单个字符串）。"""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in ("[]", "{}", "null"):
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return _parse_host_list(parsed)
            return []
        h = _normalize_host_for_policy(s)
        return [h] if _is_valid_policy_host(h) else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        seen: Set[str] = set()
        for item in value:
            if isinstance(item, (list, tuple)):
                for sub in _parse_host_list(item):
                    if sub not in seen:
                        seen.add(sub)
                        out.append(sub)
                continue
            for sub in _parse_host_list(str(item)):
                if sub not in seen:
                    seen.add(sub)
                    out.append(sub)
        return out
    return _parse_host_list(str(value))


def _host_in_blocklist(peer_host: str, blocklist: Set[str]) -> bool:
    h = _normalize_host_for_policy(peer_host)
    return bool(h) and h in blocklist


__all__ = [n for n in globals() if n.startswith("_") and not n.startswith("__")]