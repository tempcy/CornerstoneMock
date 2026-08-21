from __future__ import annotations

import time
import uuid
from typing import Any


SCHEMA_JOB = "agent-job.v1"
SCHEMA_RESULT = "agent-result.v1"
SCHEMA_UPLINK = "agent-uplink.v1"

ROUTING_FIELDS = frozenset({"lab_id", "instrument_id", "timeout_s"})

TOOL_TO_JOB = {
    "get_instrument_status": "get_status",
    "get_analysis_sets": "get_sets",
    "get_set_reps": "get_set_reps",
    "collect_instrument": "collect",
    "eval_instrument_rules": "rules_eval",
    "tail_instrument_logs": "log_tail",
    "inspect_instrument_ui": "ui_inspect",
}


def utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_job_id() -> str:
    return str(uuid.uuid4())


def strip_routing_fields(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in ROUTING_FIELDS}


def make_job(
    job_type: str,
    params: dict[str, Any] | None = None,
    *,
    job_id: str | None = None,
    timeout_s: int = 60,
    trace_id: str | None = None,
    reply_to: str | None = None,
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "schema_version": SCHEMA_JOB,
        "job_id": job_id or new_job_id(),
        "type": job_type,
        "created_at": utc_iso(),
        "timeout_s": int(timeout_s),
        "params": dict(params or {}),
    }
    if trace_id:
        job["trace_id"] = trace_id
    if reply_to:
        job["reply_to"] = reply_to
    return job


def make_result(
    job: dict[str, Any],
    *,
    status: str,
    data: Any = None,
    error: dict[str, Any] | None = None,
    agent_id: str = "",
    instrument_id: str = "",
    bridge_reachable: bool | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": SCHEMA_RESULT,
        "job_id": job.get("job_id"),
        "type": job.get("type"),
        "status": status,
        "finished_at": utc_iso(),
        "agent_id": agent_id,
        "instrument_id": instrument_id,
        "error": error,
        "data": data if data is not None else {},
        "meta": dict(meta or {}),
    }
    if bridge_reachable is not None:
        out["bridge_reachable"] = bridge_reachable
    return out


def make_uplink(
    msg_type: str,
    *,
    agent_id: str,
    org_id: str,
    lab_id: str,
    instrument_id: str,
    bridge_reachable: bool = False,
    capabilities: list[str] | None = None,
    versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_UPLINK,
        "msg_type": msg_type,
        "agent_id": agent_id,
        "org_id": org_id,
        "lab_id": lab_id,
        "instrument_id": instrument_id,
        "ts": utc_iso(),
        "bridge_reachable": bridge_reachable,
        "capabilities": list(capabilities or []),
        "versions": dict(versions or {}),
    }
