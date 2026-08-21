from __future__ import annotations

from pathlib import Path
from typing import Any

from .bridge_client import BridgeClient
from .envelopes import TOOL_TO_JOB, make_job, make_result, strip_routing_fields
from .jobs import execute_job
from .registry import AgentRegistry


class ToolDispatcher:
    """C1：注册表路由 + 本机 Bridge 同步执行 P0/P1 jobs。"""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        redact_sample_names: bool = True,
        default_timeout_s: int = 60,
        snapshot_dir: str | None = None,
    ):
        self.registry = registry
        self.redact_sample_names = redact_sample_names
        self.default_timeout_s = default_timeout_s
        if snapshot_dir:
            self.snapshot_dir = snapshot_dir
        else:
            # 与注册表同目录下的 acquisition_snapshots/
            self.snapshot_dir = str(Path(registry.path).resolve().parent / "acquisition_snapshots")

    def list_instruments(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        lab_id = (args.get("lab_id") or None) or None
        if lab_id is not None:
            lab_id = str(lab_id).strip() or None
        online_only = bool(args.get("online_only", True))
        return {"instruments": self.registry.list_instruments(lab_id=lab_id, online_only=online_only)}

    def dispatch_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        if name == "list_instruments":
            return {
                "tool": name,
                "ok": True,
                "result": self.list_instruments(args),
            }

        job_type = TOOL_TO_JOB.get(name)
        if not job_type:
            return {
                "tool": name,
                "ok": False,
                "error": {"code": "unknown_tool", "message": f"unknown tool: {name}"},
            }

        lab_id = str(args.get("lab_id") or "").strip()
        instrument_id = str(args.get("instrument_id") or "").strip()
        if not lab_id or not instrument_id:
            return {
                "tool": name,
                "ok": False,
                "error": {
                    "code": "invalid_params",
                    "message": "lab_id and instrument_id are required",
                },
            }

        rec = self.registry.lookup(lab_id, instrument_id)
        if rec is None:
            return {
                "tool": name,
                "ok": False,
                "error": {
                    "code": "instrument_not_found",
                    "message": f"no agent registered for {lab_id}/{instrument_id}",
                },
            }
        if not self.registry.is_online(rec):
            return {
                "tool": name,
                "ok": False,
                "error": {
                    "code": "agent_offline",
                    "message": f"agent {rec.agent_id} offline (heartbeat TTL)",
                    "agent_id": rec.agent_id,
                },
            }
        if rec.capabilities and job_type not in rec.capabilities:
            return {
                "tool": name,
                "ok": False,
                "error": {
                    "code": "capability_missing",
                    "message": f"agent lacks capability {job_type}",
                },
            }

        timeout_s = int(args.get("timeout_s") or self.default_timeout_s)
        if job_type == "collect" and args.get("timeout_s") is None:
            timeout_s = max(timeout_s, 120)
        job = make_job(job_type, strip_routing_fields(args), timeout_s=timeout_s)
        bridge_url = rec.bridge_url or "http://127.0.0.1:8081"
        bridge = BridgeClient(bridge_url, timeout_s=float(timeout_s))
        result = execute_job(
            job,
            bridge=bridge,
            agent_id=rec.agent_id,
            instrument_id=rec.instrument_id,
            redact_sample_names=self.redact_sample_names,
            snapshot_dir=self.snapshot_dir,
        )
        return {
            "tool": name,
            "ok": result.get("status") == "ok",
            "job": {"job_id": job["job_id"], "type": job_type},
            "result": result,
        }

    def dispatch_job_raw(
        self,
        job: dict[str, Any],
        *,
        lab_id: str,
        instrument_id: str,
    ) -> dict[str, Any]:
        rec = self.registry.lookup(lab_id, instrument_id)
        if rec is None:
            return make_result(
                job,
                status="rejected",
                error={"code": "instrument_not_found", "message": "not registered"},
            )
        bridge = BridgeClient(rec.bridge_url or "http://127.0.0.1:8081", timeout_s=float(job.get("timeout_s") or 60))
        return execute_job(
            job,
            bridge=bridge,
            agent_id=rec.agent_id,
            instrument_id=rec.instrument_id,
            redact_sample_names=self.redact_sample_names,
            snapshot_dir=self.snapshot_dir,
        )
