"""Unit tests for Agent UI overview helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_agent.config import AgentConfig  # noqa: E402
from cornerstone_agent.registry import AgentRegistry  # noqa: E402
from cornerstone_agent.ui_api import build_config_view, build_overview  # noqa: E402


class UiApiTests(unittest.TestCase):
    def test_overview_merges_config_and_registry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            reg_path = Path(td) / "reg.json"
            registry = AgentRegistry(reg_path, online_ttl_s=120)
            registry.register(
                agent_id="agent-2lg-gc8",
                org_id="baowu",
                lab_id="lab-2lg",
                instrument_id="GC8",
                bridge_url="http://192.0.2.10:8080",
                bridge_reachable=True,
            )
            cfg = AgentConfig.from_mapping(
                {
                    "identity": {
                        "org_id": "baowu",
                        "lab_id": "lab-2lg",
                        "instrument_id": "GC8",
                        "agent_id": "agent-2lg-gc8",
                    },
                    "bridge": {"base_url": "http://192.0.2.10:8080"},
                    "instruments": [
                        {
                            "instrument_id": "GC8",
                            "agent_id": "agent-2lg-gc8",
                            "bridge_url": "http://192.0.2.10:8080",
                        },
                        {
                            "instrument_id": "GO7",
                            "agent_id": "agent-2lg-go7",
                            "bridge_url": "http://192.0.2.11:8080",
                        },
                    ],
                },
                config_path=str(Path(td) / "cfg.json"),
            )
            ov = build_overview(cfg, registry, started_at=0)
            self.assertTrue(ov["ok"])
            self.assertEqual(ov["summary"]["instruments_total"], 2)
            ids = {r["instrument_id"] for r in ov["instruments"]}
            self.assertEqual(ids, {"GC8", "GO7"})
            gc8 = next(r for r in ov["instruments"] if r["instrument_id"] == "GC8")
            self.assertTrue(gc8["online"])
            go7 = next(r for r in ov["instruments"] if r["instrument_id"] == "GO7")
            self.assertFalse(go7["online"])
            self.assertEqual(go7["bridge_url"], "http://192.0.2.11:8080")

            cfg_view = build_config_view(cfg)
            self.assertFalse(cfg_view["read_only"])
            self.assertEqual(len(cfg_view["instruments"]), 2)
            catalog = cfg_view.get("catalog") or []
            aliases = {c["alias"] for c in catalog}
            self.assertIn("status-widgets", aliases)
            self.assertIn("ambients", aliases)
            jobs = (cfg_view.get("timeseries") or {}).get("jobs") or []
            self.assertEqual({j["id"] for j in jobs}, {"widgets", "ambients"})


if __name__ == "__main__":
    unittest.main()
