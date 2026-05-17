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


def _automation_operation_mode_display(mode: str) -> str:
    m = (mode or "").strip()
    ml = m.lower()
    if ml == "enabled":
        return "已启用"
    if ml == "disabled":
        return "已禁用"
    return m or "—"


def _parse_automation_status(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    """解析 ``<AutomationStatus>``（``automation-status`` Remote Query）。"""
    s = (resp_xml or "").strip()
    if not s:
        return {}, "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return {}, f"XML 解析失败: {e}"

    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "AutomationStatus":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "AutomationStatus")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "AutomationStatus":
                outer = el
                break
    if outer is None:
        return {}, "未找到 AutomationStatus 根节点"

    ec = (outer.attrib.get("ErrorCode") or "").strip()
    em = (outer.attrib.get("ErrorMessage") or "").strip()
    if ec and ec not in ("0",) and ec.lower() not in ("success", "ok"):
        return {}, em or f"仪器 ErrorCode={ec}"

    auto_el: Optional[ET.Element] = None
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Automation":
            continue
        aid = (ch.attrib.get("Id") or ch.attrib.get("id") or "").strip()
        if aid == "AutoCleaner" or auto_el is None:
            auto_el = ch
        if aid == "AutoCleaner":
            break

    if auto_el is None:
        return {"id": "", "rows": []}, ""

    aid = (auto_el.attrib.get("Id") or auto_el.attrib.get("id") or "").strip()
    mode = (auto_el.attrib.get("OperationMode") or auto_el.attrib.get("operationMode") or "").strip()
    clean_interval = _element_text(_first_child_by_local(auto_el, "CleanInterval")).strip()
    num_cycles = _element_text(_first_child_by_local(auto_el, "NumberOfCleanCycles")).strip()
    rows: List[Dict[str, str]] = [
        {"label": "自动清洁器状态", "value": _automation_operation_mode_display(mode)},
        {"label": "清扫周期数目", "value": num_cycles or "—"},
        {"label": "分析清洁间隔", "value": clean_interval or "—"},
    ]
    return {
        "id": aid,
        "operationMode": mode,
        "cleanInterval": clean_interval,
        "numberOfCleanCycles": num_cycles,
        "rows": rows,
    }, ""


_SYSTEM_PARAM_SECTION_DEFS: List[Tuple[str, str, List[str]]] = [
    (
        "health",
        "CORNERSTONE 健康管理",
        ["ParticipationLevel", "ShareUsageWithLeco", "ShareUsageWithLECO"],
    ),
    ("software", "软件更新", ["AutoCheckForUpdates", "AutoCheckSoftwareUpdates"]),
    ("gasStandby", "气体备用", ["Mode", "PedestalDownGasState", "Time"]),
    ("gasWakeup", "气体唤醒", ["Weekday", "Saturday", "Sunday", "RunLeakCheck"]),
    ("analytes", "分析物", ["AnalyzeCarbon", "AnalyzeSulfur"]),
    (
        "leakCheck",
        "漏气检查",
        [
            "MaximumSystemleak",
            "MaximumSystemLeak",
            "MaximumIncoming&Dosersegmentleak",
            "MaximumIncomingAndDosersegmentleak",
            "MaximumFurnacesegmentleak",
            "MaximumFurnaceSegmentLeak",
            "MaximumDetectorsegmentleak",
            "MaximumDetectorSegmentLeak",
        ],
    ),
    ("instrument", "仪器选项", ["DustFilterHeater", "DustFilterTemperature", "BackPressureControl", "GasDoser", "AutoIncrementSampleName"]),
]

_SYSTEM_PARAM_LABEL_ZH: Dict[str, str] = {
    "ParticipationLevel": "参与级别",
    "ShareUsageWithLeco": "与 LECO 共享使用",
    "ShareUsageWithLECO": "与 LECO 共享使用",
    "AutoCheckForUpdates": "自动检查软件更新",
    "AutoCheckSoftwareUpdates": "自动检查软件更新",
    "Mode": "模式",
    "PedestalDownGasState": "气体状态",
    "Time": "时间",
    "Weekday": "工作日",
    "Saturday": "星期六",
    "Sunday": "星期天",
    "RunLeakCheck": "运行漏气检查",
    "AnalyzeCarbon": "分析碳",
    "AnalyzeSulfur": "分析硫",
    "MaximumSystemleak": "最大系统泄漏",
    "MaximumSystemLeak": "最大系统泄漏",
    "MaximumIncoming&Dosersegmentleak": "最大进气与剂量段泄漏",
    "MaximumIncomingAndDosersegmentleak": "最大进气与剂量段泄漏",
    "MaximumFurnacesegmentleak": "最大炉段泄漏",
    "MaximumFurnaceSegmentLeak": "最大炉段泄漏",
    "MaximumDetectorsegmentleak": "最大检测器段泄漏",
    "MaximumDetectorSegmentLeak": "最大检测器段泄漏",
    "DustFilterHeater": "粉尘过滤器加热",
    "DustFilterTemperature": "粉尘过滤器温度",
    "BackPressureControl": "背压控制",
    "GasDoser": "气体剂量器",
    "AutoIncrementSampleName": "样品名自动递增",
}


def _system_param_field_kind(raw_value: str, display: str) -> str:
    rv = (raw_value or "").strip().lower()
    if rv in ("true", "false"):
        return "bool"
    dl = (display or "").strip().lower()
    if dl in ("yes", "no", "enabled", "disabled"):
        return "bool"
    return "text"


def _parse_system_parameters(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    """解析 ``<SystemParameters>``（``system-parameters`` Remote Query）。"""
    s = (resp_xml or "").strip()
    if not s:
        return {}, "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return {}, f"XML 解析失败: {e}"

    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "SystemParameters":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "SystemParameters")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "SystemParameters":
                outer = el
                break
    if outer is None:
        return {}, "未找到 SystemParameters 根节点"

    ec = (outer.attrib.get("ErrorCode") or "").strip()
    em = (outer.attrib.get("ErrorMessage") or "").strip()
    if ec and ec not in ("0",) and ec.lower() not in ("success", "ok"):
        return {}, em or f"仪器 ErrorCode={ec}"

    fields_by_id: Dict[str, Dict[str, Any]] = {}
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Field":
            continue
        fid = (ch.attrib.get("Id") or ch.attrib.get("id") or "").strip()
        if not fid:
            continue
        label_en = html.unescape((ch.attrib.get("Label") or ch.attrib.get("label") or "").strip())
        display = (ch.text or "").strip()
        raw = (ch.attrib.get("RawValue") or ch.attrib.get("rawValue") or "").strip()
        units = (ch.attrib.get("Units") or ch.attrib.get("units") or "").strip()
        fields_by_id[fid] = {
            "id": fid,
            "label": _SYSTEM_PARAM_LABEL_ZH.get(fid) or label_en or fid,
            "labelEn": label_en,
            "display": display,
            "rawValue": raw,
            "units": units,
            "kind": _system_param_field_kind(raw, display),
        }

    sections: List[Dict[str, Any]] = []
    used: Set[str] = set()
    for sid, title, id_list in _SYSTEM_PARAM_SECTION_DEFS:
        rows: List[Dict[str, Any]] = []
        for fid in id_list:
            if fid in fields_by_id:
                rows.append(fields_by_id[fid])
                used.add(fid)
        if rows:
            sections.append({"id": sid, "title": title, "fields": rows})

    remaining = [fields_by_id[fid] for fid in fields_by_id if fid not in used]
    if remaining:
        sections.append({"id": "other", "title": "其它", "fields": remaining})

    return {"sections": sections}, ""


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


def _parse_methods_list(resp_xml: str) -> Tuple[List[Dict[str, Any]], str]:
    """解析 ``<Methods>`` 方法列表。"""
    s = (resp_xml or "").strip()
    if not s:
        return [], "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return [], f"XML 解析失败: {e}"
    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Methods":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "Methods")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Methods":
                outer = el
                break
    if outer is None:
        return [], "未找到 Methods 根节点"

    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Method":
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


def _parse_method_field(el: ET.Element) -> Dict[str, str]:
    text = (el.text or "").strip()
    raw = (el.attrib.get("RawValue") or el.attrib.get("rawValue") or "").strip()
    return {
        "label": (el.attrib.get("Label") or el.attrib.get("label") or "").strip(),
        "id": (el.attrib.get("Id") or el.attrib.get("id") or "").strip(),
        "value": text or raw,
        "rawValue": raw,
        "units": (el.attrib.get("Units") or el.attrib.get("units") or "").strip(),
        "valueStatus": (el.attrib.get("ValueStatus") or el.attrib.get("valueStatus") or "").strip(),
    }


def _parse_method_sets(el: ET.Element) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for set_el in el:
        if _xml_local_tag(set_el.tag) != "Set":
            continue
        key = (set_el.attrib.get("Key") or set_el.attrib.get("key") or "").strip()
        reps = 0
        reps_el = _first_child_by_local(set_el, "Replicates")
        if reps_el is not None:
            for rep in reps_el:
                if _xml_local_tag(rep.tag) == "Replicate":
                    reps += 1
        out.append({"key": key, "replicateCount": reps})
    return out


def _parse_method_block(el: ET.Element) -> Dict[str, Any]:
    """解析 ``Section`` / ``Subsection`` / ``SubSection`` / ``Range`` 等嵌套块。"""
    tag = _xml_local_tag(el.tag)
    label = (el.attrib.get("Label") or el.attrib.get("label") or "").strip()
    if tag == "Range":
        label = label or (el.attrib.get("Range") or el.attrib.get("range") or "").strip()
    block_id = (el.attrib.get("Id") or el.attrib.get("id") or "").strip()
    fields: List[Dict[str, str]] = []
    children: List[Dict[str, Any]] = []
    for ch in el:
        ctag = _xml_local_tag(ch.tag)
        if ctag == "Field":
            fields.append(_parse_method_field(ch))
        elif ctag == "Sets":
            sets = _parse_method_sets(ch)
            if sets:
                children.append({"kind": "sets", "label": "Sets", "sets": sets})
        elif ctag in ("Section", "Subsection", "SubSection", "Range"):
            children.append(_parse_method_block(ch))
    return {
        "kind": tag.lower() if tag else "block",
        "label": label,
        "id": block_id,
        "fields": fields,
        "children": children,
    }


def _parse_method_detail(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    """解析单条 ``<Method Key=\"…\"/>`` 应答（含 ``Sections`` 树）。"""
    s = (resp_xml or "").strip()
    if not s:
        return {}, "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return {}, f"XML 解析失败: {e}"
    box: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Method":
        box = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        box = _first_child_by_local(root, "Method")
    if box is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Method":
                box = el
                break
    if box is None:
        return {}, "未找到 Method 根节点"

    key_el = _first_child_by_local(box, "Key")
    key = (key_el.text or "").strip() if key_el is not None else (box.attrib.get("Key") or "").strip()

    scalars: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    for ch in box:
        tag = _xml_local_tag(ch.tag)
        if tag == "Sections":
            for sec in ch:
                if _xml_local_tag(sec.tag) == "Section":
                    sections.append(_parse_method_block(sec))
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


def _standard_certified_display(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    val = (el.text or "").strip()
    if not val:
        return ""
    units = (el.attrib.get("Units") or el.attrib.get("units") or "").strip()
    if units.lower() == "percent":
        return f"{val} %"
    if units:
        return f"{val} {units}"
    return val


def _standard_list_analyte_values(std_el: ET.Element) -> Tuple[str, str]:
    """从列表项或详情中的 ``Analytes`` 提取碳/硫认证值展示串。"""
    carbon, sulfur = "", ""
    analytes = _first_child_by_local(std_el, "Analytes")
    if analytes is None:
        return carbon, sulfur
    for a in analytes:
        if _xml_local_tag(a.tag) != "Analyte":
            continue
        label = (a.attrib.get("Label") or a.attrib.get("label") or "").strip().lower()
        key_txt = _element_text(_first_child_by_local(a, "Key")).strip().lower()
        disp = _standard_certified_display(_first_child_by_local(a, "Certified"))
        if label == "carbon" or key_txt == "carbon":
            carbon = disp
        elif label == "sulfur" or key_txt == "sulfur":
            sulfur = disp
    return carbon, sulfur


def _parse_standards_list(resp_xml: str) -> Tuple[List[Dict[str, Any]], str]:
    """解析 ``<Standards>`` 标样列表。"""
    s = (resp_xml or "").strip()
    if not s:
        return [], "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return [], f"XML 解析失败: {e}"
    outer: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Standards":
        outer = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        outer = _first_child_by_local(root, "Standards")
    if outer is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Standards":
                outer = el
                break
    if outer is None:
        return [], "未找到 Standards 根节点"

    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Standard":
            continue

        def txt(local: str) -> str:
            return _element_text(_first_child_by_local(ch, local)).strip()

        excluded_raw = txt("Excluded").lower()
        lm_raw = txt("LastModified")
        carbon, sulfur = _standard_list_analyte_values(ch)
        rows.append(
            {
                "key": txt("Key"),
                "name": txt("Name"),
                "description": txt("Description"),
                "carbon": carbon,
                "sulfur": sulfur,
                "lastModified": _transport_datetime_display(lm_raw),
                "excluded": excluded_raw in ("1", "true", "yes"),
            }
        )
    return rows, ""


def _parse_standard_analyte(a_el: ET.Element) -> Dict[str, Any]:
    label = (a_el.attrib.get("Label") or a_el.attrib.get("label") or "").strip()
    key_txt = _element_text(_first_child_by_local(a_el, "Key")).strip()
    fields: List[Dict[str, str]] = []
    for ch in a_el:
        tag = _xml_local_tag(ch.tag)
        if tag == "Key":
            continue
        flabel = (ch.attrib.get("Label") or ch.attrib.get("label") or tag).strip()
        val = (ch.text or "").strip()
        if tag == "Certified":
            disp = _standard_certified_display(ch)
        else:
            units = (ch.attrib.get("Units") or ch.attrib.get("units") or "").strip()
            if val and units:
                disp = f"{val} {units}" if units.lower() != "percent" else f"{val} %"
            else:
                disp = val
        fields.append({"tag": tag, "label": flabel, "value": val, "display": disp or val or "—"})
    return {"label": label or key_txt, "key": key_txt, "fields": fields}


def _parse_standard_detail(resp_xml: str) -> Tuple[Dict[str, Any], str]:
    """解析单条 ``<Standard Key=\"…\"/>`` 应答。"""
    s = (resp_xml or "").strip()
    if not s:
        return {}, "空应答"
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        return {}, f"XML 解析失败: {e}"
    box: Optional[ET.Element] = None
    if _xml_local_tag(root.tag) == "Standard":
        box = root
    elif _xml_local_tag(root.tag) == "CornerstoneMessage":
        box = _first_child_by_local(root, "Standard")
    if box is None:
        for el in root.iter():
            if _xml_local_tag(el.tag) == "Standard":
                box = el
                break
    if box is None:
        return {}, "未找到 Standard 根节点"

    key_el = _first_child_by_local(box, "Key")
    key = (key_el.text or "").strip() if key_el is not None else (box.attrib.get("Key") or "").strip()

    scalars: List[Dict[str, Any]] = []
    analytes: List[Dict[str, Any]] = []
    for ch in box:
        tag = _xml_local_tag(ch.tag)
        if tag == "Analytes":
            for a in ch:
                if _xml_local_tag(a.tag) == "Analyte":
                    analytes.append(_parse_standard_analyte(a))
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

    carbon, sulfur = _standard_list_analyte_values(box)
    return {
        "key": key,
        "scalars": scalars,
        "analytes": analytes,
        "carbon": carbon,
        "sulfur": sulfur,
    }, ""


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


def _find_setsex_container(root: ET.Element) -> ET.Element:
    """在应答根节点下定位 ``<SetsEx>``。"""
    lt = _xml_local_tag(root.tag)
    if lt == "SetsEx":
        return root
    if lt == "CornerstoneMessage":
        inner = _first_child_by_local(root, "SetsEx")
        if inner is not None:
            return inner
    for el in root.iter():
        if _xml_local_tag(el.tag) == "SetsEx":
            return el
    return root


def _parse_last_remote_added_set_keys(resp_xml: str) -> List[str]:
    """解析 ``<LastRemoteAddedSets>`` 下各 ``<Set Key=\"…\"/>``。"""
    root = ET.fromstring(resp_xml)
    box = root
    if _xml_local_tag(root.tag) != "LastRemoteAddedSets":
        found: Optional[ET.Element] = None
        for el in root.iter():
            if _xml_local_tag(el.tag) == "LastRemoteAddedSets":
                found = el
                break
        if found is not None:
            box = found
    keys: List[str] = []
    for ch in box:
        if _xml_local_tag(ch.tag) != "Set":
            continue
        k = (ch.attrib.get("Key") or ch.attrib.get("key") or "").strip()
        if not k:
            k = _element_text(_first_child_by_local(ch, "Key"))
        if k:
            keys.append(k)
    return keys


def _parse_sets_ex_response(resp_xml: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """解析 ``<SetsEx>`` 应答（外层 ``<Set>`` 常包裹内层含 ``HeaderFields`` 的 ``<Set>``）。"""
    root = ET.fromstring(resp_xml)
    outer = _find_setsex_container(root)
    analyte_defs = _parse_sets_analyte_defs(outer)
    rows: List[Dict[str, Any]] = []
    for ch in outer:
        if _xml_local_tag(ch.tag) != "Set":
            continue
        inner = _first_child_by_local(ch, "Set")
        node = inner if inner is not None else ch
        rows.append(_parse_one_set_row(node, analyte_defs))
    return rows, analyte_defs


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


__all__ = [n for n in globals() if n.startswith("_") and not n.startswith("__")]
