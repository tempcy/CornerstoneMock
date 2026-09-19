"""从 collect endpoint 载荷抽出扁平时序点（A1）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional


_METRIC_SAFE = re.compile(r"[^\w.\-]+", re.UNICODE)
_LEADING_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class MetricPoint:
    metric: str
    value_num: Optional[float] = None
    value_bool: Optional[bool] = None
    value_text: Optional[str] = None
    unit: str = ""


def _metric_id(parts: Iterable[str]) -> str:
    raw = ".".join(str(p).strip() for p in parts if str(p).strip())
    cleaned = _METRIC_SAFE.sub("_", raw).strip("._-")
    return cleaned[:160]


def _try_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        m = _LEADING_NUM.search(s)
        if not m:
            return None
        try:
            return float(m.group(0))
        except ValueError:
            return None


def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "ok", "passed", "pass", "success"):
        return True
    if s in ("0", "false", "no", "fail", "failed", "error"):
        return False
    return None


def _point_num(metric: str, value: Any, *, unit: str = "") -> Optional[MetricPoint]:
    n = _try_float(value)
    if n is None:
        return None
    return MetricPoint(metric=metric, value_num=n, unit=unit)


def _point_bool(metric: str, value: Any) -> Optional[MetricPoint]:
    b = _as_bool(value)
    if b is None:
        return None
    return MetricPoint(metric=metric, value_bool=b, value_num=1.0 if b else 0.0)


def _unwrap_data(ep: Any) -> Any:
    if not isinstance(ep, dict):
        return None
    if ep.get("ok") is False:
        return None
    if "data" in ep:
        return ep.get("data")
    return ep


def _from_status(data: Any) -> list[MetricPoint]:
    if not isinstance(data, dict):
        return []
    out: list[MetricPoint] = []
    mapping = (
        ("status.upstream_connected", data.get("upstreamConnected")),
        ("status.instrument_online", data.get("instrumentOnline")),
        ("status.business_online", data.get("businessOnline")),
        ("status.queue_count", data.get("queueCount")),
        ("status.heartbeat_fail_streak", data.get("heartbeatFailStreak")),
        ("status.command_fail_streak", data.get("commandFailStreak")),
        ("status.recv_buffer_bytes", data.get("recvBufferBytes")),
    )
    for name, raw in mapping:
        p = _point_bool(name, raw) if "online" in name or name.endswith("connected") else _point_num(name, raw)
        if p is None and name.startswith("status.") and name.endswith(("_connected", "_online")):
            p = _point_num(name, 1.0 if raw else 0.0)
        if p is not None:
            out.append(p)
    return out


def _from_status_check(data: Any) -> list[MetricPoint]:
    if not isinstance(data, dict):
        return []
    out: list[MetricPoint] = []
    sc = data.get("systemCheck") if isinstance(data.get("systemCheck"), dict) else {}
    for key in ("passed", "failed", "total", "executed"):
        p = _point_num(f"syscheck.{key}", sc.get(key))
        if p:
            out.append(p)
    for item in sc.get("items") or []:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or item.get("label") or "").strip()
        if not iid:
            continue
        p = _point_bool(_metric_id(["syscheck", "item", iid]), item.get("status"))
        if p:
            out.append(p)
    for od in data.get("odometers") or []:
        if not isinstance(od, dict):
            continue
        ot = str(od.get("type") or od.get("Type") or "").strip()
        if not ot:
            continue
        p = _point_num(_metric_id(["odometer", ot]), od.get("value"))
        if p:
            out.append(p)
    for el in data.get("elements") or []:
        if not isinstance(el, dict):
            continue
        key = str(el.get("key") or "").strip()
        if not key:
            continue
        p = _point_num(_metric_id(["element", key]), el.get("value"))
        if p:
            out.append(p)
    for leak in data.get("leakChecks") or []:
        if not isinstance(leak, dict):
            continue
        lid = str(leak.get("id") or leak.get("label") or "").strip() or "leak"
        summary = leak.get("summary")
        p = _point_bool(_metric_id(["leak", lid, "ok"]), summary)
        if p:
            out.append(p)
        else:
            out.append(MetricPoint(metric=_metric_id(["leak", lid, "present"]), value_num=1.0, value_bool=True))
    return out


def _from_counters(data: Any) -> list[MetricPoint]:
    items = None
    if isinstance(data, dict):
        items = data.get("items")
    elif isinstance(data, list):
        items = data
    if not isinstance(items, list):
        return []
    out: list[MetricPoint] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or row.get("name") or "").strip()
        if not key:
            continue
        mid = _metric_id(["counter", key])
        p = _point_num(f"{mid}.expires_in", row.get("expiresIn") or row.get("expires_in"))
        if p:
            out.append(p)
        b = _point_bool(f"{mid}.expired", row.get("isExpired") if "isExpired" in row else row.get("expired"))
        if b:
            out.append(b)
        ex = _point_bool(f"{mid}.excluded", row.get("excluded"))
        if ex:
            out.append(ex)
    return out


def _from_system_parameters(data: Any) -> list[MetricPoint]:
    if not isinstance(data, dict):
        return []
    out: list[MetricPoint] = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for field in sec.get("fields") or []:
            if not isinstance(field, dict):
                continue
            fid = str(field.get("id") or field.get("labelEn") or field.get("label") or "").strip()
            if not fid:
                continue
            raw = field.get("rawValue")
            if raw is None or str(raw).strip() == "":
                raw = field.get("display")
            unit = str(field.get("units") or "").strip()
            p = _point_num(_metric_id(["param", fid]), raw, unit=unit)
            if p:
                out.append(p)
            else:
                b = _point_bool(_metric_id(["param", fid]), raw if raw is not None else field.get("display"))
                if b:
                    out.append(b)
    return out


def _from_status_widgets(data: Any) -> list[MetricPoint]:
    widgets = None
    if isinstance(data, dict):
        widgets = data.get("widgets")
    elif isinstance(data, list):
        widgets = data
    if not isinstance(widgets, list):
        return []
    out: list[MetricPoint] = []
    for w in widgets:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("label") or w.get("id") or "").strip()
        if not wid:
            continue
        unit = str(w.get("units") or w.get("Units") or "").strip()
        p = _point_num(_metric_id(["gauge", wid]), w.get("value"), unit=unit)
        if p:
            out.append(p)
        warn = w.get("warning")
        if warn is True or str(warn).strip().lower() in ("1", "true", "yes"):
            out.append(
                MetricPoint(
                    metric=_metric_id(["gauge", wid, "warning"]),
                    value_bool=True,
                    value_num=1.0,
                )
            )
    return out


def _from_ambients(data: Any) -> list[MetricPoint]:
    items = None
    if isinstance(data, dict):
        items = data.get("items")
    elif isinstance(data, list):
        items = data
    if not isinstance(items, list):
        return []
    out: list[MetricPoint] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("key") or "").strip()
        if not name:
            continue
        unit = str(row.get("units") or row.get("Units") or "").strip()
        raw = row.get("valueRaw")
        if raw is None or str(raw).strip() == "":
            raw = row.get("value")
        p = _point_num(_metric_id(["ambient", name]), raw, unit=unit)
        if p:
            out.append(p)
        warn = row.get("inWarning")
        if warn is True or str(warn).strip().lower() in ("1", "true", "yes"):
            out.append(
                MetricPoint(
                    metric=_metric_id(["ambient", name, "warning"]),
                    value_bool=True,
                    value_num=1.0,
                )
            )
    return out


_EXTRACTORS = {
    "status": _from_status,
    "status-check": _from_status_check,
    "status-widgets": _from_status_widgets,
    "counters": _from_counters,
    "system-parameters": _from_system_parameters,
    "ambients": _from_ambients,
}


def extract_points(endpoints: Any) -> list[MetricPoint]:
    """``endpoints`` 为 collect 的 alias → {ok, data} 映射。"""
    if not isinstance(endpoints, dict):
        return []
    out: list[MetricPoint] = []
    seen: set[str] = set()
    for alias, extractor in _EXTRACTORS.items():
        payload = _unwrap_data(endpoints.get(alias))
        if payload is None:
            continue
        for pt in extractor(payload):
            if pt.metric in seen:
                continue
            seen.add(pt.metric)
            out.append(pt)
    return out
