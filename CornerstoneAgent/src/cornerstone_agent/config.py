from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


def _as_dict(v: Any, default: dict | None = None) -> dict:
    return dict(v) if isinstance(v, dict) else (default or {})


_JOB_ID_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def _clamp_interval(raw: Any, default: float = 300.0) -> float:
    try:
        interval = float(raw if raw is not None else default)
    except (TypeError, ValueError):
        interval = default
    if interval < 5:
        return 5.0
    if interval > 86400:
        return 86400.0
    return interval


def _clamp_retention(raw: Any, default: int = 30) -> int:
    try:
        days = int(raw if raw is not None else default)
    except (TypeError, ValueError):
        days = default
    if days < 1:
        return 1
    if days > 3650:
        return 3650
    return days


def _parse_job(raw: Any, *, fallback_id: str) -> TimeseriesJobConfig | None:
    if not isinstance(raw, dict):
        return None
    jid = str(raw.get("id") or fallback_id).strip()
    if not _JOB_ID_SAFE.match(jid):
        jid = fallback_id
    endpoints: list[str] = []
    if isinstance(raw.get("endpoints"), list):
        endpoints = [str(x).strip() for x in raw["endpoints"] if str(x).strip()]
    if not endpoints:
        return None
    from .collect import ENDPOINT_SPECS

    endpoints = [a for a in endpoints if a in ENDPOINT_SPECS]
    if not endpoints:
        return None
    scope = str(raw.get("scope") or "all").strip().lower()
    if scope not in ("all", "single"):
        scope = "all"
    ids: list[str] = []
    if isinstance(raw.get("instrument_ids"), list):
        ids = [str(x).strip() for x in raw["instrument_ids"] if str(x).strip()]
    enabled = True if "enabled" not in raw else bool(raw.get("enabled"))
    return TimeseriesJobConfig(
        id=jid,
        enabled=enabled,
        label=str(raw.get("label") or jid).strip() or jid,
        endpoints=endpoints,
        interval_s=_clamp_interval(raw.get("interval_s"), 300.0),
        retention_days=_clamp_retention(raw.get("retention_days"), 30),
        scope=scope,
        instrument_ids=ids,
    )


def default_timeseries_jobs() -> list[TimeseriesJobConfig]:
    return [
        TimeseriesJobConfig(
            id="widgets",
            enabled=True,
            label="实时仪表 Widgets",
            endpoints=["status-widgets"],
            interval_s=10.0,
            retention_days=3,
            scope="all",
        ),
        TimeseriesJobConfig(
            id="ambients",
            enabled=True,
            label="环境 Ambients",
            endpoints=["ambients"],
            interval_s=300.0,
            retention_days=90,
            scope="all",
        ),
    ]


def _parse_timeseries(ts: dict[str, Any]) -> TimeseriesConfig:
    timeout = float(ts.get("timeout_s") or 90)
    if timeout < 5:
        timeout = 5.0
    enabled = True if "enabled" not in ts else bool(ts.get("enabled"))
    jobs: list[TimeseriesJobConfig] = []
    if "jobs" in ts:
        raw_jobs = ts.get("jobs")
        if isinstance(raw_jobs, list):
            seen: set[str] = set()
            for i, item in enumerate(raw_jobs):
                job = _parse_job(item, fallback_id=f"job-{i + 1}")
                if job is None:
                    continue
                if job.id in seen:
                    job.id = f"{job.id}-{i + 1}"
                seen.add(job.id)
                jobs.append(job)
    else:
        jobs = default_timeseries_jobs()
    return TimeseriesConfig(
        enabled=enabled,
        db_path=str(ts.get("db_path") or "agent_timeseries.sqlite3"),
        timeout_s=timeout,
        jobs=jobs,
    )


def timeseries_to_mapping(ts: "TimeseriesConfig") -> dict[str, Any]:
    return {
        "enabled": ts.enabled,
        "db_path": ts.db_path,
        "timeout_s": ts.timeout_s,
        "jobs": [j.to_mapping() for j in ts.jobs],
    }


def save_timeseries_config(cfg: "AgentConfig", ts: "TimeseriesConfig") -> Path:
    """只改配置文件里的 timeseries，保留 instruments[] 等其它字段。"""
    path = Path(cfg.config_path)
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config root must be object")
    raw["timeseries"] = timeseries_to_mapping(ts)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


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


DEFAULT_TIMESERIES_ENDPOINTS = (
    "status-widgets",
    "ambients",
)


@dataclass
class TimeseriesJobConfig:
    """一条独立采集任务：查询命令列表 + 周期 + 有效期 + 仪器范围。"""

    id: str
    endpoints: list[str]
    enabled: bool = True
    label: str = ""
    interval_s: float = 300.0
    retention_days: int = 30
    scope: str = "all"
    instrument_ids: list[str] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "label": self.label or self.id,
            "endpoints": list(self.endpoints),
            "interval_s": self.interval_s,
            "retention_days": self.retention_days,
            "scope": self.scope,
            "instrument_ids": list(self.instrument_ids),
        }

    def matches_instrument(self, instrument_id: str) -> bool:
        iid = (instrument_id or "").strip()
        if not iid:
            return False
        if self.scope != "single":
            return True
        wanted = {x.strip() for x in self.instrument_ids if str(x).strip()}
        return iid in wanted


@dataclass
class TimeseriesConfig:
    """A1 长周期采集：多任务 SQLite 时序。"""

    enabled: bool = True
    db_path: str = "agent_timeseries.sqlite3"
    timeout_s: float = 90.0
    jobs: list[TimeseriesJobConfig] = field(default_factory=default_timeseries_jobs)

    def enabled_jobs(self) -> list[TimeseriesJobConfig]:
        return [j for j in self.jobs if j.enabled and j.endpoints]

    def job_by_id(self, job_id: str) -> TimeseriesJobConfig | None:
        jid = (job_id or "").strip()
        if not jid:
            return None
        for job in self.jobs:
            if job.id == jid:
                return job
        return None

    @property
    def interval_s(self) -> float:
        jobs = self.enabled_jobs()
        if not jobs:
            return 300.0
        return min(j.interval_s for j in jobs)

    @property
    def retention_days(self) -> int:
        jobs = self.jobs or default_timeseries_jobs()
        return max((j.retention_days for j in jobs), default=30)

    @property
    def endpoints(self) -> list[str]:
        out: list[str] = []
        for job in self.enabled_jobs():
            for alias in job.endpoints:
                if alias not in out:
                    out.append(alias)
        return out

    def select_instruments(
        self,
        job: TimeseriesJobConfig,
        instruments: Sequence[InstrumentEndpoint],
    ) -> list[InstrumentEndpoint]:
        if job.scope == "single":
            wanted = {x.strip() for x in job.instrument_ids if str(x).strip()}
            if not wanted:
                return []
            return [ep for ep in instruments if ep.instrument_id in wanted]
        return list(instruments)


@dataclass
class AgentConfig:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    timeseries: TimeseriesConfig = field(default_factory=TimeseriesConfig)
    instruments: list[InstrumentEndpoint] = field(default_factory=list)
    config_path: str = ""

    def resolve_data_path(self, raw: str, default_name: str) -> Path:
        """相对路径相对配置文件目录（无配置则 CWD）。"""
        p = Path((raw or default_name).strip() or default_name)
        if p.is_absolute():
            return p
        base = Path(self.config_path).resolve().parent if self.config_path else Path.cwd()
        return (base / p).resolve()

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
        ts = _as_dict(data.get("timeseries"))
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
            timeseries=_parse_timeseries(ts),
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
