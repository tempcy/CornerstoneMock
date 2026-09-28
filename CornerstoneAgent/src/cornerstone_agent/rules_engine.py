"""A2 规则引擎：对 Bridge 拉取的数据产出结构化建议。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .bridge_client import BridgeClient, BridgeError
from .envelopes import utc_iso

RULE_DAILY_MAINTENANCE = "instrument.daily_maintenance_counters"

DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "id": RULE_DAILY_MAINTENANCE,
        "enabled": True,
        "domain": "instrument",
        "severity": "maintain",
        "interval_s": 14400,
        "description": (
            "每 4h 经 Bridge 拉取维护计数器；若有未排除且已到期(IsExpired)项，"
            "判定日常维护缺失并记录。"
        ),
        "params": {
            "include_excluded": False,
            "only_expired": True,
        },
    }
]


@dataclass
class RuleDef:
    id: str
    enabled: bool = True
    domain: str = "instrument"
    severity: str = "maintain"
    interval_s: float = 14400.0
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "RuleDef | None":
        rid = str(raw.get("id") or "").strip()
        if not rid:
            return None
        severity = str(raw.get("severity") or "maintain").strip().lower()
        if severity not in ("info", "review", "reject", "maintain"):
            severity = "maintain"
        try:
            interval = float(raw.get("interval_s") if raw.get("interval_s") is not None else 14400)
        except (TypeError, ValueError):
            interval = 14400.0
        if interval < 60:
            interval = 60.0
        if interval > 86400 * 7:
            interval = float(86400 * 7)
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        return cls(
            id=rid,
            enabled=True if "enabled" not in raw else bool(raw.get("enabled")),
            domain=str(raw.get("domain") or "instrument").strip() or "instrument",
            severity=severity,
            interval_s=interval,
            description=str(raw.get("description") or "").strip(),
            params=dict(params),
        )


def bundled_rules_path() -> Path:
    """仓库内 ``CornerstoneAgent/rules/default.json``（相对包源码定位）。"""
    return Path(__file__).resolve().parents[2] / "rules" / "default.json"


def load_rule_defs(path: str | Path | None = None) -> list[RuleDef]:
    """加载规则定义；文件缺失或无效时回退内置默认。"""
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    candidates.append(bundled_rules_path())
    for p in candidates:
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = raw.get("rules") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            continue
        out: list[RuleDef] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rule = RuleDef.from_mapping(item)
            if rule is not None:
                out.append(rule)
        if out:
            return out
    return [RuleDef.from_mapping(x) for x in DEFAULT_RULES if RuleDef.from_mapping(x)]


def _counter_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _is_expired(row: dict[str, Any]) -> bool:
    if "isExpired" in row:
        return bool(row.get("isExpired"))
    if "expired" in row:
        return bool(row.get("expired"))
    return False


def _is_excluded(row: dict[str, Any]) -> bool:
    return bool(row.get("excluded"))


def evaluate_daily_maintenance_counters(
    counters_payload: Any,
    *,
    rule: RuleDef,
    instrument_id: str = "",
    lab_id: str = "",
) -> dict[str, Any]:
    """对已拉取的 counters JSON 评估日常维护缺失。

    Cornerstone 软件维护计数器 ``IsExpired=true`` 且未 Excluded 时，
    表示操作人员未按软件要求完成该项维护（含按天配置的日常项）。
    """
    params = rule.params or {}
    include_excluded = bool(params.get("include_excluded", False))
    only_expired = True if "only_expired" not in params else bool(params.get("only_expired"))

    items = _counter_items(counters_payload)
    missing: list[dict[str, Any]] = []
    for idx, row in enumerate(items):
        if only_expired and not _is_expired(row):
            continue
        if not include_excluded and _is_excluded(row):
            continue
        missing.append(
            {
                "path": f"counters.items[{idx}]",
                "key": str(row.get("key") or ""),
                "name": str(row.get("name") or ""),
                "description": str(row.get("description") or ""),
                "expiresIn": str(row.get("expiresIn") or row.get("expires_in") or ""),
                "lastModified": str(row.get("lastModified") or ""),
                "lastUsed": str(row.get("lastUsed") or ""),
                "excluded": _is_excluded(row),
                "isExpired": _is_expired(row),
            }
        )

    ts = utc_iso()
    suggestions: list[dict[str, Any]] = []
    if missing:
        names = [m["name"] or m["key"] or "?" for m in missing[:8]]
        more = f" 等共 {len(missing)} 项" if len(missing) > 8 else f"共 {len(missing)} 项"
        suggestions.append(
            {
                "rule_id": rule.id,
                "domain": rule.domain,
                "severity": rule.severity,
                "message": (
                    f"维护计数器显示有到期项（{', '.join(names)}{more}），"
                    "操作人员可能未按软件要求完成日常维护；"
                    "请在仪器软件 Instrument → Maintenance → Counters 中检查并执行。"
                ),
                "evidence": missing,
                "timestamp": ts,
                "instrument_id": instrument_id,
                "lab_id": lab_id,
            }
        )

    return {
        "rule_id": rule.id,
        "ok": True,
        "hit": bool(suggestions),
        "suggestions": suggestions,
        "counters_total": len(items),
        "expired_missing_count": len(missing),
        "checked_at": ts,
        "instrument_id": instrument_id,
        "lab_id": lab_id,
    }


def fetch_counters(bridge: BridgeClient) -> dict[str, Any]:
    data = bridge.get_json("/api/instrument/counters")
    if not isinstance(data, dict):
        return {"ok": False, "error": "counters response is not object", "items": []}
    return data


def evaluate_rules(
    bridge: BridgeClient,
    rules: Sequence[RuleDef],
    *,
    instrument_id: str = "",
    lab_id: str = "",
    rule_domains: Sequence[str] | None = None,
    rule_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """拉取必要端点并评估启用中的规则。"""
    started = time.time()
    domains = {str(d).strip() for d in (rule_domains or []) if str(d).strip()}
    ids = {str(i).strip() for i in (rule_ids or []) if str(i).strip()}

    selected = [
        r
        for r in rules
        if r.enabled
        and (not domains or r.domain in domains)
        and (not ids or r.id in ids)
    ]

    suggestions: list[dict[str, Any]] = []
    rule_results: list[dict[str, Any]] = []
    fetch_error = ""
    counters_payload: dict[str, Any] | None = None

    need_counters = any(r.id == RULE_DAILY_MAINTENANCE for r in selected)
    if need_counters:
        try:
            counters_payload = fetch_counters(bridge)
            if counters_payload.get("ok") is False and counters_payload.get("error"):
                fetch_error = str(counters_payload.get("error") or "counters fetch failed")
        except BridgeError as e:
            fetch_error = str(e)
            counters_payload = None

    for rule in selected:
        if rule.id == RULE_DAILY_MAINTENANCE:
            if counters_payload is None:
                rr = {
                    "rule_id": rule.id,
                    "ok": False,
                    "hit": False,
                    "suggestions": [],
                    "error": fetch_error or "counters unavailable",
                    "checked_at": utc_iso(),
                    "instrument_id": instrument_id,
                    "lab_id": lab_id,
                }
            else:
                rr = evaluate_daily_maintenance_counters(
                    counters_payload,
                    rule=rule,
                    instrument_id=instrument_id,
                    lab_id=lab_id,
                )
                if fetch_error and not rr.get("hit"):
                    rr["fetch_warning"] = fetch_error
            rule_results.append(rr)
            suggestions.extend(rr.get("suggestions") or [])
        else:
            rule_results.append(
                {
                    "rule_id": rule.id,
                    "ok": False,
                    "hit": False,
                    "suggestions": [],
                    "error": "unknown_rule",
                    "checked_at": utc_iso(),
                }
            )

    hit = any(r.get("hit") for r in rule_results)
    return {
        "ok": not fetch_error or bool(rule_results),
        "hit": hit,
        "suggestions": suggestions,
        "rule_results": rule_results,
        "rules_evaluated": [r.id for r in selected],
        "fetch_error": fetch_error,
        "checked_at": utc_iso(),
        "instrument_id": instrument_id,
        "lab_id": lab_id,
        "duration_ms": int((time.time() - started) * 1000),
    }
