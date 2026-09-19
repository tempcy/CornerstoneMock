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
    return {
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

    return snapshot


HANDLERS: dict[str, Handler] = {
    "get_status": handle_get_status,
    "get_sets": handle_get_sets,
    "get_set_reps": handle_get_set_reps,
    "collect": handle_collect,
}


def execute_job(
    job: dict[str, Any],
    *,
    bridge: BridgeClient,
    agent_id: str,
    instrument_id: str,
    redact_sample_names: bool = True,
    snapshot_dir: str | Path | None = None,
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

    ctx: dict[str, Any] = {}
    if snapshot_dir:
        ctx["snapshot_store"] = SnapshotStore(snapshot_dir)

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
