from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _as_dict(v: Any, default: dict | None = None) -> dict:
    return dict(v) if isinstance(v, dict) else (default or {})


@dataclass
class IdentityConfig:
    org_id: str = "acme"
    lab_id: str = "lab-local-01"
    instrument_id: str = "CS-LOCAL-01"
    agent_id: str = ""

    def ensure_agent_id(self) -> str:
        if not self.agent_id:
            self.agent_id = f"agent-{uuid.uuid4().hex[:12]}"
        return self.agent_id


@dataclass
class BridgeConfig:
    base_url: str = "http://127.0.0.1:8081"
    timeout_s: float = 60.0


@dataclass
class InstrumentEndpoint:
    """同机编排下的一台仪器（注册表一条 + 独立 Bridge URL）。"""

    instrument_id: str
    bridge_url: str
    agent_id: str = ""
    lab_id: str = ""
    org_id: str = ""

    def resolve(
        self,
        *,
        default_org: str,
        default_lab: str,
    ) -> "InstrumentEndpoint":
        lab = (self.lab_id or default_lab).strip()
        org = (self.org_id or default_org).strip()
        iid = self.instrument_id.strip()
        aid = (self.agent_id or "").strip()
        if not aid and lab and iid:
            # agent-2lg-gc8 风格：lab 去掉 lab- 前缀
            lab_slug = lab[4:] if lab.startswith("lab-") else lab
            aid = f"agent-{lab_slug}-{iid.lower()}"
        return InstrumentEndpoint(
            instrument_id=iid,
            bridge_url=self.bridge_url.rstrip("/"),
            agent_id=aid,
            lab_id=lab,
            org_id=org,
        )


@dataclass
class OrchestratorConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8090
    registry_path: str = "agent_registry.json"
    snapshot_dir: str = "acquisition_snapshots"
    heartbeat_interval_s: float = 30.0
    online_ttl_s: float = 90.0
    embed_local_agent: bool = True


@dataclass
class PrivacyConfig:
    redact_sample_names: bool = True


@dataclass
class AgentConfig:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    instruments: list[InstrumentEndpoint] = field(default_factory=list)
    config_path: str = ""

    def local_instruments(self) -> list[InstrumentEndpoint]:
        """嵌入式注册/心跳目标：优先 ``instruments[]``，否则 identity+bridge。"""
        if self.instruments:
            return [
                ep.resolve(default_org=self.identity.org_id, default_lab=self.identity.lab_id)
                for ep in self.instruments
                if (ep.instrument_id or "").strip() and (ep.bridge_url or "").strip()
            ]
        return [
            InstrumentEndpoint(
                instrument_id=self.identity.instrument_id,
                bridge_url=self.bridge.base_url,
                agent_id=self.identity.agent_id,
                lab_id=self.identity.lab_id,
                org_id=self.identity.org_id,
            ).resolve(default_org=self.identity.org_id, default_lab=self.identity.lab_id)
        ]

    @classmethod
    def from_mapping(cls, data: dict[str, Any], config_path: str = "") -> "AgentConfig":
        ident = _as_dict(data.get("identity"))
        br = _as_dict(data.get("bridge"))
        orch = _as_dict(data.get("orchestrator"))
        priv = _as_dict(data.get("privacy"))
        raw_instruments = data.get("instruments")
        instruments: list[InstrumentEndpoint] = []
        if isinstance(raw_instruments, list):
            for item in raw_instruments:
                if not isinstance(item, dict):
                    continue
                iid = str(item.get("instrument_id") or "").strip()
                url = str(item.get("bridge_url") or item.get("base_url") or "").strip()
                if not iid or not url:
                    continue
                instruments.append(
                    InstrumentEndpoint(
                        instrument_id=iid,
                        bridge_url=url,
                        agent_id=str(item.get("agent_id") or ""),
                        lab_id=str(item.get("lab_id") or ""),
                        org_id=str(item.get("org_id") or ""),
                    )
                )
        cfg = cls(
            identity=IdentityConfig(
                org_id=str(ident.get("org_id") or "acme"),
                lab_id=str(ident.get("lab_id") or "lab-local-01"),
                instrument_id=str(ident.get("instrument_id") or "CS-LOCAL-01"),
                agent_id=str(ident.get("agent_id") or ""),
            ),
            bridge=BridgeConfig(
                base_url=str(br.get("base_url") or "http://127.0.0.1:8081").rstrip("/"),
                timeout_s=float(br.get("timeout_s") or 60),
            ),
            orchestrator=OrchestratorConfig(
                listen_host=str(orch.get("listen_host") or "127.0.0.1"),
                listen_port=int(orch.get("listen_port") or 8090),
                registry_path=str(orch.get("registry_path") or "agent_registry.json"),
                snapshot_dir=str(orch.get("snapshot_dir") or "acquisition_snapshots"),
                heartbeat_interval_s=float(orch.get("heartbeat_interval_s") or 30),
                online_ttl_s=float(orch.get("online_ttl_s") or 90),
                embed_local_agent=bool(orch.get("embed_local_agent", True)),
            ),
            privacy=PrivacyConfig(
                redact_sample_names=bool(priv.get("redact_sample_names", True)),
            ),
            instruments=instruments,
            config_path=config_path,
        )
        cfg.identity.ensure_agent_id()
        return cfg


def resolve_config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = (os.environ.get("CORNERSTONE_AGENT_CONFIG") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd() / "cornerstone-agent.config.json"
    if cwd.is_file():
        return cwd.resolve()
    here = Path(__file__).resolve().parents[2] / "cornerstone-agent.config.example.json"
    return here.resolve()


def load_config(path: str | None = None) -> AgentConfig:
    p = resolve_config_path(path)
    if not p.is_file():
        return AgentConfig.from_mapping({}, config_path=str(p))
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be object: {p}")
    return AgentConfig.from_mapping(raw, config_path=str(p))
