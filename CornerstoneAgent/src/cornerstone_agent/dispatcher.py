from __future__ import annotations

from pathlib import Path
from typing import Any

from .bridge_client import BridgeClient
from .envelopes import TOOL_TO_JOB, make_job, make_result, strip_routing_fields
from .jobs import execute_job
from .registry import AgentRegistry

# A1 时序：编排侧读 SQLite，不经 Bridge；供 BaoClaw 对话查询
TIMESERIES_TOOLS = frozenset(
    {
        "get_timeseries_latest",
        "list_timeseries_metrics",
        "query_timeseries",
        "get_timeseries_sample",
    }
)


def _downsample_series(series: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    n = len(series)
    if n <= max_points or max_points < 2:
        return series
    out: list[dict[str, Any]] = []
    for i in range(max_points):
        idx = int(round(i * (n - 1) / (max_points - 1)))
        out.append(series[idx])
    return out


def _series_summary(series: list[dict[str, Any]]) -> dict[str, Any]:
    nums: list[float] = []
    for p in series:
        v = p.get("value")
        if isinstance(v, bool):
            nums.append(1.0 if v else 0.0)
        elif isinstance(v, (int, float)):
            nums.append(float(v))
    if not nums:
        return {"count": len(series), "numeric_count": 0}
    return {
        "count": len(series),
        "numeric_count": len(nums),
        "min": min(nums),
        "max": max(nums),
        "avg": sum(nums) / len(nums),
        "first": nums[0],
        "last": nums[-1],
        "unit": (series[-1].get("unit") if series else "") or "",
    }


class ToolDispatcher:
    """C1：注册表路由 + 本机 Bridge 同步执行 P0/P1 jobs；A1 时序本地查询。"""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        redact_sample_names: bool = True,
        default_timeout_s: int = 60,
        snapshot_dir: str | None = None,
        timeseries_store: Any = None,
        timeseries_cfg: Any = None,
        notice_audit: Any = None,
    ):
        self.registry = registry
        self.redact_sample_names = redact_sample_names
        self.default_timeout_s = default_timeout_s
        self.timeseries_store = timeseries_store
        self.timeseries_cfg = timeseries_cfg
        self.notice_audit = notice_audit
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

    def _require_registered(self, args: dict[str, Any]) -> tuple[Any | None, dict[str, Any] | None]:
        lab_id = str(args.get("lab_id") or "").strip()
        instrument_id = str(args.get("instrument_id") or "").strip()
        if not lab_id or not instrument_id:
            return None, {
                "code": "invalid_params",
                "message": "lab_id and instrument_id are required",
            }
        rec = self.registry.lookup(lab_id, instrument_id)
        if rec is None:
            return None, {
                "code": "instrument_not_found",
                "message": f"no agent registered for {lab_id}/{instrument_id}",
            }
        return rec, None

    def _jobs_public(self) -> list[dict[str, Any]]:
        cfg = self.timeseries_cfg
        if cfg is None:
            return []
        jobs = getattr(cfg, "jobs", None) or []
        out: list[dict[str, Any]] = []
        for j in jobs:
            if hasattr(j, "to_mapping"):
                out.append(j.to_mapping())
            elif isinstance(j, dict):
                out.append(j)
        return out

    def _dispatch_timeseries(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.timeseries_store is None:
            return {
                "tool": name,
                "ok": False,
                "error": {
                    "code": "timeseries_disabled",
                    "message": "timeseries store not enabled on this agent",
                },
            }
        rec, err = self._require_registered(args)
        if err:
            return {"tool": name, "ok": False, "error": err}
        assert rec is not None
        iid = rec.instrument_id
        job_id = str(args.get("job_id") or "").strip() or None
        store = self.timeseries_store

        if name == "get_timeseries_latest":
            gauges_only = bool(args.get("gauges_only", True))
            bundle = store.latest_values(iid, job_id=job_id)
            values = list(bundle.get("values") or [])
            if gauges_only:
                gvals = [v for v in values if str(v.get("metric") or "").startswith("gauge.")]
                if gvals:
                    values = gvals
            return {
                "tool": name,
                "ok": True,
                "result": {
                    "lab_id": rec.lab_id,
                    "instrument_id": iid,
                    "job_id": job_id,
                    "jobs": self._jobs_public(),
                    "sample": bundle.get("sample"),
                    "values": values,
                    "value_count": len(values),
                },
            }

        if name == "list_timeseries_metrics":
            hours = float(args.get("hours") or 24)
            metrics = store.list_metrics(iid, hours=hours, job_id=job_id)
            gauges_only = bool(args.get("gauges_only", False))
            if gauges_only:
                metrics = [m for m in metrics if m.startswith("gauge.")]
            return {
                "tool": name,
                "ok": True,
                "result": {
                    "lab_id": rec.lab_id,
                    "instrument_id": iid,
                    "hours": hours,
                    "job_id": job_id,
                    "jobs": self._jobs_public(),
                    "metrics": metrics,
                    "metric_count": len(metrics),
                },
            }

        if name == "get_timeseries_sample":
            sid_raw = args.get("sample_id")
            if sid_raw is None or str(sid_raw).strip() == "":
                return {
                    "tool": name,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": "sample_id is required"},
                }
            try:
                sid = int(sid_raw)
            except (TypeError, ValueError):
                return {
                    "tool": name,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": "sample_id must be an integer"},
                }
            bundle = store.sample_values(sid)
            sample = bundle.get("sample")
            if not sample:
                return {
                    "tool": name,
                    "ok": False,
                    "error": {"code": "sample_not_found", "message": f"sample_id={sid} not found"},
                }
            if str(sample.get("instrument_id") or "") != iid:
                return {
                    "tool": name,
                    "ok": False,
                    "error": {
                        "code": "sample_mismatch",
                        "message": f"sample {sid} belongs to {sample.get('instrument_id')}, not {iid}",
                    },
                }
            return {
                "tool": name,
                "ok": True,
                "result": {
                    "lab_id": rec.lab_id,
                    "instrument_id": iid,
                    "sample": sample,
                    "values": bundle.get("values") or [],
                    "value_count": len(bundle.get("values") or []),
                },
            }

        if name == "query_timeseries":
            hours = float(args.get("hours") or 24)
            metric = str(args.get("metric") or "").strip()
            sample_limit = max(1, min(int(args.get("sample_limit") or 10), 50))
            series_points = max(2, min(int(args.get("series_points") or 60), 200))
            stats = store.sample_stats(iid, hours=hours, job_id=job_id)
            samples = store.list_samples(iid, hours=hours, limit=sample_limit, job_id=job_id)
            metrics = store.list_metrics(iid, hours=hours, job_id=job_id)
            result: dict[str, Any] = {
                "lab_id": rec.lab_id,
                "instrument_id": iid,
                "hours": hours,
                "job_id": job_id,
                "jobs": self._jobs_public(),
                "stats": stats,
                "samples": samples,
                "metrics": metrics[:80],
                "metric_count": len(metrics),
            }
            if metric:
                full = store.query_series(
                    iid, metric, hours=hours, limit=5000, job_id=job_id
                )
                result["metric"] = metric
                result["series_summary"] = _series_summary(full)
                result["series"] = _downsample_series(full, series_points)
                result["series_raw_count"] = len(full)
            else:
                result["metric"] = None
                result["hint"] = "pass metric to get series_summary + downsampled series"
            return {"tool": name, "ok": True, "result": result}

        return {
            "tool": name,
            "ok": False,
            "error": {"code": "unknown_tool", "message": f"unknown timeseries tool: {name}"},
        }

    def dispatch_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        if name == "list_instruments":
            return {
                "tool": name,
                "ok": True,
                "result": self.list_instruments(args),
            }

        if name in TIMESERIES_TOOLS:
            return self._dispatch_timeseries(name, args)

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
            timeseries_store=self.timeseries_store,
            lab_id=rec.lab_id,
            notice_audit=self.notice_audit,
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
            timeseries_store=self.timeseries_store,
            lab_id=rec.lab_id,
            notice_audit=self.notice_audit,
        )
