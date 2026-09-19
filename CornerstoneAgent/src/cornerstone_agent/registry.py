from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class AgentRecord:
    agent_id: str
    org_id: str
    lab_id: str
    instrument_id: str
    bridge_url: str = ""
    capabilities: list[str] = field(default_factory=list)
    bridge_reachable: bool = False
    last_seen: str = ""
    versions: dict[str, str] = field(default_factory=dict)
    last_seen_epoch: float = 0.0

    def to_public(self, *, online: bool) -> dict[str, Any]:
        return {
            "lab_id": self.lab_id,
            "instrument_id": self.instrument_id,
            "agent_id": self.agent_id,
            "org_id": self.org_id,
            "online": online,
            "last_seen": self.last_seen,
            "bridge_reachable": self.bridge_reachable,
            "capabilities": list(self.capabilities),
            "bridge_url": self.bridge_url,
            "versions": dict(self.versions),
        }

    def to_stored(self) -> dict[str, Any]:
        d = self.to_public(online=False)
        d["last_seen_epoch"] = self.last_seen_epoch
        return d

    @classmethod
    def from_stored(cls, data: dict[str, Any]) -> "AgentRecord":
        return cls(
            agent_id=str(data.get("agent_id") or ""),
            org_id=str(data.get("org_id") or ""),
            lab_id=str(data.get("lab_id") or ""),
            instrument_id=str(data.get("instrument_id") or ""),
            bridge_url=str(data.get("bridge_url") or ""),
            capabilities=list(data.get("capabilities") or []),
            bridge_reachable=bool(data.get("bridge_reachable")),
            last_seen=str(data.get("last_seen") or ""),
            versions=dict(data.get("versions") or {}),
            last_seen_epoch=float(data.get("last_seen_epoch") or 0),
        )


DEFAULT_CAPABILITIES = [
    "get_status",
    "get_sets",
    "get_set_reps",
    "collect",
    "operator_notice",
    "rules_eval",
    "log_tail",
]


class AgentRegistry:
    """进程内注册表 + JSON 落盘。"""

    def __init__(self, path: str | Path, online_ttl_s: float = 90.0):
        self.path = Path(path)
        self.online_ttl_s = float(online_ttl_s)
        self._lock = threading.RLock()
        self._by_agent: dict[str, AgentRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        agents = raw.get("agents") if isinstance(raw, dict) else None
        if not isinstance(agents, list):
            return
        for item in agents:
            if not isinstance(item, dict):
                continue
            rec = AgentRecord.from_stored(item)
            if rec.agent_id:
                self._by_agent[rec.agent_id] = rec

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "agent-registry.v1",
            "updated_at": _utc_iso(),
            "agents": [r.to_stored() for r in self._by_agent.values()],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def register(
        self,
        *,
        agent_id: str | None = None,
        org_id: str,
        lab_id: str,
        instrument_id: str,
        bridge_url: str = "",
        capabilities: list[str] | None = None,
        bridge_reachable: bool = False,
        versions: dict[str, str] | None = None,
    ) -> AgentRecord:
        with self._lock:
            aid = (agent_id or "").strip() or f"agent-{uuid.uuid4().hex[:12]}"
            now = time.time()
            rec = AgentRecord(
                agent_id=aid,
                org_id=org_id,
                lab_id=lab_id,
                instrument_id=instrument_id,
                bridge_url=bridge_url.rstrip("/"),
                capabilities=list(capabilities or DEFAULT_CAPABILITIES),
                bridge_reachable=bridge_reachable,
                last_seen=_utc_iso(),
                versions=dict(versions or {}),
                last_seen_epoch=now,
            )
            # 同 lab+instrument 替换旧实例
            stale = [
                k
                for k, v in self._by_agent.items()
                if v.lab_id == lab_id and v.instrument_id == instrument_id and k != aid
            ]
            for k in stale:
                del self._by_agent[k]
            self._by_agent[aid] = rec
            self._save_unlocked()
            return rec

    def heartbeat(
        self,
        agent_id: str,
        *,
        bridge_reachable: bool | None = None,
        capabilities: list[str] | None = None,
        versions: dict[str, str] | None = None,
    ) -> AgentRecord | None:
        with self._lock:
            rec = self._by_agent.get(agent_id)
            if rec is None:
                return None
            rec.last_seen = _utc_iso()
            rec.last_seen_epoch = time.time()
            if bridge_reachable is not None:
                rec.bridge_reachable = bool(bridge_reachable)
            if capabilities is not None:
                rec.capabilities = list(capabilities)
            if versions is not None:
                rec.versions = dict(versions)
            self._save_unlocked()
            return rec

    def is_online(self, rec: AgentRecord, now: float | None = None) -> bool:
        t = time.time() if now is None else now
        return (t - rec.last_seen_epoch) <= self.online_ttl_s

    def lookup(self, lab_id: str, instrument_id: str) -> AgentRecord | None:
        with self._lock:
            for rec in self._by_agent.values():
                if rec.lab_id == lab_id and rec.instrument_id == instrument_id:
                    return rec
            return None

    def get(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            return self._by_agent.get(agent_id)

    def list_instruments(
        self,
        *,
        lab_id: str | None = None,
        online_only: bool = True,
    ) -> list[dict[str, Any]]:
        now = time.time()
        out: list[dict[str, Any]] = []
        with self._lock:
            for rec in self._by_agent.values():
                if lab_id and rec.lab_id != lab_id:
                    continue
                online = self.is_online(rec, now)
                if online_only and not online:
                    continue
                out.append(rec.to_public(online=online))
        out.sort(key=lambda x: (x.get("lab_id") or "", x.get("instrument_id") or ""))
        return out
