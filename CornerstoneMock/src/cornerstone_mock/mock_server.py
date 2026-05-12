from __future__ import annotations

import argparse
import asyncio
import contextlib
import html
import json
import math
import sys
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


def _frame(payload_text: str, encoding: str) -> bytes:
    payload = payload_text.encode(encoding, errors="strict")
    return struct.pack("<I", len(payload)) + payload


def _parse_cookie_from_payload(text: str) -> str:
    stripped = (text or "").lstrip()
    if not stripped.startswith("<"):
        return ""
    with contextlib.suppress(ET.ParseError):
        root = ET.fromstring(text)
        return (root.attrib.get("Cookie") or "").strip()
    return ""


def _root_tag(text: str) -> str:
    stripped = (text or "").lstrip()
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


def _parse_ambients_items_from_root(root: ET.Element) -> List[Dict[str, str]]:
    """将 <Ambients> 下各 <Ambient> 转为前端卡片用字典。"""
    out: List[Dict[str, str]] = []
    for amb in root.findall("Ambient"):

        def txt(tag: str) -> str:
            el = amb.find(tag)
            return (el.text or "").strip() if el is not None else ""

        def raw(tag: str) -> str:
            el = amb.find(tag)
            return (el.get("RawValue") or "").strip() if el is not None else ""

        out.append(
            {
                "name": txt("Name"),
                "key": txt("Key"),
                "value": txt("Value"),
                "valueRaw": raw("Value"),
                "min": txt("Min"),
                "max": txt("Max"),
                "units": txt("Units"),
                "type": txt("Type"),
                "inWarning": txt("InWarning"),
            }
        )
    return out


def _parse_bit_io_rows(resp_xml: str, outer_local: str, row_local: str) -> Tuple[List[Dict[str, Any]], str]:
    """解析 ``<Solenoids>`` / ``<Switches>`` 下 ``<Solenoid>`` / ``<Switch>`` 列表（Key/Name/Label/BitState）。"""
    s = (resp_xml or "").strip()
    if not s:
        return [], "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return [], f"XML 解析失败: {e}"

    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == outer_local:
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, outer_local)
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == outer_local:
                outer = el
                break
    if outer is None:
        return [], f"未找到 {outer_local} 根节点"

    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != row_local:
            continue
        key = _element_text(_first_child_by_local(ch, "Key")).strip()
        name = _element_text(_first_child_by_local(ch, "Name")).strip()
        label = _element_text(_first_child_by_local(ch, "Label")).strip()
        bit_raw = _element_text(_first_child_by_local(ch, "BitState")).strip()
        bit_l = bit_raw.lower()
        on = bit_l == "set"
        rows.append(
            {
                "key": key,
                "name": name or label or key,
                "label": label,
                "bitState": bit_raw,
                "on": on,
            }
        )
    return rows, ""


def _parse_maintenance_counters(resp_xml: str) -> Tuple[List[Dict[str, Any]], str]:
    """解析 ``<Counters>`` 维护计数器列表。"""
    s = (resp_xml or "").strip()
    if not s:
        return [], "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return [], f"XML 解析失败: {e}"
    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Counters":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "Counters")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Counters":
                outer = el
                break
    if outer is None:
        return [], "未找到 Counters 根节点"

    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Counter":
            continue

        def txt(local: str) -> str:
            return _element_text(_first_child_by_local(ch, local)).strip()

        excluded_raw = txt("Excluded").lower()
        expired_raw = txt("IsExpired").lower()
        rows.append(
            {
                "key": txt("Key"),
                "name": txt("Name"),
                "description": txt("Description"),
                "expiresIn": txt("ExpiresIn"),
                "lastModified": txt("LastModified"),
                "lastUsed": txt("LastUsed"),
                "excluded": excluded_raw in ("1", "true", "yes"),
                "isExpired": expired_raw in ("1", "true", "yes"),
            }
        )
    return rows, ""


def _parse_counter_detail(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    """解析单条 ``<Counter Key=\"…\"/>`` 应答（与 ``cornerstone-cli tcp counter --key`` 一致）。"""
    s = (resp_xml or "").strip()
    if not s:
        return {}, "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return {}, f"XML 解析失败: {e}"
    box: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Counter":
        box = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        box = _first_child_by_local(root, "Counter")
    if box is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Counter":
                box = el
                break
    if box is None:
        return {}, "未找到 Counter 根节点"
    key_el = _first_child_by_local(box, "Key")
    key = (key_el.text or "").strip() if key_el is not None else (box.attrib.get("Key") or "").strip()
    scalars: List[Dict[str, Any]] = []
    for ch in box:
        tag = _xml_local_tag(ch.tag)
        if tag == "Key":
            continue
        raw_val = (ch.text or "").strip()
        scalars.append(
            {
                "tag": tag,
                "label": (ch.attrib.get("Label") or ch.attrib.get("label") or "").strip(),
                "value": raw_val,
            }
        )
    return {"key": key, "scalars": scalars}, ""


def _transport_datetime_display(raw: str) -> str:
    """将仪器常见 ``M/D/YYYY HH:MM:SS`` 转为展示用 ``YYYY/M/D HH:mm:ss``；哨兵日期置空。"""
    s = (raw or "").strip()
    if not s:
        return ""
    if "0001" in s or s.startswith("01/01/0001"):
        return ""
    parts = s.replace("-", "/").split()
    if len(parts) >= 2 and "/" in parts[0]:
        mdys = parts[0].split("/")
        if len(mdys) == 3:
            try:
                m, d, y = int(mdys[0]), int(mdys[1]), int(mdys[2])
                return f"{y}/{m}/{d} {parts[1]}"
            except ValueError:
                pass
    return s


def _parse_transports_list(resp_xml: str) -> Tuple[List[Dict[str, Any]], str]:
    """解析 ``<Transports>`` 传送格式列表。"""
    s = (resp_xml or "").strip()
    if not s:
        return [], "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return [], f"XML 解析失败: {e}"
    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Transports":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "Transports")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Transports":
                outer = el
                break
    if outer is None:
        return [], "未找到 Transports 根节点"

    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Transport":
            continue

        def txt(local: str) -> str:
            return _element_text(_first_child_by_local(ch, local)).strip()

        excluded_raw = txt("Excluded").lower()
        lu_raw = txt("LastUsed")
        lm_raw = txt("LastModified")
        rows.append(
            {
                "key": txt("Key"),
                "name": txt("Name"),
                "description": txt("Description"),
                "lastUsed": _transport_datetime_display(lu_raw),
                "lastModified": _transport_datetime_display(lm_raw),
                "excluded": excluded_raw in ("1", "true", "yes"),
            }
        )
    return rows, ""


_TRANSPORT_FIELD_LIST_META: Dict[str, Tuple[str, str]] = {
    "SetBeginFields": ("setBeginFields", "Set 开始字段（SetBeginFields）"),
    "ReplicateFields": ("replicateFields", "重复试样字段（ReplicateFields）"),
    "SetEndFields": ("setEndFields", "Set 结束字段（SetEndFields）"),
}


def _parse_transport_detail(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    """解析单条 ``<Transport Key=\"…\"/>`` 应答为结构化 JSON（含字段列表分区）。"""
    s = (resp_xml or "").strip()
    if not s:
        return {}, "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return {}, f"XML 解析失败: {e}"
    box: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Transport":
        box = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        box = _first_child_by_local(root, "Transport")
    if box is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Transport":
                box = el
                break
    if box is None:
        return {}, "未找到 Transport 根节点"

    key_el = _first_child_by_local(box, "Key")
    key = (key_el.text or "").strip() if key_el is not None else (box.attrib.get("Key") or "").strip()

    scalars: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    for ch in box:
        tag = _xml_local_tag(ch.tag)
        if tag in _TRANSPORT_FIELD_LIST_META:
            sid, title = _TRANSPORT_FIELD_LIST_META[tag]
            field_rows: List[Dict[str, str]] = []
            for fe in ch:
                if _xml_local_tag(fe.tag) != "Field":
                    continue
                field_rows.append(
                    {
                        "name": (fe.attrib.get("Name") or fe.attrib.get("name") or "").strip(),
                        "label": (fe.attrib.get("Label") or fe.attrib.get("label") or "").strip(),
                    }
                )
            sections.append({"id": sid, "title": title, "fields": field_rows})
            continue
        if tag == "Key":
            continue
        val = (ch.text or "").strip()
        scalars.append(
            {
                "tag": tag,
                "label": (ch.attrib.get("Label") or ch.attrib.get("label") or "").strip(),
                "value": val,
            }
        )

    return {"key": key, "scalars": scalars, "sections": sections}, ""


_VALVE_STATE_NAME_ZH: Dict[str, str] = {
    "Unknown": "未知",
    "Gas Off": "气体关闭",
    "Gas Off Pedestal Down": "气体关闭（升降台下）",
    "Quick Purge Pedestal Up": "快速吹扫（升降台上）",
    "Quick Purge Pedestal Down": "快速吹扫（升降台下）",
    "Standby": "待机",
    "Standby Pedestal Down": "待机（升降台下）",
    "Analyze": "分析",
    "Pressure Check": "压力检查",
    "Pressure Check Pedestal Down": "压力检查（升降台下）",
    "Furnace Open 1": "炉子打开 1",
    "Furnace Open 2": "炉子打开 2",
    "Clean 1": "清洁 1",
    "Clean 2": "清洁 2",
    "Clean 2.5": "清洁 2.5",
    "Clean 3": "清洁 3",
    "Clean 4": "清洁 4",
    "Manual Clean": "手动清洁",
    "Furnace Close 1": "炉子关闭 1",
    "Furnace Close 2": "炉子关闭 2",
    "Furnace Close 3": "炉子关闭 3",
    "Pedestal Abort": "升降台中止",
    "Pedestal Abort Gas Off": "升降台中止（气体关闭）",
    "Depressurize Pedestal Down": "卸压（升降台下）",
    "Dose A Fill": "A 剂填充",
    "Dose A Equilibrate": "A 剂平衡",
    "Dose B Fill": "B 剂填充",
    "Dose B Equilibrate": "B 剂平衡",
    "Dose AB Fill": "AB 剂填充",
    "Dose AB Equilibrate": "AB 剂平衡",
    "Combustion Tube Maint 1": "燃烧管维护 1",
    "Combustion Tube Maint 2": "燃烧管维护 2",
    "Combustion Tube Maint 3": "燃烧管维护 3",
    "Leak Check - Equilibrate": "漏气检查 - 平衡",
    "Leak Check - System": "漏气检查 - 系统",
    "Leak Check - Segmented": "漏气检查 - 分段",
    "Leak Check - Pneumatic State 1": "漏气检查 - 气动状态 1",
    "Leak Check - Pneumatic State 1 Pressurize": "漏气检查 - 气动状态 1 加压",
    "Leak Check - Pneumatic State 2 Pressurize": "漏气检查 - 气动状态 2 加压",
    "Leak Check - Pneumatic State 2": "漏气检查 - 气动状态 2",
}


def _parse_valve_states(resp_xml: str) -> Tuple[List[Dict[str, Any]], str]:
    """解析 ``<ValveStates>`` 列表。"""
    s = (resp_xml or "").strip()
    if not s:
        return [], "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return [], f"XML 解析失败: {e}"
    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "ValveStates":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "ValveStates")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "ValveStates":
                outer = el
                break
    if outer is None:
        return [], "未找到 ValveStates 根节点"

    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != "ValveState":
            continue
        name = (ch.attrib.get("Name") or "").strip()
        active = (ch.attrib.get("Active") or "").strip().lower() in ("1", "true", "yes")
        rows.append(
            {
                "name": name,
                "displayName": _VALVE_STATE_NAME_ZH.get(name, name),
                "active": active,
            }
        )
    return rows, ""


def _solenoid_icon_kind(label: str, name: str = "") -> str:
    lb = (label or "").strip().lower()
    nm = (name or "").strip().lower()
    if "light" in nm:
        return "light"
    if lb == "vac":
        return "vac"
    if "pump" in lb:
        return "pump"
    if lb.startswith("fan") or "fan" in lb:
        return "fan"
    if lb == "plate":
        return "plate"
    if lb.startswith("sv"):
        return "valve"
    if "brush" in lb or "brush" in nm:
        return "brush"
    if "lance" in lb or "lance" in nm:
        return "lance"
    return "default"


def _switch_display_kind(name: str, label: str, on: bool) -> str:
    """输入侧指示灯：Set=亮（on），Unset=灭（off）；仅 Interlock 类开关在 Unset 时用三角警示（warn）。"""
    if on:
        return "on"
    n = (name or "").lower()
    lab = (label or "").strip().lower()
    if "interlock" in n or "interlock" in lab:
        return "warn"
    return "off"


def _queue_item_to_api_dict(p: PendingAddSamples) -> Dict[str, Any]:
    return {
        "id": p.entry_id,
        "receivedAt": p.received_at,
        "receivedAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.received_at)),
        "peer": p.source_peer,
        "sampleName": p.sample_name,
        "sampleDescription": p.sample_description,
        "xml": p.payload_xml,
    }


def _xml_local_tag(tag: Any) -> str:
    """ElementTree 命名空间下 tag 可能为 ``{uri}Local``，取 Local 部分。"""
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        _, _, rest = tag.partition("}")
        return rest or tag
    return tag


def _first_child_by_local(root: ET.Element, local: str) -> Optional[ET.Element]:
    for ch in root:
        if _xml_local_tag(ch.tag) == local:
            return ch
    return None


def _find_sets_container(root: ET.Element) -> ET.Element:
    """在应答根节点下定位 ``<Sets>``（支持默认命名空间、``CornerstoneMessage`` 包裹）。"""
    lt = _xml_local_tag(root.tag)
    if lt == "Sets":
        return root
    if lt == "CornerstoneMessage":
        inner = _first_child_by_local(root, "Sets")
        if inner is not None:
            return inner
    for el in root.iter():
        if _xml_local_tag(el.tag) == "Sets":
            return el
    return root


def _find_setreps_container(root: ET.Element) -> ET.Element:
    lt = _xml_local_tag(root.tag)
    if lt == "SetReps":
        return root
    if lt == "CornerstoneMessage":
        inner = _first_child_by_local(root, "SetReps")
        if inner is not None:
            return inner
    for el in root.iter():
        if _xml_local_tag(el.tag) == "SetReps":
            return el
    return root


def _element_text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _sets_list_parent(outer: ET.Element) -> ET.Element:
    """仪器常见：外层 ``<Sets>`` 内含元数据，真正的列表在子元素 ``<Sets>`` 下。"""
    inner = _first_child_by_local(outer, "Sets")
    if inner is not None:
        for ch in inner:
            if _xml_local_tag(ch.tag) == "Set":
                return inner
    return outer


def _parse_sets_analyte_defs(outer: ET.Element) -> List[Dict[str, str]]:
    """从外层 ``<Sets><Analytes><Analyte Label=\"…\">Carbon</Analyte>`` 提取元素列定义。"""
    out: List[Dict[str, str]] = []
    box = _first_child_by_local(outer, "Analytes")
    if box is None:
        return out
    for ch in box:
        if _xml_local_tag(ch.tag) != "Analyte":
            continue
        label = (ch.attrib.get("Label") or ch.attrib.get("label") or "").strip()
        key = (ch.text or "").strip()
        if not key:
            continue
        avg_rid = f"{key} Avg."
        out.append({"elementKey": key, "label": label or key, "avgRegistryId": avg_rid})
    return out


def _set_row_avg_for_analyte(fields: Dict[str, str], avg_registry_id: str, element_key: str) -> str:
    v = (fields.get(avg_registry_id) or "").strip()
    if v:
        return v
    for rid, val in fields.items():
        if rid.endswith(" Avg.") and element_key.lower() in rid.lower().replace(" ", ""):
            return (val or "").strip()
    return ""


def _parse_one_set_row(s: ET.Element, analyte_defs: List[Dict[str, str]]) -> Dict[str, Any]:
    """解析单个 ``<Set>``：支持 ``Key`` 属性、子元素 ``<Key>``/``<SetId>``，以及 ``<HeaderFields>`` 下的 ``<Field>``。"""
    key = (s.attrib.get("Key") or s.attrib.get("key") or "").strip()
    if not key:
        key = _element_text(_first_child_by_local(s, "Key"))
    if not key:
        key = _element_text(_first_child_by_local(s, "SetId"))

    fields: Dict[str, str] = {}
    hf = _first_child_by_local(s, "HeaderFields")
    field_nodes: List[ET.Element] = list(hf) if hf is not None else [ch for ch in s if _xml_local_tag(ch.tag) == "Field"]
    for f in field_nodes:
        if _xml_local_tag(f.tag) != "Field":
            continue
        fid = (f.attrib.get("Id") or f.attrib.get("id") or "").strip()
        reg = (f.attrib.get("RegistryId") or f.attrib.get("registryId") or "").strip()
        val = (f.text or "").strip()
        if not val:
            val = (f.attrib.get("RawValue") or f.attrib.get("rawValue") or "").strip()
        if fid:
            fields[fid] = val
        if reg:
            fields[reg] = val
        label = (f.attrib.get("Label") or f.attrib.get("label") or "").strip()
        if label and label not in fields:
            fields[label] = val

    sample_type = _element_text(_first_child_by_local(s, "SampleType"))
    if not sample_type:
        sample_type = fields.get("SampleType", "") or fields.get("1", "")

    state = _element_text(_first_child_by_local(s, "AnalysisState"))
    if not state:
        state = fields.get("AnalysisState") or fields.get("State") or fields.get("Status", "")

    completed = (
        fields.get("Set Analysis Date")
        or fields.get("4")
        or fields.get("WhenCompleted")
        or fields.get("DateCompleted")
        or fields.get("Completed", "")
    )

    name = (
        fields.get("Set Name")
        or fields.get("Name")
        or fields.get("2")
        or fields.get("SampleId")
        or fields.get("Description")
        or "—"
    )

    num_reps = _element_text(_first_child_by_local(s, "NumRepsInSet"))
    description = fields.get("Description") or fields.get("3") or ""
    method = fields.get("Method") or fields.get("0") or ""

    analyte_avgs: List[Dict[str, str]] = []
    for ad in analyte_defs:
        rid = ad.get("avgRegistryId") or ""
        ek = ad.get("elementKey") or ""
        analyte_avgs.append(
            {
                "elementKey": ek,
                "label": ad.get("label") or ek,
                "avgRegistryId": rid,
                "value": _set_row_avg_for_analyte(fields, rid, ek),
            }
        )

    return {
        "setKey": key,
        "fields": fields,
        "name": name,
        "numReps": num_reps,
        "description": description,
        "method": method,
        "analyteAvgs": analyte_avgs,
        "sampleType": sample_type,
        "state": state,
        "completed": completed,
    }


def _parse_sets_window_from_outer(outer: ET.Element) -> Dict[str, Optional[int]]:
    out: Dict[str, Optional[int]] = {"firstIndex": None, "lastIndex": None, "totalSamplesAvailable": None}
    for xml_tag, out_key in (
        ("FirstIndex", "firstIndex"),
        ("LastIndex", "lastIndex"),
        ("TotalSamplesAvailable", "totalSamplesAvailable"),
    ):
        el = _first_child_by_local(outer, xml_tag)
        if el is not None and (el.text or "").strip():
            with contextlib.suppress(ValueError):
                out[out_key] = int((el.text or "").strip())
    return out



def _parse_sets_response(resp_xml: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], Dict[str, Optional[int]]]:
    """解析 Sets 应答：行列表、Analytes 列定义、分页窗口。"""
    root = ET.fromstring(resp_xml)
    outer = _find_sets_container(root)
    analyte_defs = _parse_sets_analyte_defs(outer)
    list_parent = _sets_list_parent(outer)
    rows: List[Dict[str, Any]] = []
    for s in list_parent:
        if _xml_local_tag(s.tag) != "Set":
            continue
        rows.append(_parse_one_set_row(s, analyte_defs))
    win = _parse_sets_window_from_outer(outer)
    return rows, analyte_defs, win


_REP_COL_EXCLUDE = frozenset(
    {
        "Sample Mass",
        "Comments",
        "Operator",
        "Rep Analysis Date",
        "Method",
        "Description",
    }
)


def _rep_field_is_analyte_column(registry_id: str) -> bool:
    rid = (registry_id or "").strip()
    if not rid or rid in _REP_COL_EXCLUDE:
        return False
    if "Concentration" in rid or "浓度" in rid:
        return True
    return False


def _rep_analyte_columns_from_first_replicate(resp_xml: str) -> List[Dict[str, str]]:
    """从首条 ``<Replicate>`` 的 ``HeaderFields`` 顺序提取元素含量列（RegistryId 含 Concentration 等）。"""
    try:
        root = ET.fromstring(resp_xml)
    except ET.ParseError:
        return []
    container = _find_setreps_container(root)
    parent = _replicates_list_parent(container)
    first_rep: Optional[ET.Element] = None
    for ch in parent:
        if _xml_local_tag(ch.tag) == "Replicate":
            first_rep = ch
            break
    if first_rep is None:
        return []
    hf = _first_child_by_local(first_rep, "HeaderFields")
    if hf is None:
        return []
    cols: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for f in hf:
        if _xml_local_tag(f.tag) != "Field":
            continue
        reg = (f.attrib.get("RegistryId") or f.attrib.get("registryId") or "").strip()
        if not reg or reg in seen or not _rep_field_is_analyte_column(reg):
            continue
        seen.add(reg)
        label = (f.attrib.get("Label") or f.attrib.get("label") or "").strip() or reg
        units = (f.attrib.get("Units") or f.attrib.get("units") or "").strip()
        cols.append({"registryId": reg, "label": label, "units": units})
    return cols


def _float_from_instrument_field(s: str) -> Optional[float]:
    if s is None or not str(s).strip():
        return None
    t = str(s).strip().replace(",", ".")
    m = re.search(r"[-+]?(?:\d*\.\d+|\d+(?:\.\d+)?)(?:[eE][-+]?\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _sample_stddev(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    mean = sum(vals) / n
    var = sum((x - mean) ** 2 for x in vals) / (n - 1)
    return math.sqrt(var)


def _element_stats_for_replicates(
    replicates: List[Dict[str, Any]], column_defs: List[Dict[str, str]]
) -> List[Dict[str, Any]]:
    """仅 ``AnalysisState==Analyzed`` 的 Replicate；各元素含量：均值±1σ、n、RSD%。"""
    out: List[Dict[str, Any]] = []
    done = [r for r in replicates if (r.get("analysisState") or "").strip().lower() == "analyzed"]
    for col in column_defs:
        rid = col.get("registryId") or ""
        vals: List[float] = []
        for r in done:
            raw = (r.get("fields") or {}).get(rid, "")
            fv = _float_from_instrument_field(raw)
            if fv is not None:
                vals.append(fv)
        n = len(vals)
        if n == 0:
            out.append(
                {
                    "registryId": rid,
                    "label": col.get("label") or rid,
                    "units": col.get("units") or "",
                    "n": 0,
                    "mean": None,
                    "std": None,
                    "meanPlusMinusSigma": None,
                    "rsdPercent": None,
                }
            )
            continue
        mean = sum(vals) / n
        std = _sample_stddev(vals) if n > 1 else 0.0
        rsd = (100.0 * std / mean) if mean != 0 else None
        pm = f"{mean:g} ± {std:g}" if n > 1 else f"{mean:g}"
        out.append(
            {
                "registryId": rid,
                "label": col.get("label") or rid,
                "units": col.get("units") or "",
                "n": n,
                "mean": mean,
                "std": std,
                "meanPlusMinusSigma": pm,
                "rsdPercent": rsd,
            }
        )
    return out


def _child_float_optional(parent: ET.Element, local: str) -> Optional[float]:
    el = _first_child_by_local(parent, local)
    if el is None or not (el.text or "").strip():
        return None
    with contextlib.suppress(ValueError, TypeError):
        return float((el.text or "").strip().replace(",", "."))
    return None


# RepPlot ``TracePoint@DateTime`` 与 ``Trace`` 的 ``XMin``/``XMax`` 为同一刻度；除以 1e7 为横轴秒。
_REP_PLOT_TRACE_DATETIME_TO_SECONDS = 10_000_000.0


def _parse_rep_plot_analyte_series(resp_xml: str) -> List[Dict[str, Any]]:
    """
    解析仪器 RepPlot 常见结构::

        <RepPlot><Plot><Replicate><Analyte Label=\"Carbon\" ...>
          <Trace><YMin/><YMax/><TracePoints>
            <TracePoint DateTime=\"...\">检测器强度 y</TracePoint>

    横轴 x：``DateTime`` 数值除以 :data:`_REP_PLOT_TRACE_DATETIME_TO_SECONDS` 得到秒。
    纵轴 y：元素文本为检测器强度。
    """
    out: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(resp_xml)
    except ET.ParseError:
        return out
    plot_el: Optional[ET.Element] = None
    plot_el = _first_child_by_local(root, "Plot")
    if plot_el is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Plot":
                plot_el = el
                break
    if plot_el is None:
        return out
    rep_el = _first_child_by_local(plot_el, "Replicate")
    if rep_el is None:
        for ch in plot_el:
            if _xml_local_tag(ch.tag) == "Replicate":
                rep_el = ch
                break
    if rep_el is None:
        rep_el = plot_el
    for ch in rep_el:
        if _xml_local_tag(ch.tag) != "Analyte":
            continue
        label = (ch.attrib.get("Label") or ch.attrib.get("label") or "").strip()
        key = label or "Analyte"
        val = (ch.attrib.get("Value") or ch.attrib.get("value") or "").strip()
        units = (ch.attrib.get("Units") or ch.attrib.get("units") or "").strip()
        trace = _first_child_by_local(ch, "Trace")
        if trace is None:
            continue
        y_min = _child_float_optional(trace, "YMin")
        y_max = _child_float_optional(trace, "YMax")
        tps = _first_child_by_local(trace, "TracePoints")
        pts: List[List[float]] = []
        dt_scale = _REP_PLOT_TRACE_DATETIME_TO_SECONDS
        if tps is not None:
            for tp in tps:
                if _xml_local_tag(tp.tag) != "TracePoint":
                    continue
                dt_s = (tp.attrib.get("DateTime") or tp.attrib.get("datetime") or "0").strip()
                y_txt = (tp.text or "").strip()
                with contextlib.suppress(ValueError, TypeError):
                    pts.append([float(dt_s) / dt_scale, float(y_txt)])
        if len(pts) < 2:
            continue
        conc_rid = f"{key} Concentration" if key else ""
        b: Dict[str, Any] = {}
        # 横轴仅用 ``TracePoint@DateTime``/1e7（秒）；``Trace`` 的 ``XMin``/``XMax`` 常与点时间戳刻度不一致，勿写入 bounds 以免压扁曲线。
        if y_min is not None:
            b["yMin"] = y_min
        if y_max is not None:
            b["yMax"] = y_max
        out.append(
            {
                "analyteKey": key,
                "label": label or key,
                "value": val,
                "units": units,
                "concentrationRegistryId": conc_rid,
                "points": pts,
                "bounds": b,
            }
        )
    return out


def _parse_rep_detail_fields(resp_xml: str) -> Dict[str, Any]:
    """解析 ``<RepDetail>`` 下 ``<Replicate><DetailFields><Field>``。"""
    empty: Dict[str, Any] = {
        "ok": False,
        "error": "",
        "errorCode": "",
        "errorMessage": "",
        "tag": "",
        "detailFields": [],
    }
    try:
        root = ET.fromstring(resp_xml)
    except ET.ParseError as ex:
        out = dict(empty)
        out["error"] = f"解析 RepDetail XML 失败: {ex}"
        return out
    rep_el: Optional[ET.Element] = None
    if _xml_local_tag(root.tag).lower() == "repdetail":
        rep_el = _first_child_by_local(root, "Replicate")
    if rep_el is None:
        for el in root.iter():
            if _xml_local_tag(el.tag).lower() == "repdetail":
                rep_el = _first_child_by_local(el, "Replicate")
                root = el
                break
    if rep_el is None:
        out = dict(empty)
        out["error"] = "应答中无 RepDetail/Replicate"
        return out
    ec = (root.attrib.get("ErrorCode") or root.attrib.get("errorCode") or "").strip()
    em = (root.attrib.get("ErrorMessage") or root.attrib.get("errorMessage") or "").strip()
    tag_txt = (rep_el.attrib.get("Tag") or rep_el.attrib.get("tag") or "").strip()
    if not tag_txt:
        tag_txt = (_element_text(_first_child_by_local(rep_el, "Tag")) or "").strip()
    fields: List[Dict[str, Any]] = []
    df = _first_child_by_local(rep_el, "DetailFields")
    if df is not None:
        for f in df:
            if _xml_local_tag(f.tag) != "Field":
                continue
            fields.append(
                {
                    "label": (f.attrib.get("Label") or f.attrib.get("label") or "").strip(),
                    "registryId": (f.attrib.get("RegistryId") or f.attrib.get("registryId") or "").strip(),
                    "id": (f.attrib.get("Id") or f.attrib.get("id") or "").strip(),
                    "units": (f.attrib.get("Units") or f.attrib.get("units") or "").strip(),
                    "rawValue": (f.attrib.get("RawValue") or f.attrib.get("rawValue") or "").strip(),
                    "valueStatus": (f.attrib.get("ValueStatus") or f.attrib.get("valueStatus") or "").strip(),
                    "value": (f.text or "").strip(),
                }
            )
    ok_instr = ec in ("", "0") or ec.lower() in ("success", "ok")
    out = {
        "ok": ok_instr,
        "error": "" if ok_instr else (em or f"仪器 ErrorCode={ec!r}"),
        "errorCode": ec,
        "errorMessage": em,
        "tag": tag_txt,
        "detailFields": fields,
    }
    return out


def _parse_status_widgets(resp_xml: str) -> List[Dict[str, Any]]:
    """解析 ``<Status>`` 下 ``<Widgets><Widget>``（仅仪表小部件）。"""
    out: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(resp_xml)
    except ET.ParseError:
        return out
    for el in root.iter():
        if _xml_local_tag(el.tag) != "Widgets":
            continue
        for ch in el:
            if _xml_local_tag(ch.tag) != "Widget":
                continue
            warn_raw = (ch.attrib.get("Warning") or ch.attrib.get("warning") or "").strip().lower()
            out.append(
                {
                    "id": (ch.attrib.get("Id") or ch.attrib.get("id") or "").strip(),
                    "label": (ch.attrib.get("Label") or ch.attrib.get("label") or "").strip(),
                    "units": (ch.attrib.get("Units") or ch.attrib.get("units") or "").strip(),
                    "warning": warn_raw in ("true", "1", "yes"),
                    "value": (ch.text or "").strip(),
                }
            )
        break
    return out


def _find_status_element(resp_xml: str) -> Optional[ET.Element]:
    s = (resp_xml or "").strip()
    if not s.startswith("<"):
        return None
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return None
    if _xml_local_tag(root.tag) == "Status":
        return root
    if _xml_local_tag(root.tag) == "CornerstoneMessage":
        inner = _first_child_by_local(root, "Status")
        if inner is not None:
            return inner
    for el in root.iter():
        if _xml_local_tag(el.tag) == "Status":
            return el
    return None


def _format_status_display_time(iso_s: str) -> str:
    """将 ``ExecutionDate`` 等 ISO 时间格式化为 ``YYYY/M/D HH:mm``（展示用，本地时区）。"""
    s = (iso_s or "").strip()
    if not s:
        return ""
    try:
        from datetime import datetime, timezone

        ends_z = s.endswith("Z")
        if ends_z:
            s = s[:-1]
        if "." in s and "T" in s:
            pre, post = s.split(".", 1)
            j = 0
            while j < len(post) and post[j].isdigit():
                j += 1
            fd = post[:j]
            tail = post[j:]
            if fd:
                fd = (fd + "000000")[:6]
                s = pre + "." + fd + tail
        if ends_z and not re.search(r"[+-]\d{2}:\d{2}$", s):
            s = s + "+00:00"

        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return f"{dt.year}/{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return (iso_s or "").strip().replace("T", " ")[:19]


def _humanize_camel_tag(tag: str) -> str:
    t = (tag or "").strip()
    if not t:
        return ""
    parts: List[str] = []
    buf = ""
    for i, c in enumerate(t):
        if c.isupper() and buf and (buf[-1].islower() or (i + 1 < len(t) and t[i + 1].islower())):
            parts.append(buf)
            buf = c
        else:
            buf += c
    if buf:
        parts.append(buf)
    return " ".join(parts)


_LEAK_FIELD_LABEL_ZH: Dict[str, str] = {
    "InitialPressure": "初始压力",
    "CurrentPressure": "当前压力",
    "PressureChange": "压力变化",
    "Result": "结果",
    "LeakRate": "泄漏率",
    "TargetPressure": "目标压力",
    "StartPressure": "起始压力",
    "EndPressure": "结束压力",
}


def _leak_field_label(tag_local: str) -> str:
    return _LEAK_FIELD_LABEL_ZH.get(tag_local, _humanize_camel_tag(tag_local))


def _parse_status_elements_list(status_el: ET.Element) -> List[Dict[str, str]]:
    box = _first_child_by_local(status_el, "Elements")
    if box is None:
        return []
    rows: List[Dict[str, str]] = []
    for ch in box:
        key = _xml_local_tag(ch.tag)
        if not key:
            continue
        rows.append({"key": key, "value": (ch.text or "").strip()})
    return rows


def _parse_status_odometers_list(status_el: ET.Element) -> List[Dict[str, str]]:
    box = _first_child_by_local(status_el, "Odometers")
    if box is None:
        return []
    rows: List[Dict[str, str]] = []
    for ch in box:
        if _xml_local_tag(ch.tag) != "Odometer":
            continue
        ot = (ch.attrib.get("Type") or ch.attrib.get("type") or "").strip()
        rows.append({"type": ot, "value": (ch.text or "").strip()})
    return rows


def _parse_status_system_check(status_el: ET.Element) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "executionDate": "",
        "executionDateText": "",
        "items": [],
        "passed": 0,
        "failed": 0,
        "total": 0,
        "executed": 0,
    }
    scr = _first_child_by_local(status_el, "SystemCheckResults")
    if scr is None:
        return out
    sc = _first_child_by_local(scr, "SystemCheck")
    if sc is None:
        return out
    res = _first_child_by_local(sc, "Result")
    if res is None:
        return out
    out["executionDate"] = (res.attrib.get("ExecutionDate") or res.attrib.get("executionDate") or "").strip()
    out["executionDateText"] = _format_status_display_time(out["executionDate"])
    for ch in res:
        if _xml_local_tag(ch.tag) != "Item":
            continue
        st = (ch.attrib.get("Status") or ch.attrib.get("status") or "").strip()
        st_l = st.lower()
        out["items"].append(
            {
                "id": (ch.attrib.get("Id") or ch.attrib.get("id") or "").strip(),
                "label": (ch.attrib.get("Label") or ch.attrib.get("label") or "").strip(),
                "status": st,
            }
        )
        if st_l == "passed":
            out["passed"] += 1
        elif st_l == "failed":
            out["failed"] += 1
    n = len(out["items"])
    out["total"] = n
    out["executed"] = n
    return out


_LEAK_SEGMENT_HEADER_CLASS = ("leak-h-blue", "leak-h-red", "leak-h-green")


def _parse_status_leak_checks(status_el: ET.Element) -> List[Dict[str, Any]]:
    lcr = _first_child_by_local(status_el, "LeakCheckResults")
    if lcr is None:
        return []
    checks: List[Dict[str, Any]] = []
    for lc in lcr:
        if _xml_local_tag(lc.tag) != "LeakCheck":
            continue
        cid = (lc.attrib.get("Id") or lc.attrib.get("id") or "").strip()
        lab = (lc.attrib.get("Label") or lc.attrib.get("label") or "").strip()
        res = _first_child_by_local(lc, "Result")
        ed = ""
        summary = ""
        segments: List[Dict[str, Any]] = []
        if res is not None:
            ed = (res.attrib.get("ExecutionDate") or res.attrib.get("executionDate") or "").strip()
            summary = (res.attrib.get("Summary") or res.attrib.get("Message") or res.attrib.get("Note") or "").strip()
            seg_i = 0
            for seg_el in res:
                slt = _xml_local_tag(seg_el.tag)
                if slt in ("Segment", "Section", "Zone", "Part"):
                    title = (
                        (seg_el.attrib.get("Name") or seg_el.attrib.get("Label") or seg_el.attrib.get("Title") or "")
                        .strip()
                        or slt
                    )
                    rows: List[Dict[str, str]] = []
                    for fe in seg_el:
                        fl = _xml_local_tag(fe.tag)
                        rl = (fe.attrib.get("Label") or "").strip() or _leak_field_label(fl)
                        val = (fe.text or "").strip()
                        if not val:
                            val = (fe.attrib.get("Value") or fe.attrib.get("Text") or "").strip()
                        rows.append({"label": rl, "value": val})
                    segments.append(
                        {
                            "title": title,
                            "headerClass": _LEAK_SEGMENT_HEADER_CLASS[seg_i % len(_LEAK_SEGMENT_HEADER_CLASS)],
                            "rows": rows,
                        }
                    )
                    seg_i += 1
            if not segments:
                loose: List[Dict[str, str]] = []
                for fe in res:
                    flt = _xml_local_tag(fe.tag)
                    if flt.lower() in ("summary", "message", "note"):
                        if not summary:
                            summary = (fe.text or "").strip()
                        continue
                    val = (fe.text or "").strip()
                    if not val:
                        val = (fe.attrib.get("Value") or fe.attrib.get("Text") or "").strip()
                    if not val and not fe.attrib:
                        continue
                    loose.append({"label": _leak_field_label(flt), "value": val})
                if loose:
                    segments.append(
                        {
                            "title": lab or cid or "漏气检查",
                            "headerClass": _LEAK_SEGMENT_HEADER_CLASS[0],
                            "rows": loose,
                        }
                    )
        checks.append(
            {
                "id": cid,
                "label": lab,
                "executionDate": ed,
                "executionDateText": _format_status_display_time(ed),
                "summary": summary,
                "segments": segments,
            }
        )
    return checks


def _status_check_payload_from_xml(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    st = _find_status_element(resp_xml)
    if st is None:
        return {}, "未找到 Status 应答节点"
    return {
        "elements": _parse_status_elements_list(st),
        "odometers": _parse_status_odometers_list(st),
        "systemCheck": _parse_status_system_check(st),
        "leakChecks": _parse_status_leak_checks(st),
    }, ""


def _parse_rep_plot_series(resp_xml: str) -> List[Dict[str, Any]]:
    """
    旧版 RepPlot 回退：``<Curve>``/``<Point X Y/>`` 等；若存在 ``<Analyte><TracePoint>`` 结构则优先用
    :func:`_parse_rep_plot_analyte_series`（由调用方合并）。
    """
    series: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(resp_xml)
    except ET.ParseError:
        return series

    def points_from_curve_container(container: ET.Element) -> Optional[List[List[float]]]:
        pts: List[List[float]] = []
        for el in container.iter():
            if el is container:
                continue
            tag = _xml_local_tag(el.tag)
            if tag.lower() not in ("point", "datapoint", "pt", "xy"):
                continue
            xs = el.attrib.get("X") or el.attrib.get("x") or el.attrib.get("Index")
            ys = el.attrib.get("Y") or el.attrib.get("y") or el.attrib.get("Value")
            if xs is None or ys is None:
                continue
            with contextlib.suppress(ValueError, TypeError):
                pts.append([float(xs), float(ys)])
        if len(pts) >= 2:
            return pts
        return None

    for ch in root:
        t = _xml_local_tag(ch.tag).lower()
        if t in ("curve", "trace", "line", "series", "plotcurve"):
            pts = points_from_curve_container(ch)
            if pts:
                nm = (ch.attrib.get("Name") or ch.attrib.get("Color") or ch.tag or "curve").strip()
                series.append({"name": nm, "points": pts})

    if not series:
        pts_all: List[List[float]] = []
        for el in root.iter():
            tag = _xml_local_tag(el.tag)
            if tag.lower() not in ("point", "datapoint", "pt"):
                continue
            xs = el.attrib.get("X") or el.attrib.get("x") or el.attrib.get("Index")
            ys = el.attrib.get("Y") or el.attrib.get("y") or el.attrib.get("Value")
            if xs is None or ys is None:
                continue
            with contextlib.suppress(ValueError, TypeError):
                pts_all.append([float(xs), float(ys)])
        if len(pts_all) >= 2:
            series.append({"name": "RepPlot", "points": pts_all})

    return series


def _sets_pagination(window: Dict[str, Optional[int]], number: int) -> Dict[str, Any]:
    fi = window.get("firstIndex")
    li = window.get("lastIndex")
    tot = window.get("totalSamplesAvailable")
    pag: Dict[str, Any] = {
        "firstIndex": fi,
        "lastIndex": li,
        "totalSamplesAvailable": tot,
        "nextOlderStartAt": None,
        "prevNewerStartAt": None,
    }
    if fi is not None and number > 0 and fi > 0:
        pag["nextOlderStartAt"] = max(0, int(fi) - int(number))
    if li is not None and tot is not None and int(li) < int(tot) - 1:
        pag["prevNewerStartAt"] = int(li) + 1
    return pag


def _replicates_list_parent(container: ET.Element) -> ET.Element:
    """``<SetReps>`` 下常见 ``<Replicates>`` 包裹 ``<Replicate>``。"""
    repl = _first_child_by_local(container, "Replicates")
    if repl is not None:
        for ch in repl:
            if _xml_local_tag(ch.tag) == "Replicate":
                return repl
    return container


def _header_field_value_status(hf: Optional[ET.Element], registry_id: str) -> str:
    if hf is None or not registry_id:
        return ""
    for f in hf:
        if _xml_local_tag(f.tag) != "Field":
            continue
        reg = (f.attrib.get("RegistryId") or f.attrib.get("registryId") or "").strip()
        if reg == registry_id:
            return (f.attrib.get("ValueStatus") or f.attrib.get("valueStatus") or "").strip()
    return ""


def _parse_one_replicate_row(r: ET.Element) -> Dict[str, Any]:
    """解析单个 ``<Replicate>``：``<Tag>`` 子元素、``<HeaderFields>`` 内 ``<Field>``。"""
    tag = (r.attrib.get("Tag") or r.attrib.get("tag") or "").strip()
    if not tag:
        tag = _element_text(_first_child_by_local(r, "Tag"))

    fields: Dict[str, str] = {}
    hf = _first_child_by_local(r, "HeaderFields")
    field_nodes: List[ET.Element] = list(hf) if hf is not None else [ch for ch in r if _xml_local_tag(ch.tag) == "Field"]
    for f in field_nodes:
        if _xml_local_tag(f.tag) != "Field":
            continue
        fid = (f.attrib.get("Id") or f.attrib.get("id") or "").strip()
        reg = (f.attrib.get("RegistryId") or f.attrib.get("registryId") or "").strip()
        val = (f.text or "").strip()
        if not val:
            val = (f.attrib.get("RawValue") or f.attrib.get("rawValue") or "").strip()
        if fid:
            fields[fid] = val
        if reg:
            fields[reg] = val
        label = (f.attrib.get("Label") or f.attrib.get("label") or "").strip()
        if label and label not in fields:
            fields[label] = val

    mass = fields.get("Sample Mass") or fields.get("11") or ""
    comments = fields.get("Comments") or fields.get("12") or ""
    carbon = fields.get("Carbon Concentration") or fields.get("Carbon") or fields.get("102") or ""
    sulfur = fields.get("Sulfur Concentration") or fields.get("Sulfur") or fields.get("137") or ""
    analysis_date = fields.get("Rep Analysis Date") or fields.get("14") or ""

    st_c = _header_field_value_status(hf, "Carbon Concentration")
    st_s = _header_field_value_status(hf, "Sulfur Concentration")
    st_m = _header_field_value_status(hf, "Sample Mass")
    quality_parts = [p for p in (st_c, st_s, st_m) if p]
    quality = "/".join(dict.fromkeys(quality_parts)) if quality_parts else ""

    analysis_state = _element_text(_first_child_by_local(r, "AnalysisState"))

    return {
        "tag": tag,
        "fields": fields,
        "mass": mass,
        "comments": comments,
        "carbon": carbon,
        "sulfur": sulfur,
        "analysisDate": analysis_date,
        "quality": quality,
        "analysisState": analysis_state,
    }


def _parse_set_reps_replicates(resp_xml: str) -> List[Dict[str, Any]]:
    """解析 ``<SetReps>`` 下各 ``<Replicate>``（``<Replicates>`` 包裹、``<Tag>`` 子元素、``HeaderFields``）。"""
    root = ET.fromstring(resp_xml)
    container = _find_setreps_container(root)
    parent = _replicates_list_parent(container)
    reps: List[Dict[str, Any]] = []
    for r in parent:
        if _xml_local_tag(r.tag) != "Replicate":
            continue
        reps.append(_parse_one_replicate_row(r))
    return reps


def _build_sets_query_xml(filter_key: str, number: int, start_at: int) -> str:
    """构造 ``<Sets/>`` 请求；始终带 ``FilterKey``（可为空串），与仪器常见约定一致。"""
    fk = _xml_escape(filter_key or "")
    return f'<Sets FilterKey="{fk}" Number="{int(number)}" StartAt="{int(start_at)}"/>'


def _aggregate_replicate_field_stats(replicates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对多条 Replicate 的数值型 Field 做简单 min/max/mean。"""
    from collections import defaultdict

    nums: Dict[str, List[float]] = defaultdict(list)
    for rep in replicates:
        for k, v in rep.get("fields", {}).items():
            if v is None or v == "":
                continue
            try:
                nums[k].append(float(v.replace(",", ".")))
            except ValueError:
                continue
    field_stats: Dict[str, Dict[str, Union[int, float]]] = {}
    for k, arr in nums.items():
        if not arr:
            continue
        field_stats[k] = {
            "n": len(arr),
            "min": min(arr),
            "max": max(arr),
            "mean": sum(arr) / len(arr),
        }
    return {"replicateCount": len(replicates), "fieldStats": field_stats}


def _extract_embedded_image_from_xml(resp_xml: str) -> Tuple[Optional[str], Optional[str]]:
    """从 RepPlot 等应答中尝试取出内嵌 base64 图片 (mime, base64)。"""
    try:
        root = ET.fromstring(resp_xml)
    except ET.ParseError:
        root = None
    if root is not None:
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag.lower() in (
                "imagedata",
                "binarydata",
                "plotdata",
                "pngdata",
                "bitmap",
                "image",
            ):
                t = (el.text or "").strip().replace("\r", "").replace("\n", "").replace(" ", "")
                if len(t) > 300:
                    mime = (el.attrib.get("Format") or el.attrib.get("format") or "image/png").strip()
                    if "/" not in mime:
                        mime = "image/png"
                    return mime, t
    m = re.search(r">([A-Za-z0-9+/=\s]{800,})<", resp_xml)
    if m:
        t = re.sub(r"\s+", "", m.group(1))
        if len(t) > 500:
            return "image/png", t
    return None, None


_STATIC_DIR = Path(__file__).resolve().parent / "mock_web_static"


@dataclass
class PendingAddSamples:
    entry_id: str
    received_at: float
    source_peer: str
    payload_xml: str
    sample_name: str = ""
    sample_description: str = ""


@dataclass
class _FutureWaiter:
    """上游应答按 Cookie 路由时，网页触发的请求用 Future 收文，不写回 TCP。"""

    fut: asyncio.Future[str]


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
    a = (peer_host or "").strip().lower()
    b = (privileged or "").strip().lower()
    return bool(a) and bool(b) and a == b


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

        self._pending_add_samples: deque[PendingAddSamples] = deque(maxlen=self._add_samples_max)

        self._upstream_reader_task: Optional[asyncio.Task[None]] = None
        self._upstream_heartbeat_task: Optional[asyncio.Task[None]] = None
        self._upstream_reconnect_task: Optional[asyncio.Task[None]] = None
        self._instrument_sidecar_lock = asyncio.Lock()

        self._tcp_listen_host = (tcp_listen_host or "").strip()
        self._tcp_listen_port = int(tcp_listen_port)
        self._web_listen_host = (web_listen_host or "").strip()
        self._web_listen_port = int(web_listen_port)
        self._config_file_path: Optional[Path] = (
            Path(config_file_path).expanduser().resolve() if config_file_path else None
        )
        self._last_upstream_heartbeat_reply_at = 0.0

        self._rcs_lock = asyncio.Lock()
        self._remote_control_display: str = "—"
        self._remote_control_active: bool = False
        self._remote_control_last_err: str = ""

    def pending_snapshot(self) -> List[PendingAddSamples]:
        return list(self._pending_add_samples)

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
        return selected

    def set_add_samples_queue_max(self, n: int) -> None:
        n = max(1, min(int(n), 50_000))
        items: List[PendingAddSamples] = list(self._pending_add_samples)
        while len(items) > n:
            items.pop(0)
        self._add_samples_max = n
        self._pending_add_samples = deque(items, maxlen=n)

    async def _force_close_upstream(self) -> None:
        await self._stop_upstream_heartbeat()
        rct = self._upstream_reconnect_task
        if rct is not None and not rct.done():
            rct.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rct
        self._upstream_reconnect_task = None
        t = self._upstream_reader_task
        self._upstream_reader_task = None
        async with self._upstream_connect_lock:
            uw = self._upstream_writer
            if uw is not None and not uw.is_closing():
                uw.close()
        if t is not None and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        async with self._upstream_connect_lock:
            self._upstream_reader = None
            self._upstream_writer = None
        self._logon_seen_upstream_success = False
        self._upstream_session_authenticated = False
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

    async def _ensure_upstream(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        created_new = False
        async with self._upstream_connect_lock:
            if self._upstream_writer is not None and not self._upstream_writer.is_closing():
                assert self._upstream_reader is not None
                return self._upstream_reader, self._upstream_writer
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
        while self._upstream_reader is not None:
            try:
                header = await self._upstream_reader.readexactly(4)
            except (asyncio.IncompleteReadError, asyncio.CancelledError):
                break
            (length,) = struct.unpack("<I", header)
            if length == 0:
                continue
            try:
                payload_bytes = await self._upstream_reader.readexactly(length)
            except asyncio.IncompleteReadError:
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

        print("[gateway] upstream read loop ended")
        await self._stop_upstream_heartbeat()
        async with self._upstream_connect_lock:
            self._upstream_reader = None
            self._upstream_writer = None
        self._logon_seen_upstream_success = False
        self._upstream_session_authenticated = False
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


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    hub: GatewayHub,
    async_message_interval: float,
) -> None:
    peer = writer.get_extra_info("peername")
    peer_s = str(peer)
    print(f"[gateway] client connected: {peer_s}")

    async_task: Optional[asyncio.Task[None]] = None

    async def push_async_messages() -> None:
        n = 0
        while True:
            await asyncio.sleep(async_message_interval)
            n += 1
            msg = f"<CornerstoneMessage><Text>Hello {n}</Text></CornerstoneMessage>"
            writer.write(_frame(msg, hub.encoding))
            await writer.drain()

    if async_message_interval > 0:
        async_task = asyncio.create_task(push_async_messages(), name="gateway_async_messages")

    enc = hub.encoding
    try:
        while True:
            header = await reader.readexactly(4)
            (length,) = struct.unpack("<I", header)
            if length == 0:
                continue
            payload_bytes = await reader.readexactly(length)
            text = payload_bytes.decode(enc, errors="replace")
            print(f"[gateway] client IN: {text[:400]}{'...' if len(text) > 400 else ''}")

            tag = _root_tag(text)
            cookie = _parse_cookie_from_payload(text)

            if tag == "Logon":
                if hub._synthetic_logon_after_first and hub._logon_seen_upstream_success:
                    resp = _synthetic_logon_success(cookie)
                    print(f"[gateway] synthetic Logon for {peer_s}")
                    writer.write(_frame(resp, enc))
                    await writer.drain()
                    continue
                await hub.forward_client_frame(text, writer)
                continue

            if tag == "AddSamples":
                peer_host = _peer_host_from_peername(peer)
                direct_upstream = _peer_host_matches_privileged(
                    peer_host, hub._privileged_add_samples_host
                )
                if direct_upstream:
                    print(
                        f"[gateway] AddSamples direct upstream (privileged host) "
                        f"peer={peer_s} host={peer_host!r}"
                    )
                    await hub.forward_client_frame(text, writer)
                    continue
                s_name, s_desc = _add_samples_name_description(text)
                hub._pending_add_samples.append(
                    PendingAddSamples(
                        entry_id=secrets.token_hex(8),
                        received_at=time.time(),
                        source_peer=peer_s,
                        payload_xml=text,
                        sample_name=s_name,
                        sample_description=s_desc,
                    )
                )
                resp = _synthetic_add_samples_held(cookie)
                print(f"[gateway] AddSamples held -> queue size={len(hub._pending_add_samples)}")
                writer.write(_frame(resp, enc))
                await writer.drain()
                continue

            await hub.forward_client_frame(text, writer)
    except asyncio.IncompleteReadError:
        pass
    finally:
        if async_task is not None:
            async_task.cancel()
            with contextlib.suppress(Exception):
                await async_task
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        print(f"[gateway] client disconnected: {peer_s}")


def _legacy_queue_html_page(hub: GatewayHub) -> bytes:
    rows = []
    for p in hub.pending_snapshot():
        full_xml = html.escape(p.payload_xml)
        name = html.escape(p.sample_name)
        desc = html.escape(p.sample_description)
        rows.append(
            f"<tr><td><input type=\"checkbox\" name=\"id\" value=\"{html.escape(p.entry_id)}\"/></td>"
            f"<td><code>{html.escape(p.entry_id)}</code></td>"
            f"<td>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.received_at))}</td>"
            f"<td>{html.escape(p.source_peer)}</td>"
            f"<td>{name}</td>"
            f"<td>{desc}</td>"
            f"<td><details><summary>展开 XML</summary><pre>{full_xml}</pre></details></td></tr>"
        )
    body_rows = "\n".join(rows) if rows else "<tr><td colspan=\"7\">（队列为空）</td></tr>"
    web_login_hint = (
        "已配置网页登录凭据（发往仪器前会自动 Logon）。"
        if (hub.web_user and hub.web_password)
        else "<strong>未配置 --web-user / --web-password</strong>：点击「发送」将无法登录上游，请先配置后重启网关。"
    )
    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><title>AddSamples 队列</title>
<style>body{{font-family:sans-serif;max-width:1400px;margin:1rem auto;}} table{{border-collapse:collapse;width:100%;}} th,td{{border:1px solid #ccc;padding:6px;vertical-align:top;}} pre{{white-space:pre-wrap;word-break:break-all;font-size:12px;max-height:50vh;overflow:auto;}} details summary{{cursor:pointer;color:#06c;}}</style>
</head><body>
<h1>AddSamples 截留队列（最多 {hub._add_samples_max} 条）</h1>
<p>{web_login_hint}</p>
<p>勾选后点击下方按钮，将选中条目<strong>依次</strong>发往上游 Cornerstone（发送前会自动对上游执行 Logon，需配置 <code>--web-user</code> / <code>--web-password</code>）。</p>
<form method="post" action="/send">
<table>
<thead><tr><th>选</th><th>ID</th><th>时间</th><th>来源</th><th>样品名称</th><th>样品说明</th><th>XML 全文</th></tr></thead>
<tbody>{body_rows}</tbody>
</table>
<p><button type="submit">发送选中到 Cornerstone</button></p>
</form>
<p><a href="/">新版网页</a> · <a href="/legacy">刷新本页</a></p>
</body></html>"""
    return page.encode("utf-8")


async def _http_send(
    writer: asyncio.StreamWriter,
    status: int,
    body: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    reason = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        500: "Internal Server Error",
    }.get(status, "OK")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    )
    writer.write(head.encode("latin-1", errors="replace") + body)
    await writer.drain()


def _hub_settings_public_dict(hub: GatewayHub) -> Dict[str, Any]:
    """网页「网关配置」表单用字段（不含密码明文）。"""
    return {
        "tcpListenHost": hub._tcp_listen_host,
        "tcpListenPort": hub._tcp_listen_port,
        "webListenHost": hub._web_listen_host,
        "webListenPort": hub._web_listen_port,
        "upstreamHost": hub._upstream_host,
        "upstreamPort": hub._upstream_port,
        "webUser": hub.web_user,
        "webPasswordSet": bool(hub.web_password),
        "privilegedAddSamplesHost": hub._privileged_add_samples_host,
        "queueMax": hub._add_samples_max,
        "encoding": hub.encoding,
        "configFile": str(hub._config_file_path) if hub._config_file_path else "",
    }


def _persist_hub_settings_to_config(hub: GatewayHub) -> Tuple[bool, str]:
    """将当前 Hub 状态合并写入 ``-c`` 指定的 JSON（保留文件中其它键）。"""
    if hub._config_file_path is None:
        return False, "未使用 --config 启动，无法写回文件"
    try:
        p = Path(hub._config_file_path)
        data: Dict[str, Any] = {}
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        data["host"] = hub._tcp_listen_host
        data["port"] = int(hub._tcp_listen_port)
        data["web_host"] = hub._web_listen_host
        data["web_port"] = int(hub._web_listen_port)
        data["upstream_host"] = hub._upstream_host
        data["upstream_port"] = int(hub._upstream_port)
        data["web_user"] = hub.web_user
        data["web_password"] = hub.web_password
        data["encoding"] = hub.encoding
        data["add_samples_queue_size"] = hub._add_samples_max
        data["privileged_add_samples_host"] = hub._privileged_add_samples_host
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


def _safe_static_path(path: str) -> Optional[Path]:
    """只允许 mock_web_static 下的文件。"""
    if not path.startswith("/static/"):
        return None
    rel = path[len("/static/") :].lstrip("/").replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    base = _STATIC_DIR.resolve()
    fp = (base / rel).resolve()
    try:
        fp.relative_to(base)
    except ValueError:
        return None
    return fp if fp.is_file() else None


def _q_int(q: Dict[str, str], key: str, default: int) -> int:
    v = (q.get(key) or "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _q_bool(q: Dict[str, str], key: str, default: bool) -> bool:
    v = (q.get(key) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


async def _handle_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    hub: GatewayHub,
) -> None:
    try:
        first = await reader.read(65536)
        if not first:
            return
        header_end = first.find(b"\r\n\r\n")
        body = b""
        if header_end >= 0:
            header_blob = first[:header_end].decode("latin-1", errors="replace")
            body = first[header_end + 4 :]
            first_line = header_blob.split("\r\n", 1)[0]
            headers: Dict[str, str] = {}
            for hl in header_blob.split("\r\n")[1:]:
                if ":" in hl:
                    k, v = hl.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            cl = int(headers.get("content-length", "0") or "0")
            while len(body) < cl:
                chunk = await reader.read(cl - len(body))
                if not chunk:
                    break
                body += chunk
        else:
            first_line = first.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")

        parts = first_line.split()
        method = parts[0].upper() if parts else "GET"
        raw_target = parts[1] if len(parts) > 1 else "/"
        path_only, _, qstr = raw_target.partition("?")
        path = path_only.split("#", 1)[0]
        qparams: Dict[str, str] = dict(urllib.parse.parse_qsl(qstr, keep_blank_values=True)) if qstr else {}

        # 静态资源（SPA）
        if method == "GET" and path.startswith("/static/"):
            fp = _safe_static_path(path)
            if fp is None:
                await _http_send(writer, 404, b"Not Found", "text/plain; charset=utf-8")
                return
            suf = fp.suffix.lower()
            mime = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
            }.get(suf, "application/octet-stream")
            await _http_send(writer, 200, fp.read_bytes(), mime)
            return

        if method == "GET" and path in ("/", "/index.html"):
            idx = _STATIC_DIR / "index.html"
            if idx.is_file():
                await _http_send(writer, 200, idx.read_bytes(), "text/html; charset=utf-8")
            else:
                await _http_send(writer, 200, _legacy_queue_html_page(hub), "text/html; charset=utf-8")
            return

        if method == "GET" and path == "/legacy":
            await _http_send(writer, 200, _legacy_queue_html_page(hub), "text/html; charset=utf-8")
            return

        if method == "GET" and path == "/api/queue":
            items = [_queue_item_to_api_dict(p) for p in hub.pending_snapshot()]
            payload = json.dumps({"ok": True, "items": items}, ensure_ascii=False).encode("utf-8")
            await _http_send(writer, 200, payload, "application/json; charset=utf-8")
            return

        if method == "GET" and path == "/api/config":
            tcp_listen = (
                f"{hub._tcp_listen_host}:{hub._tcp_listen_port}"
                if hub._tcp_listen_port
                else ""
            )
            web_ln = (
                f"{hub._web_listen_host}:{hub._web_listen_port}"
                if hub._web_listen_port
                else ""
            )
            cfg = {
                "ok": True,
                "hasWebCredentials": bool(hub.web_user and hub.web_password),
                "queueMax": hub._add_samples_max,
                "queueCurrent": len(hub.pending_snapshot()),
                "upstream": f"{hub._upstream_host}:{hub._upstream_port}",
                "tcpListen": tcp_listen,
                "webListen": web_ln,
                "webUser": hub.web_user or "",
                "encoding": hub.encoding,
                "instrumentLongConnection": not hub._instrument_short_connection,
                "upstreamHeartbeatInterval": hub._upstream_heartbeat_interval_s,
                "upstreamAutoReconnect": hub._upstream_auto_reconnect,
                "privilegedAddSamplesHost": hub._privileged_add_samples_host,
                "remoteControlState": hub._remote_control_display,
                "remoteControlStateError": hub._remote_control_last_err,
                "configFile": str(hub._config_file_path) if hub._config_file_path else "",
            }
            await _http_send(
                writer,
                200,
                json.dumps(cfg, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings":
            pl = {
                "ok": True,
                "queueCurrent": len(hub.pending_snapshot()),
                **_hub_settings_public_dict(hub),
            }
            await _http_send(
                writer,
                200,
                json.dumps(pl, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/transports":
            data = await hub.fetch_transports_list_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/settings/transport":
            tk = (qparams.get("key") or "").strip()
            data = await hub.fetch_transport_detail_json(tk)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "PUT" and path == "/api/settings":
            try:
                obj = json.loads(body.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "无效 JSON"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            if not isinstance(obj, dict):
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "请求体须为 JSON 对象"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            restart_required = False
            notes: List[str] = []
            upstream_addr_changed = False
            if "tcpListenHost" in obj:
                v = str(obj.get("tcpListenHost") or "").strip()
                if v != hub._tcp_listen_host:
                    hub._tcp_listen_host = v
                    restart_required = True
            if "tcpListenPort" in obj:
                try:
                    p = int(obj["tcpListenPort"])
                except (TypeError, ValueError, KeyError):
                    p = hub._tcp_listen_port
                p = max(1, min(65535, p))
                if p != hub._tcp_listen_port:
                    hub._tcp_listen_port = p
                    restart_required = True
            if "webListenHost" in obj:
                v = str(obj.get("webListenHost") or "").strip()
                if v != hub._web_listen_host:
                    hub._web_listen_host = v
                    restart_required = True
            if "webListenPort" in obj:
                try:
                    p = int(obj["webListenPort"])
                except (TypeError, ValueError, KeyError):
                    p = hub._web_listen_port
                p = max(1, min(65535, p))
                if p != hub._web_listen_port:
                    hub._web_listen_port = p
                    restart_required = True
            if restart_required:
                notes.append("客户端监听或网页监听地址已更改：须重启 cornerstone-mock 进程后方可生效。")
            if "upstreamHost" in obj:
                nh = str(obj.get("upstreamHost") or "").strip()
                if nh != hub._upstream_host:
                    hub._upstream_host = nh
                    upstream_addr_changed = True
            if "upstreamPort" in obj:
                try:
                    np = int(obj["upstreamPort"])
                except (TypeError, ValueError, KeyError):
                    np = hub._upstream_port
                np = max(1, min(65535, np))
                if np != hub._upstream_port:
                    hub._upstream_port = np
                    upstream_addr_changed = True
            if "webUser" in obj:
                hub.web_user = str(obj.get("webUser") or "").strip()
            if "webPassword" in obj and obj["webPassword"] is not None:
                hub.web_password = str(obj["webPassword"])
            if "privilegedAddSamplesHost" in obj:
                hub._privileged_add_samples_host = str(obj.get("privilegedAddSamplesHost") or "").strip()
            if "queueMax" in obj:
                try:
                    hub.set_add_samples_queue_max(int(obj["queueMax"]))
                except (TypeError, ValueError, KeyError):
                    pass
            reco_ok = True
            reco_err = ""
            if upstream_addr_changed:
                reco_ok, reco_err = await hub.reconnect_upstream_with_current_target()
                if reco_ok:
                    notes.append("上游 TCP 已按新地址重连。")
                else:
                    notes.append(f"上游重连失败: {reco_err}")
            persist_ok = False
            persist_err = ""
            want_persist = bool(obj.get("persistToConfigFile", True))
            if want_persist and hub._config_file_path is not None:
                persist_ok, persist_err = _persist_hub_settings_to_config(hub)
            elif want_persist:
                persist_err = "未使用 --config 启动，跳过写回文件"
            out = {
                "ok": reco_ok,
                "restartRequired": restart_required,
                "upstreamReconnectOk": reco_ok,
                "upstreamReconnectError": reco_err,
                "persistOk": persist_ok,
                "persistError": persist_err,
                "notes": notes,
                "settings": {**_hub_settings_public_dict(hub), "queueCurrent": len(hub.pending_snapshot())},
            }
            await _http_send(
                writer,
                200,
                json.dumps(out, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/status":
            st = {
                "ok": True,
                "upstreamConnected": hub.upstream_connected(),
                "lastHeartbeatReplyAt": hub._last_upstream_heartbeat_reply_at,
                "queueCount": len(hub.pending_snapshot()),
                "queueMax": hub._add_samples_max,
                "remoteControlState": hub._remote_control_display,
                "privilegedAddSamplesHost": hub._privileged_add_samples_host,
                "remoteControlStateError": hub._remote_control_last_err,
            }
            await _http_send(
                writer,
                200,
                json.dumps(st, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/instrument-info":
            data = await hub.fetch_instrument_info_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/counters":
            data = await hub.fetch_maintenance_counters_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/counter":
            ck = (qparams.get("key") or "").strip()
            data = await hub.fetch_counter_detail_json(ck)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/environment/ambients":
            data = await hub.fetch_ambients_json_api()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/diagnostic/digital-io":
            data = await hub.fetch_digital_io_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/diagnostic/status-check":
            data = await hub.fetch_status_check_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/sets":
            n = _q_int(qparams, "number", 10)
            sa = _q_int(qparams, "start_at", -1)
            fk = (qparams.get("filter_key") or "").strip()
            # 留空时与常见 CLI ``--filter-key 0`` 一致；多数仪器对 FilterKey="" 与 ``0`` 语义不同，前者常无数据。
            if fk == "":
                fk = "0"
            data = await hub.fetch_sets_json(fk, n, sa)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/set-reps":
            sk = (qparams.get("set_key") or "").strip()
            inc = _q_bool(qparams, "include_detail", True)
            tg = _q_int(qparams, "tag", -1)
            data = await hub.fetch_set_reps_json(sk, include_detail=inc, tag=tg)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/rep-plot":
            sk = (qparams.get("set_key") or "").strip()
            tg = (qparams.get("tag") or "").strip()
            data = await hub.fetch_rep_plot_json(sk, tg)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/rep-detail":
            sk = (qparams.get("set_key") or "").strip()
            tg = (qparams.get("tag") or "").strip()
            data = await hub.fetch_rep_detail_json(sk, tg)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/status-widgets":
            data = await hub.fetch_status_widgets_json()
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "GET" and path == "/api/instrument/set-stats":
            sk = (qparams.get("set_key") or "").strip()
            if not sk:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "缺少 set_key"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            data = await hub.fetch_set_collection_stats_json(sk)
            await _http_send(
                writer,
                200,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "POST" and path == "/api/queue/send":
            try:
                obj = json.loads(body.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                await _http_send(
                    writer,
                    400,
                    json.dumps({"ok": False, "error": "无效 JSON"}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            ids = set(obj.get("ids") or [])
            selected = hub.remove_pending_by_ids(ids)
            results: List[Dict[str, Any]] = []
            for p in selected:
                r = await hub.forward_add_samples_web(p.payload_xml)
                results.append({"id": p.entry_id, "upstreamResponse": r})
            out = {"ok": True, "results": results}
            if not selected:
                out = {"ok": False, "error": "未选择任何条目或 ID 无效", "results": []}
            await _http_send(
                writer,
                200,
                json.dumps(out, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if method == "POST" and path == "/send":
            form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
            ids = set(form.get("id", []))
            selected = hub.remove_pending_by_ids(ids)
            results: List[str] = []
            for p in selected:
                r = await hub.forward_add_samples_web(p.payload_xml)
                results.append(f"--- {p.entry_id} ---\n{r}")
            summary = "\n\n".join(results) if results else "未选择任何条目"
            msg = html.escape(summary)
            data = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><title>发送结果</title></head>
<body><h1>上游应答</h1><pre>{msg}</pre><p><a href="/">返回主页</a> · <a href="/legacy">旧版队列</a></p></body></html>""".encode(
                "utf-8"
            )
            await _http_send(writer, 200, data, "text/html; charset=utf-8")
            return

        await _http_send(writer, 404, b"Not Found", "text/plain; charset=utf-8")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _run_gateway(
    listen_host: str,
    listen_port: int,
    web_host: str,
    web_port: int,
    upstream_host: str,
    upstream_port: int,
    encoding: str,
    add_samples_queue_size: int,
    synthetic_logon_after_first: bool,
    instrument_short_connection: bool,
    upstream_heartbeat_interval: float,
    upstream_auto_reconnect: bool,
    async_message_interval: float,
    web_user: str,
    web_password: str,
    privileged_add_samples_host: str,
    config_file_path: Optional[Path] = None,
) -> None:
    hub = GatewayHub(
        upstream_host=upstream_host,
        upstream_port=upstream_port,
        encoding=encoding,
        add_samples_queue_size=add_samples_queue_size,
        synthetic_logon_after_first=synthetic_logon_after_first,
        instrument_short_connection=instrument_short_connection,
        upstream_heartbeat_interval_s=upstream_heartbeat_interval,
        upstream_auto_reconnect=upstream_auto_reconnect,
        web_user=web_user,
        web_password=web_password,
        privileged_add_samples_host=privileged_add_samples_host,
        tcp_listen_host=listen_host,
        tcp_listen_port=listen_port,
        web_listen_host=web_host,
        web_listen_port=web_port,
        config_file_path=config_file_path,
    )

    async def client_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _handle_client(r, w, hub=hub, async_message_interval=async_message_interval)

    async def http_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _handle_http(r, w, hub=hub)

    srv_client = await asyncio.start_server(client_cb, listen_host, listen_port)
    srv_web = await asyncio.start_server(http_cb, web_host, web_port)

    c_addrs = ", ".join(str(s.getsockname()) for s in srv_client.sockets or [])
    w_addrs = ", ".join(str(s.getsockname()) for s in srv_web.sockets or [])
    print(f"[gateway] TCP clients: {c_addrs} (encoding={encoding})")
    print(f"[gateway] Web UI: http://{web_host}:{web_port}/  （{w_addrs}）")
    print(
        f"[gateway] Upstream Cornerstone: {upstream_host}:{upstream_port} ; "
        f"synthetic 2nd+ Logon={'on' if synthetic_logon_after_first else 'off'} ; "
        f"instrument API={'short TCP' if instrument_short_connection else 'long (reuse upstream)'} ; "
        f"upstream heartbeat={upstream_heartbeat_interval}s ; "
        f"upstream auto-reconnect={'on' if upstream_auto_reconnect else 'off'}"
    )
    if hub.web_user:
        print(f"[gateway] Web→upstream Logon user: {hub.web_user!r} (password {'set' if hub.web_password else 'empty'})")
    else:
        print("[gateway] Web→upstream Logon: --web-user not set (web send will fail until configured or a TCP client logs upstream in)")
    if hub._privileged_add_samples_host:
        print(
            f"[gateway] AddSamples 直通上位机 IP: {hub._privileged_add_samples_host!r} "
            f"(其余 TCP 客户端仍截留)"
        )

    async def _preconnect_upstream_long_instrument() -> None:
        """仪器 API 长连接模式：启动后立即建上游 TCP，并在配置了 web 账号时预先 Logon。"""
        if hub._instrument_short_connection:
            return
        try:
            await hub._ensure_upstream()
            print("[gateway] upstream TCP connected at startup (instrument long mode)")
        except Exception as e:
            print(f"[gateway] startup upstream TCP connect failed: {e}")
            return
        if hub.web_user and hub.web_password:
            ok, err = await hub._ensure_upstream_instrument_logon_for_web()
            if ok:
                print("[gateway] upstream web Logon completed at startup")
            else:
                print(f"[gateway] startup upstream web Logon failed: {err}")

    async with srv_client, srv_web:
        await _preconnect_upstream_long_instrument()
        await asyncio.gather(srv_client.serve_forever(), srv_web.serve_forever())


def _load_mock_config_defaults(config_path: Path) -> Dict[str, Any]:
    """
    读取 JSON 配置文件，返回可传给 argparse.set_defaults 的字段（与命令行长选项对应的 dest 名一致）。
    未识别的键会跳过并打印警告。根对象必须为 JSON object。
    """
    allowed = {
        "host",
        "port",
        "web_host",
        "web_port",
        "upstream_host",
        "upstream_port",
        "encoding",
        "add_samples_queue_size",
        "no_synthetic_logon",
        "instrument_long_connection",
        "upstream_heartbeat_interval",
        "upstream_auto_reconnect",
        "async_message_interval",
        "web_user",
        "web_password",
        "privileged_add_samples_host",
    }
    text = config_path.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点须为 JSON 对象 {...}")
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in allowed:
            print(f"[cornerstone-mock] 配置文件忽略未知键: {k!r}", file=sys.stderr)
            continue
        if k == "encoding" and v is not None and str(v).strip() != "":
            out[k] = _normalize_encoding(str(v))
            continue
        if k in ("port", "web_port", "upstream_port", "add_samples_queue_size"):
            out[k] = int(v)
            continue
        if k == "async_message_interval":
            out[k] = float(v)
            continue
        if k == "no_synthetic_logon":
            out[k] = bool(v)
            continue
        if k == "instrument_long_connection":
            out["instrument_short_connection"] = not bool(v)
            continue
        if k == "upstream_heartbeat_interval":
            out[k] = float(v)
            continue
        if k == "upstream_auto_reconnect":
            out["no_upstream_auto_reconnect"] = not bool(v)
            continue
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else str(v)
    return out


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-c", "--config", type=str, default=None, metavar="PATH", help=argparse.SUPPRESS)
    pre_args, argv_rest = pre.parse_known_args()

    parser = argparse.ArgumentParser(
        prog="cornerstone-mock",
        description=(
            "多客户端 TCP 网关：转发到单机 Cornerstone；AddSamples 截留，网页多选后发送到上游。"
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="JSON 配置文件路径（键名与下方长选项的 dest 一致，如 host、web_port；命令行参数优先覆盖文件）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="客户端连接的监听地址")
    parser.add_argument("--port", type=int, default=12345, help="客户端连接的监听端口")
    parser.add_argument("--web-host", default="127.0.0.1", help="网页队列监听地址")
    parser.add_argument("--web-port", type=int, default=8765, help="网页队列监听端口")
    parser.add_argument("--upstream-host", default="127.0.0.1", help="真实 Cornerstone 地址")
    parser.add_argument("--upstream-port", type=int, default=12346, help="真实 Cornerstone 端口")
    parser.add_argument("--encoding", type=_normalize_encoding, default="utf-16-le")
    parser.add_argument(
        "--add-samples-queue-size",
        type=int,
        default=8,
        help="截留 AddSamples 队列最大条数（超出时丢弃最旧）",
    )
    parser.add_argument(
        "--no-synthetic-logon",
        action="store_true",
        help="所有 Logon 均转发上游（不再为第 2 个及以后客户端合成成功应答）",
    )
    parser.add_argument(
        "--async-message-interval",
        type=float,
        default=0.0,
        help=">0 时向各客户端定时推送 <CornerstoneMessage/>（秒）",
    )
    parser.add_argument(
        "--web-user",
        default="",
        help="网页「发送到仪器」时在上游使用的仪器远程用户名（与 tcp logon 一致）；缺省则发送前会提示需配置",
    )
    parser.add_argument(
        "--web-password",
        default="",
        help="与 --web-user 配对的密码；网页发往仪器前会先对上游执行 Logon",
    )
    parser.add_argument(
        "--privileged-add-samples-host",
        default="",
        metavar="HOST",
        help="该主机地址发来的 TCP AddSamples 不截留、直接转发上游（与配置文件 privileged_add_samples_host 一致）",
    )
    parser.add_argument(
        "--instrument-short-connection",
        action="store_true",
        help=(
            "网页 /api/instrument/* 使用独立短连接（每次 TCP+logon）；"
            "默认复用网关与上游 Cornerstone 的长连接（与单机会话更兼容）"
        ),
    )
    parser.add_argument(
        "--upstream-heartbeat-interval",
        type=float,
        default=60.0,
        metavar="SEC",
        help="网关上游 Cornerstone 长连接心跳间隔（秒），发送 <Heartbeat/>；0 关闭",
    )
    parser.add_argument(
        "--no-upstream-auto-reconnect",
        action="store_true",
        help="上游 TCP 断开后不自动重连（默认断线后自动重连并尽量恢复 web Logon）",
    )

    if pre_args.config:
        cfg_path = Path(pre_args.config).expanduser()
        if not cfg_path.is_file():
            print(f"[cornerstone-mock] 配置文件不存在: {cfg_path}", file=sys.stderr)
            return 2
        try:
            defaults = _load_mock_config_defaults(cfg_path)
            parser.set_defaults(**defaults)
        except (OSError, ValueError, json.JSONDecodeError, argparse.ArgumentTypeError) as e:
            print(f"[cornerstone-mock] 读取配置失败: {e}", file=sys.stderr)
            return 2

    args = parser.parse_args(argv_rest)

    cfg_resolved: Optional[Path] = None
    if args.config:
        cfg_resolved = Path(args.config).expanduser().resolve()

    try:
        asyncio.run(
            _run_gateway(
                listen_host=args.host,
                listen_port=args.port,
                web_host=args.web_host,
                web_port=args.web_port,
                upstream_host=args.upstream_host,
                upstream_port=args.upstream_port,
                encoding=args.encoding,
                add_samples_queue_size=args.add_samples_queue_size,
                synthetic_logon_after_first=not args.no_synthetic_logon,
                instrument_short_connection=bool(args.instrument_short_connection),
                upstream_heartbeat_interval=float(args.upstream_heartbeat_interval),
                upstream_auto_reconnect=not bool(args.no_upstream_auto_reconnect),
                async_message_interval=args.async_message_interval,
                web_user=args.web_user,
                web_password=args.web_password,
                privileged_add_samples_host=args.privileged_add_samples_host,
                config_file_path=cfg_resolved,
            )
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
