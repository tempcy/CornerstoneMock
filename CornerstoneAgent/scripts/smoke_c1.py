#!/usr/bin/env python3
"""C1 smoke: register + P0/P1 tools (stdlib). Run from CornerstoneAgent with Bridge optional."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_agent.config import load_config  # noqa: E402
from cornerstone_agent.dispatcher import ToolDispatcher  # noqa: E402
from cornerstone_agent.registry import DEFAULT_CAPABILITIES, AgentRegistry  # noqa: E402
from cornerstone_agent.bridge_client import BridgeClient  # noqa: E402


def main() -> int:
    cfg = load_config(str(ROOT / "cornerstone-agent.config.json"))
    registry = AgentRegistry(cfg.orchestrator.registry_path, cfg.orchestrator.online_ttl_s)
    bridge = BridgeClient(cfg.bridge.base_url, timeout_s=min(10.0, cfg.bridge.timeout_s))
    reachable = bridge.ping()
    rec = registry.register(
        agent_id=cfg.identity.agent_id,
        org_id=cfg.identity.org_id,
        lab_id=cfg.identity.lab_id,
        instrument_id=cfg.identity.instrument_id,
        bridge_url=cfg.bridge.base_url,
        capabilities=list(DEFAULT_CAPABILITIES),
        bridge_reachable=reachable,
    )
    print("register:", json.dumps(rec.to_public(online=True), ensure_ascii=False))
    print("bridge_reachable:", reachable)

    from pathlib import Path

    snap = Path(cfg.orchestrator.snapshot_dir)
    if not snap.is_absolute():
        snap = ROOT / snap
    disp = ToolDispatcher(
        registry,
        redact_sample_names=cfg.privacy.redact_sample_names,
        snapshot_dir=str(snap),
    )
    for name, args in [
        ("list_instruments", {"online_only": True}),
        (
            "get_instrument_status",
            {
                "lab_id": cfg.identity.lab_id,
                "instrument_id": cfg.identity.instrument_id,
                "include_status_check": True,
            },
        ),
        (
            "get_analysis_sets",
            {
                "lab_id": cfg.identity.lab_id,
                "instrument_id": cfg.identity.instrument_id,
                "number": 3,
            },
        ),
        (
            "collect_instrument",
            {
                "lab_id": cfg.identity.lab_id,
                "instrument_id": cfg.identity.instrument_id,
                "profile": "status_light",
            },
        ),
    ]:
        out = disp.dispatch_tool(name, args)
        print(f"\n=== {name} ok={out.get('ok')} ===")
        print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
