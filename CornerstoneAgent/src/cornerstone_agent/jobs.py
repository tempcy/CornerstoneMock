from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional

from .bridge_client import BridgeClient, BridgeError
from .collect import ENDPOINT_SPECS, build_endpoint_queries, resolve_endpoint_aliases
from .envelopes import make_result, utc_iso
from .privacy import redact_value
from .snapshots import SnapshotStore, new_snapshot_id

Handler = Callable[[BridgeClient, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _status_summary(status: dict[str, Any]) -> dict[str, Any]:
    from .bridge_client import extract_bridge_version

    out: dict[str, Any] = {
        "bridge_ok": bool(status.get("ok", True)),
        "upstream_connected": bool(status.get("upstreamConnected")),
        "instrument_online": bool(status.get("instrumentOnline")),
        "business_online": bool(status.get("businessOnline")),
        "remote_control_state": status.get("remoteControlState"),
        "queue_count": status.get("queueCount"),
        "ready_hint": (
            "online_ok"
            if status.get("businessOnline") and status.get("upstreamConnected")
            else "check_connection"
        ),
    }
    # 可选字段：旧 Bridge 无 bridgeVersion 时不出现，不改变既有摘要语义
    bv = extract_bridge_version(status)
    if bv:
        out["bridge_version"] = bv
    return out


def handle_get_status(bridge: BridgeClient, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    status = bridge.get_json("/api/status")
    data: dict[str, Any] = {
        "summary": _status_summary(status if isinstance(status, dict) else {}),
        "status": status,
    }
    if params.get("include_status_check", True):
        try:
            data["status_check"] = bridge.get_json("/api/diagnostic/status-check")
        except BridgeError as e:
            data["status_check_error"] = {"message": str(e), "http_status": e.status}
    if params.get("include_system_parameters"):
        try:
            data["system_parameters"] = bridge.get_json("/api/instrument/system-parameters")
        except BridgeError as e:
            data["system_parameters_error"] = {"message": str(e), "http_status": e.status}
    return data


def handle_get_sets(bridge: BridgeClient, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    number = int(params.get("number") or 10)
    start_at = int(params.get("start_at") if params.get("start_at") is not None else -1)
    filter_key = str(params.get("filter_key") if params.get("filter_key") is not None else "0")
    raw = bridge.get_json(
        "/api/instrument/sets",
        {"number": number, "start_at": start_at, "filter_key": filter_key},
    )
    return {"sets": raw, "query": {"number": number, "start_at": start_at, "filter_key": filter_key}}


def handle_get_set_reps(bridge: BridgeClient, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    set_key = str(params.get("set_key") or "").strip()
    if not set_key:
        raise ValueError("set_key is required")
    include_detail = bool(params.get("include_detail", False))
    tag = int(params.get("tag") if params.get("tag") is not None else -1)
    reps = bridge.get_json(
        "/api/instrument/set-reps",
        {"set_key": set_key, "include_detail": str(include_detail).lower(), "tag": tag},
    )
    data: dict[str, Any] = {"set_key": set_key, "reps": reps}
    if params.get("include_stats", True):
        try:
            data["stats"] = bridge.get_json("/api/instrument/set-stats", {"set_key": set_key})
        except BridgeError as e:
            data["stats_error"] = {"message": str(e), "http_status": e.status}
    return data


def _fetch_endpoint(
    bridge: BridgeClient,
    alias: str,
    query: dict[str, Any],
) -> dict[str, Any]:
    path, _ = ENDPOINT_SPECS[alias]
    if alias in ("set-stats", "set-reps") and not str(query.get("set_key") or "").strip():
        return {
            "ok": False,
            "error": {
                "code": "missing_set_key",
                "message": f"{alias} needs set_key (pass params.set_key or include sets first)",
            },
        }
    try:
        payload = bridge.get_json(path, query or None)
        return {"ok": True, "path": path, "query": query, "data": payload}
    except BridgeError as e:
        return {
            "ok": False,
            "path": path,
            "query": query,
            "error": {
                "code": "bridge_unreachable" if e.status is None else "bridge_http_error",
                "message": str(e),
                "http_status": e.status,
                "body": e.body,
            },
        }


def _collect_pass(bridge: BridgeClient, aliases: list[str], params: dict[str, Any]) -> dict[str, Any]:
    """单次采集：先拉 sets（若需要），再拉其余 endpoint。"""
    need_sets_first = any(a in ("set-stats", "set-reps") for a in aliases) and "sets" not in aliases
    ordered = list(aliases)
    if need_sets_first:
        ordered = ["sets"] + [a for a in aliases if a != "sets"]

    endpoints: dict[str, Any] = {}
    sets_payload: Any = None

    # 第一轮：sets（若在列表中）
    if "sets" in ordered:
        q0 = build_endpoint_queries(["sets"], params)["sets"]
        ep = _fetch_endpoint(bridge, "sets", q0)
        endpoints["sets"] = ep
        if ep.get("ok"):
            sets_payload = ep.get("data")
        ordered = [a for a in ordered if a != "sets"]

    queries = build_endpoint_queries(ordered, params, sets_payload=sets_payload)
    for alias in ordered:
        endpoints[alias] = _fetch_endpoint(bridge, alias, queries[alias])

    # 保持调用方请求的别名集合（去掉仅为补 set_key 而临时加入的 sets）
    if need_sets_first and "sets" not in aliases:
        endpoints.pop("sets", None)

    return endpoints


def handle_collect(bridge: BridgeClient, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    profile = str(params.get("profile") or "").strip()
    aliases = resolve_endpoint_aliases(profile, params.get("endpoints"))
    duration_s = int(params.get("duration_s") or 0)
    if duration_s < 0 or duration_s > 600:
        raise ValueError("duration_s must be 0..600")

    sample_interval_s = int(params.get("sample_interval_s") or 10)
    if sample_interval_s < 5:
        sample_interval_s = 5
    if sample_interval_s > 120:
        sample_interval_s = 120

    snapshot_id = new_snapshot_id()
    captured_at = utc_iso()

    if duration_s <= 0:
        endpoints = _collect_pass(bridge, aliases, params)
        snapshot: dict[str, Any] = {
            "schema_version": "acquisition-snapshot.v1",
            "snapshot_id": snapshot_id,
            "profile": profile,
            "aliases": aliases,
            "captured_at": captured_at,
            "duration_s": 0,
            "endpoints": endpoints,
        }
    else:
        samples: list[dict[str, Any]] = []
        deadline = time.time() + float(duration_s)
        while True:
            ts = utc_iso()
            samples.append({"captured_at": ts, "endpoints": _collect_pass(bridge, aliases, params)})
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(float(sample_interval_s), max(0.0, remaining)))
        snapshot = {
            "schema_version": "acquisition-snapshot.v1",
            "snapshot_id": snapshot_id,
            "profile": profile,
            "aliases": aliases,
            "captured_at": captured_at,
            "duration_s": duration_s,
            "sample_interval_s": sample_interval_s,
            "sample_count": len(samples),
            "endpoints": samples[-1]["endpoints"] if samples else {},
            "samples": samples,
        }

    store: Optional[SnapshotStore] = ctx.get("snapshot_store")
    if store is not None:
        store.save(snapshot)
        snapshot["persisted"] = True
    else:
        snapshot["persisted"] = False

    ts_store = ctx.get("timeseries_store")
    if ts_store is not None and duration_s <= 0:
        try:
            from .metrics import extract_points

            pts = extract_points(snapshot.get("endpoints"))
            ts_store.insert_sample(
                instrument_id=str(ctx.get("instrument_id") or ""),
                lab_id=str(ctx.get("lab_id") or ""),
                agent_id=str(ctx.get("agent_id") or ""),
                ok=True,
                duration_ms=0,
                points=pts,
            )
            snapshot["timeseries_ingested"] = True
            snapshot["timeseries_points"] = len(pts)
        except Exception as exc:  # noqa: BLE001 — 时序失败不影响 collect 主路径
            snapshot["timeseries_ingested"] = False
            snapshot["timeseries_error"] = str(exc)

    return snapshot


def handle_operator_notice(
    bridge: BridgeClient, params: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """将建议推送到 Bridge 操作员对话框（只展示，不写仪器）。"""
    import uuid

    title = str(params.get("title") or "").strip()
    message = str(params.get("message") or "").strip()
    if not title and not message:
        raise ValueError("title or message is required")

    severity = str(params.get("severity") or "info").strip().lower()
    if severity not in ("info", "review", "reject", "maintain"):
        severity = "info"
    source = str(params.get("source") or "orchestrator").strip().lower()
    if source not in ("agent_rule", "zhibao", "orchestrator", "manual"):
        source = "orchestrator"

    notice_id = str(params.get("notice_id") or params.get("noticeId") or uuid.uuid4())
    payload: dict[str, Any] = {
        "notice_id": notice_id,
        "source": source,
        "severity": severity,
        "title": title or (message[:40] + ("…" if len(message) > 40 else "")),
        "message": message or title,
        "evidence": params.get("evidence") if isinstance(params.get("evidence"), list) else [],
        "actions": params.get("actions") if isinstance(params.get("actions"), list) else [],
        "require_ack": bool(params.get("require_ack", True)),
        "lab_id": str(ctx.get("lab_id") or params.get("lab_id") or ""),
        "instrument_id": str(ctx.get("instrument_id") or params.get("instrument_id") or ""),
        "agent_id": str(ctx.get("agent_id") or ""),
        "trace_id": str(params.get("trace_id") or ""),
    }
    if params.get("expires_at") is not None:
        payload["expires_at"] = params.get("expires_at")

    result = bridge.post_json("/api/operator-notices", payload)
    if not isinstance(result, dict) or not result.get("ok"):
        err = (result or {}).get("error") if isinstance(result, dict) else "bridge rejected notice"
        raise BridgeError(str(err), status=400, body=result)

    audit = ctx.get("notice_audit")
    if audit is not None:
        try:
            audit.record_sent(payload, bridge_result=result)
        except Exception:  # noqa: BLE001 — 审计失败不影响主路径
            pass

    return {
        "notice_id": notice_id,
        "bridge": result,
        "delivered": True,
        # 预留：本地规则引擎 A2 命中后可直接调用本 handler（同一管道）
        "channel": "bridge_operator_dialog",
    }


HANDLERS: dict[str, Handler] = {
    "get_status": handle_get_status,
    "get_sets": handle_get_sets,
    "get_set_reps": handle_get_set_reps,
    "collect": handle_collect,
    "operator_notice": handle_operator_notice,
}


def execute_job(
    job: dict[str, Any],
    *,
    bridge: BridgeClient,
    agent_id: str,
    instrument_id: str,
    redact_sample_names: bool = True,
    snapshot_dir: str | Path | None = None,
    timeseries_store: Any = None,
    lab_id: str = "",
    notice_audit: Any = None,
) -> dict[str, Any]:
    started = time.time()
    job_type = str(job.get("type") or "")
    params = job.get("params") if isinstance(job.get("params"), dict) else {}

    if job_type not in HANDLERS:
        return make_result(
            job,
            status="rejected",
            error={"code": "unknown_type", "message": f"unsupported job type: {job_type}"},
            agent_id=agent_id,
            instrument_id=instrument_id,
            meta={"duration_ms": int((time.time() - started) * 1000)},
        )

    ctx: dict[str, Any] = {
        "instrument_id": instrument_id,
        "agent_id": agent_id,
        "lab_id": lab_id,
    }
    if snapshot_dir:
        ctx["snapshot_store"] = SnapshotStore(snapshot_dir)
    if timeseries_store is not None:
        ctx["timeseries_store"] = timeseries_store
    if notice_audit is not None:
        ctx["notice_audit"] = notice_audit

    try:
        reachable = True
        data = HANDLERS[job_type](bridge, params, ctx)
        data = redact_value(data, enabled=redact_sample_names)
        return make_result(
            job,
            status="ok",
            data=data,
            agent_id=agent_id,
            instrument_id=instrument_id,
            bridge_reachable=reachable,
            meta={
                "duration_ms": int((time.time() - started) * 1000),
                "bridge_base_url": bridge.base_url,
                "redaction": "sample_names_hashed" if redact_sample_names else "none",
            },
        )
    except ValueError as e:
        return make_result(
            job,
            status="rejected",
            error={"code": "invalid_params", "message": str(e)},
            agent_id=agent_id,
            instrument_id=instrument_id,
            meta={"duration_ms": int((time.time() - started) * 1000)},
        )
    except BridgeError as e:
        code = "bridge_unreachable" if e.status is None else "bridge_http_error"
        return make_result(
            job,
            status="error",
            error={"code": code, "message": str(e), "http_status": e.status, "body": e.body},
            agent_id=agent_id,
            instrument_id=instrument_id,
            bridge_reachable=e.status is not None,
            meta={
                "duration_ms": int((time.time() - started) * 1000),
                "bridge_base_url": bridge.base_url,
            },
        )
