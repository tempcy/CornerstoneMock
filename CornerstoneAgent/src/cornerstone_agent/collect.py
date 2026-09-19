"""P1 collect：profile → Bridge 只读 endpoint 白名单。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# endpoint 别名 → (path, 默认 query)
ENDPOINT_SPECS: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "status": ("/api/status", {}),
    "system-parameters": ("/api/instrument/system-parameters", {}),
    "counters": ("/api/instrument/counters", {}),
    "automation-status": ("/api/instrument/automation-status", {}),
    "instrument-info": ("/api/instrument/instrument-info", {}),
    "status-check": ("/api/diagnostic/status-check", {}),
    "digital-io": ("/api/diagnostic/digital-io", {}),
    "ambients": ("/api/environment/ambients", {}),
    "sets": ("/api/instrument/sets", {"number": 10, "start_at": -1, "filter_key": "0"}),
    "set-stats": ("/api/instrument/set-stats", {}),
    "set-reps": ("/api/instrument/set-reps", {"include_detail": "false", "tag": -1}),
    "queue": ("/api/queue", {}),
}

PROFILE_ENDPOINTS: Dict[str, List[str]] = {
    "status_light": ["status", "status-check"],
    "analysis_recent": ["status", "sets", "set-stats"],
    "troubleshoot": [
        "status",
        "system-parameters",
        "status-check",
        "counters",
        "set-stats",
    ],
    "custom": [],
}

# 需要 set_key 的别名
_SET_KEY_ENDPOINTS = frozenset({"set-stats", "set-reps"})


def resolve_endpoint_aliases(profile: str, endpoints: Optional[Sequence[str]]) -> List[str]:
    profile = (profile or "").strip()
    if profile not in PROFILE_ENDPOINTS:
        raise ValueError(
            f"invalid profile '{profile}'; "
            f"expected one of {sorted(PROFILE_ENDPOINTS)}"
        )
    if profile == "custom":
        aliases = [str(x).strip() for x in (endpoints or []) if str(x).strip()]
        if not aliases:
            raise ValueError("profile=custom requires non-empty endpoints[]")
    else:
        aliases = list(PROFILE_ENDPOINTS[profile])

    unknown = [a for a in aliases if a not in ENDPOINT_SPECS]
    if unknown:
        raise ValueError(
            f"unknown endpoint aliases: {unknown}; "
            f"allowed={sorted(ENDPOINT_SPECS)}"
        )
    # 去重且保序
    seen = set()
    out: List[str] = []
    for a in aliases:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _first_set_key(sets_payload: Any) -> Optional[str]:
    if not isinstance(sets_payload, dict):
        return None
    items = sets_payload.get("items")
    if not isinstance(items, list):
        items = sets_payload.get("sets")
    if not isinstance(items, list):
        return None
    for row in items:
        if not isinstance(row, dict):
            continue
        for k in ("setKey", "set_key", "Key", "key"):
            v = row.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def build_endpoint_queries(
    aliases: Sequence[str],
    params: Mapping[str, Any],
    *,
    sets_payload: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """为每个别名生成 query；set-stats/set-reps 补 set_key。"""
    set_key = str(params.get("set_key") or "").strip() or None
    if set_key is None and sets_payload is not None:
        set_key = _first_set_key(sets_payload)

    queries: Dict[str, Dict[str, Any]] = {}
    for alias in aliases:
        path, base_q = ENDPOINT_SPECS[alias]
        q = dict(base_q)
        if alias == "sets":
            if params.get("number") is not None:
                q["number"] = int(params["number"])
            if params.get("start_at") is not None:
                q["start_at"] = int(params["start_at"])
            if params.get("filter_key") is not None:
                q["filter_key"] = str(params["filter_key"])
        if alias in _SET_KEY_ENDPOINTS:
            if not set_key:
                # 调用方应先拉 sets；仍缺则标记为空 query，由 fetch 层记 error
                q["set_key"] = ""
            else:
                q["set_key"] = set_key
            if alias == "set-reps":
                if params.get("include_detail") is not None:
                    q["include_detail"] = str(bool(params["include_detail"])).lower()
                if params.get("tag") is not None:
                    q["tag"] = int(params["tag"])
        queries[alias] = q
        _ = path  # path used by caller via ENDPOINT_SPECS
    return queries
