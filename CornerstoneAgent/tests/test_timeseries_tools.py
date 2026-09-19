"""A1 BaoClaw timeseries tools (local SQLite dispatch)."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_agent.config import TimeseriesConfig, TimeseriesJobConfig  # noqa: E402
from cornerstone_agent.dispatcher import ToolDispatcher, _downsample_series, _series_summary  # noqa: E402
from cornerstone_agent.metrics import MetricPoint  # noqa: E402
from cornerstone_agent.registry import AgentRegistry  # noqa: E402
from cornerstone_agent.timeseries import TimeseriesStore  # noqa: E402


class DownsampleTests(unittest.TestCase):
    def test_downsample_and_summary(self) -> None:
        series = [{"ts": str(i), "value": float(i), "unit": "u"} for i in range(100)]
        ds = _downsample_series(series, 11)
        self.assertEqual(len(ds), 11)
        self.assertEqual(ds[0]["value"], 0.0)
        self.assertEqual(ds[-1]["value"], 99.0)
        s = _series_summary(series)
        self.assertEqual(s["min"], 0.0)
        self.assertEqual(s["max"], 99.0)
        self.assertAlmostEqual(s["avg"], 49.5)


class TimeseriesToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.reg = AgentRegistry(str(base / "registry.json"), online_ttl_s=3600)
        self.reg.register(
            agent_id="agent-2lg-gc8",
            org_id="org",
            lab_id="lab-2lg",
            instrument_id="GC8",
            bridge_url="http://127.0.0.1:8081",
            capabilities=["get_status"],
            bridge_reachable=True,
        )
        self.store = TimeseriesStore(str(base / "ts.sqlite3"))
        now = time.time()
        self.sample_id = self.store.insert_sample(
            instrument_id="GC8",
            lab_id="lab-2lg",
            agent_id="agent-2lg-gc8",
            ok=True,
            duration_ms=100,
            ts_epoch=now,
            job_id="widgets",
            points=[
                MetricPoint(metric="gauge.Back_Pressure", value_num=800.0, unit="mmHg"),
                MetricPoint(metric="param.Foo", value_num=1.0, unit=""),
            ],
        )
        self.store.insert_sample(
            instrument_id="GC8",
            lab_id="lab-2lg",
            agent_id="agent-2lg-gc8",
            ok=True,
            duration_ms=110,
            ts_epoch=now + 10,
            job_id="widgets",
            points=[
                MetricPoint(metric="gauge.Back_Pressure", value_num=810.0, unit="mmHg"),
            ],
        )
        ts_cfg = TimeseriesConfig(
            enabled=True,
            jobs=[
                TimeseriesJobConfig(
                    id="widgets",
                    label="Widgets",
                    endpoints=["status-widgets"],
                    interval_s=10,
                    retention_days=3,
                ),
                TimeseriesJobConfig(
                    id="ambients",
                    label="Ambients",
                    endpoints=["ambients"],
                    interval_s=300,
                    retention_days=90,
                ),
            ],
        )
        self.disp = ToolDispatcher(
            self.reg,
            timeseries_store=self.store,
            timeseries_cfg=ts_cfg,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_latest_gauges_only(self) -> None:
        out = self.disp.dispatch_tool(
            "get_timeseries_latest",
            {"lab_id": "lab-2lg", "instrument_id": "GC8", "job_id": "widgets"},
        )
        self.assertTrue(out["ok"])
        vals = out["result"]["values"]
        self.assertTrue(all(str(v["metric"]).startswith("gauge.") for v in vals))
        self.assertEqual(vals[0]["value"], 810.0)

    def test_list_metrics_and_query(self) -> None:
        m = self.disp.dispatch_tool(
            "list_timeseries_metrics",
            {"lab_id": "lab-2lg", "instrument_id": "GC8", "hours": 24},
        )
        self.assertTrue(m["ok"])
        self.assertIn("gauge.Back_Pressure", m["result"]["metrics"])

        q = self.disp.dispatch_tool(
            "query_timeseries",
            {
                "lab_id": "lab-2lg",
                "instrument_id": "GC8",
                "hours": 24,
                "job_id": "widgets",
                "metric": "gauge.Back_Pressure",
            },
        )
        self.assertTrue(q["ok"])
        self.assertEqual(q["result"]["series_summary"]["last"], 810.0)
        self.assertGreaterEqual(len(q["result"]["samples"]), 1)

    def test_get_sample(self) -> None:
        out = self.disp.dispatch_tool(
            "get_timeseries_sample",
            {"lab_id": "lab-2lg", "instrument_id": "GC8", "sample_id": self.sample_id},
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["value_count"], 2)

    def test_disabled(self) -> None:
        d = ToolDispatcher(self.reg, timeseries_store=None)
        out = d.dispatch_tool(
            "get_timeseries_latest",
            {"lab_id": "lab-2lg", "instrument_id": "GC8"},
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "timeseries_disabled")


if __name__ == "__main__":
    unittest.main()
