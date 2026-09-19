"""Agent 运维台 API：overview / config 只读 / Bridge ping。"""

from __future__ import annotations

import time
from typing import Any

from . import __version__
from .bridge_client import BridgeClient
from .collect import list_query_catalog
from .config import AgentConfig, timeseries_to_mapping
from .registry import AgentRegistry


def _level(*, online: int, total: int, bridge_ok: int) -> str:
    if total <= 0:
        return "warn"
    if online <= 0:
        return "error"
    if bridge_ok < total or online < total:
        return "warn"
    return "ok"


def build_overview(
    cfg: AgentConfig,
    registry: AgentRegistry,
    *,
    started_at: float,
    timeseries_store: Any = None,
    timeseries_last_tick: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    configured = {
        ep.instrument_id: ep
        for ep in cfg.local_instruments()
        if ep.instrument_id
    }
    # 注册表优先展示；配置中有但未注册的也列出
    by_id: dict[str, dict[str, Any]] = {}
    for rec in registry.list_instruments(lab_id=None, online_only=False):
        iid = str(rec.get("instrument_id") or "")
        if not iid:
            continue
        by_id[iid] = dict(rec)

    for iid, ep in configured.items():
        if iid in by_id:
            row = by_id[iid]
            if not row.get("bridge_url"):
                row["bridge_url"] = ep.bridge_url
            row.setdefault("agent_id", ep.agent_id)
            row.setdefault("lab_id", ep.lab_id)
            row.setdefault("org_id", ep.org_id)
            row["configured"] = True
        else:
            by_id[iid] = {
                "lab_id": ep.lab_id,
                "instrument_id": iid,
                "agent_id": ep.agent_id,
                "org_id": ep.org_id,
                "online": False,
                "last_seen": "",
                "bridge_reachable": False,
                "capabilities": [],
                "bridge_url": ep.bridge_url,
                "versions": {},
                "configured": True,
            }

    instruments = sorted(by_id.values(), key=lambda r: str(r.get("instrument_id") or ""))
    for row in instruments:
        row.setdefault("configured", row.get("instrument_id") in configured)
        iid = str(row.get("instrument_id") or "")
        if timeseries_store is not None and iid:
            latest = timeseries_store.latest_sample(iid)
            if latest:
                row["timeseries_last_ts"] = latest.get("ts")
                row["timeseries_last_ok"] = bool(latest.get("ok"))
                row["timeseries_last_points"] = latest.get("point_count")
            else:
                row["timeseries_last_ts"] = ""
                row["timeseries_last_ok"] = None
                row["timeseries_last_points"] = 0

    total = len(instruments)
    online = sum(1 for r in instruments if r.get("online"))
    bridge_ok = sum(1 for r in instruments if r.get("bridge_reachable"))
    level = _level(online=online, total=total, bridge_ok=bridge_ok)

    return {
        "ok": True,
        "orchestrator": {
            "service": "cornerstone-agent-orchestrator",
            "version": __version__,
            "listen_host": cfg.orchestrator.listen_host,
            "listen_port": cfg.orchestrator.listen_port,
            "listen": f"{cfg.orchestrator.listen_host}:{cfg.orchestrator.listen_port}",
            "config_path": cfg.config_path,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
            "uptime_s": max(0, int(now - started_at)),
            "heartbeat_interval_s": cfg.orchestrator.heartbeat_interval_s,
            "online_ttl_s": cfg.orchestrator.online_ttl_s,
            "embed_local_agent": cfg.orchestrator.embed_local_agent,
            "registry_path": cfg.orchestrator.registry_path,
            "snapshot_dir": cfg.orchestrator.snapshot_dir,
            "timeseries": {
                "enabled": cfg.timeseries.enabled,
                "interval_s": cfg.timeseries.interval_s,
                "retention_days": cfg.timeseries.retention_days,
                "db_path": cfg.timeseries.db_path,
                "jobs": [j.to_mapping() for j in cfg.timeseries.jobs],
                "last_tick": timeseries_last_tick or {},
            },
        },
        "identity": {
            "org_id": cfg.identity.org_id,
            "lab_id": cfg.identity.lab_id,
            "instrument_id": cfg.identity.instrument_id,
            "agent_id": cfg.identity.agent_id,
        },
        "summary": {
            "instruments_total": total,
            "online": online,
            "bridge_ok": bridge_ok,
            "level": level,
        },
        "instruments": instruments,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }


def build_config_view(cfg: AgentConfig) -> dict[str, Any]:
    """只读配置视图（当前无密钥字段）。"""
    instruments = [
        {
            "instrument_id": ep.instrument_id,
            "agent_id": ep.agent_id,
            "lab_id": ep.lab_id,
            "org_id": ep.org_id,
            "bridge_url": ep.bridge_url,
        }
        for ep in cfg.local_instruments()
    ]
    return {
        "ok": True,
        "read_only": False,
        "config_path": cfg.config_path,
        "identity": {
            "org_id": cfg.identity.org_id,
            "lab_id": cfg.identity.lab_id,
            "instrument_id": cfg.identity.instrument_id,
            "agent_id": cfg.identity.agent_id,
        },
        "bridge": {
            "base_url": cfg.bridge.base_url,
            "timeout_s": cfg.bridge.timeout_s,
        },
        "instruments": instruments,
        "orchestrator": {
            "listen_host": cfg.orchestrator.listen_host,
            "listen_port": cfg.orchestrator.listen_port,
            "registry_path": cfg.orchestrator.registry_path,
            "snapshot_dir": cfg.orchestrator.snapshot_dir,
            "heartbeat_interval_s": cfg.orchestrator.heartbeat_interval_s,
            "online_ttl_s": cfg.orchestrator.online_ttl_s,
            "embed_local_agent": cfg.orchestrator.embed_local_agent,
        },
        "timeseries": timeseries_to_mapping(cfg.timeseries),
        "privacy": {
            "redact_sample_names": cfg.privacy.redact_sample_names,
        },
        "catalog": list_query_catalog(),
    }


def resolve_bridge_url(
    cfg: AgentConfig,
    registry: AgentRegistry,
    instrument_id: str,
) -> str:
    iid = (instrument_id or "").strip()
    if not iid:
        return ""
    rec = registry.lookup(cfg.identity.lab_id, iid)
    if rec is None:
        # 跨 lab 兜底：按 instrument_id 扫注册表
        for row in registry.list_instruments(online_only=False):
            if str(row.get("instrument_id") or "") == iid:
                url = str(row.get("bridge_url") or "").strip()
                if url:
                    return url.rstrip("/")
                break
    else:
        if rec.bridge_url:
            return rec.bridge_url.rstrip("/")
    for ep in cfg.local_instruments():
        if ep.instrument_id == iid:
            return ep.bridge_url.rstrip("/")
    return ""


def ping_instrument(
    cfg: AgentConfig,
    registry: AgentRegistry,
    instrument_id: str,
) -> dict[str, Any]:
    url = resolve_bridge_url(cfg, registry, instrument_id)
    if not url:
        return {
            "ok": False,
            "error": "instrument_not_found",
            "instrument_id": instrument_id,
            "message": "no bridge_url for instrument",
        }
    from .bridge_client import extract_bridge_version

    client = BridgeClient(url, timeout_s=min(10.0, cfg.bridge.timeout_s))
    reachable, status = client.probe_status()
    bv = extract_bridge_version(status)
    agent_public: dict[str, Any] | None = None
    iid = instrument_id.strip()
    for row in registry.list_instruments(online_only=False):
        if str(row.get("instrument_id") or "") != iid:
            continue
        aid = str(row.get("agent_id") or "")
        if aid:
            versions = None
            if bv:
                # 合并写入 bridge，保留已有 agent 等字段
                existing = registry.get(aid)
                merged = dict(existing.versions) if existing else {}
                merged["bridge"] = bv
                versions = merged
            rec = registry.heartbeat(aid, bridge_reachable=reachable, versions=versions)
            if rec is not None:
                agent_public = rec.to_public(online=registry.is_online(rec))
        break
    out: dict[str, Any] = {
        "ok": True,
        "instrument_id": iid,
        "bridge_url": url,
        "bridge_reachable": reachable,
        "agent": agent_public,
    }
    if bv:
        out["bridge_version"] = bv
    return out
