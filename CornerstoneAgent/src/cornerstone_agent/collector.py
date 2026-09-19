"""A1 长周期采集：单次 tick + 按任务独立调度。"""

from __future__ import annotations

import threading
import time
from typing import Any, Sequence

from .bridge_client import BridgeClient, BridgeError
from .collect import resolve_endpoint_aliases
from .config import (
    DEFAULT_TIMESERIES_ENDPOINTS,
    AgentConfig,
    TimeseriesJobConfig,
)
from .jobs import _collect_pass
from .metrics import extract_points
from .timeseries import TimeseriesStore
from .ui_api import resolve_bridge_url


def collect_once(
    *,
    bridge: BridgeClient,
    store: TimeseriesStore,
    instrument_id: str,
    lab_id: str = "",
    agent_id: str = "",
    endpoints: Sequence[str] | None = None,
    job_id: str = "",
) -> dict[str, Any]:
    aliases = list(endpoints or DEFAULT_TIMESERIES_ENDPOINTS)
    # 复用 collect 白名单校验（custom + 显式列表）
    aliases = resolve_endpoint_aliases("custom", aliases)
    started = time.time()
    error = ""
    ok = True
    endpoints_payload: dict[str, Any] = {}
    try:
        endpoints_payload = _collect_pass(bridge, aliases, {})
    except BridgeError as e:
        ok = False
        error = str(e)
    except Exception as e:  # noqa: BLE001
        ok = False
        error = str(e)

    points = extract_points(endpoints_payload) if endpoints_payload else []
    if ok:
        # 全部 alias 失败仍记为 fail
        if endpoints_payload and not any(
            isinstance(v, dict) and v.get("ok") for v in endpoints_payload.values()
        ):
            ok = False
            error = error or "all endpoints failed"

    duration_ms = int((time.time() - started) * 1000)
    sample_id = store.insert_sample(
        instrument_id=instrument_id,
        lab_id=lab_id,
        agent_id=agent_id,
        job_id=job_id,
        ok=ok,
        error=error,
        duration_ms=duration_ms,
        points=points,
    )
    return {
        "ok": ok,
        "sample_id": sample_id,
        "instrument_id": instrument_id,
        "job_id": job_id,
        "point_count": len(points),
        "duration_ms": duration_ms,
        "error": error,
        "aliases": aliases,
    }


class TimeseriesScheduler:
    """每个采集任务独立线程，按各自 interval / 仪器范围轮询。"""

    def __init__(self, cfg: AgentConfig, store: TimeseriesStore, registry=None):
        self.cfg = cfg
        self.store = store
        self.registry = registry
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.last_tick: dict[str, Any] = {}
        self.last_ticks: dict[str, dict[str, Any]] = {}
        self._tick_lock = threading.Lock()

    def start(self) -> None:
        self._stop = threading.Event()
        self._threads = []
        jobs = [(j.id, j.retention_days) for j in self.cfg.timeseries.enabled_jobs()]
        if jobs:
            n = self.store.prune_jobs(jobs, orphan_days=30)
            if n:
                print(f"[ts] startup prune {n} expired samples")
        for job in self.cfg.timeseries.enabled_jobs():
            t = threading.Thread(
                target=self._loop_job,
                args=(job.id,),
                name=f"timeseries-{job.id}",
                daemon=True,
            )
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop.set()
        threads = list(self._threads)
        self._threads = []
        for t in threads:
            t.join(timeout=2.0)

    def reload(self, cfg: AgentConfig) -> None:
        self.stop()
        self.cfg = cfg
        self.start()

    def _loop_job(self, job_id: str) -> None:
        job = self.cfg.timeseries.job_by_id(job_id)
        if job is None:
            return
        self._tick_job(job)
        interval = max(5.0, float(job.interval_s))
        next_due = time.time() + interval
        while not self._stop.is_set():
            remain = next_due - time.time()
            if remain > 0:
                if self._stop.wait(min(0.5, remain)):
                    break
                continue
            started = time.time()
            job = self.cfg.timeseries.job_by_id(job_id)
            if job is None or not job.enabled:
                break
            self._tick_job(job)
            interval = max(5.0, float(job.interval_s))
            next_due = started + interval
            if next_due < time.time():
                next_due = time.time()

    def _tick_job(self, job: TimeseriesJobConfig) -> None:
        try:
            self._tick_job_inner(job)
        except Exception as exc:  # noqa: BLE001
            print(f"[ts] job {job.id} tick failed: {exc}")

    def _tick_job_inner(self, job: TimeseriesJobConfig) -> None:
        cfg = self.cfg
        ts_cfg = cfg.timeseries
        pruned = self.store.prune(job.retention_days, job_id=job.id)
        if pruned:
            print(
                f"[ts] job {job.id} pruned {pruned} samples older than {job.retention_days}d"
            )
        timeout = min(float(ts_cfg.timeout_s), max(8.0, float(job.interval_s)))
        targets = ts_cfg.select_instruments(job, cfg.local_instruments())
        results: list[dict[str, Any]] = []
        for ep in targets:
            if self._stop.is_set():
                break
            url = ep.bridge_url
            if self.registry is not None:
                resolved = resolve_bridge_url(cfg, self.registry, ep.instrument_id)
                if resolved:
                    url = resolved
            bridge = BridgeClient(url, timeout_s=timeout)
            out = collect_once(
                bridge=bridge,
                store=self.store,
                instrument_id=ep.instrument_id,
                lab_id=ep.lab_id,
                agent_id=ep.agent_id,
                endpoints=job.endpoints,
                job_id=job.id,
            )
            results.append(out)
            print(
                f"[ts] {job.id}/{ep.instrument_id} ok={out['ok']} "
                f"points={out['point_count']} {out['duration_ms']}ms"
                + (f" err={out['error']}" if out.get("error") else "")
            )
        summary = {
            "job_id": job.id,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "instruments": len(results),
            "ok": sum(1 for r in results if r.get("ok")),
            "interval_s": job.interval_s,
            "results": results,
        }
        with self._tick_lock:
            self.last_ticks[job.id] = summary
            self.last_tick = summary
