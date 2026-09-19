"""A1 时序：指标抽取 + SQLite 查询 / 导出 / 采集 tick。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_agent.bridge_client import BridgeError  # noqa: E402
from cornerstone_agent.collector import collect_once  # noqa: E402
from cornerstone_agent.config import AgentConfig  # noqa: E402
from cornerstone_agent.metrics import extract_points  # noqa: E402
from cornerstone_agent.timeseries import TimeseriesStore  # noqa: E402


class FakeBridge:
    def __init__(self, payloads=None, fail=None):
        self.base_url = "http://fake"
        self.payloads = payloads or {}
        self.fail = fail or set()

    def get_json(self, path, query=None):
        if path in self.fail:
            raise BridgeError(f"fail {path}", status=500, body={"ok": False})
        if path not in self.payloads:
            return {"ok": True, "path": path}
        return self.payloads[path]


STATUS_OK = {
    "ok": True,
    "upstreamConnected": True,
    "instrumentOnline": True,
    "businessOnline": True,
    "queueCount": 2,
    "heartbeatFailStreak": 0,
    "commandFailStreak": 0,
}

STATUS_CHECK = {
    "ok": True,
    "systemCheck": {"passed": 8, "failed": 1, "total": 9, "executed": 9, "items": []},
    "odometers": [{"type": "Combustion", "value": "1234"}],
    "elements": [{"key": "Furnace", "value": "1450"}],
    "leakChecks": [],
}

COUNTERS = {
    "ok": True,
    "items": [
        {"key": "Tube", "name": "Combustion Tube", "expiresIn": "12.5", "isExpired": False},
    ],
}

PARAMS = {
    "ok": True,
    "sections": [
        {
            "id": "furnace",
            "title": "炉",
            "fields": [
                {
                    "id": "FurnaceTemp",
                    "label": "炉温",
                    "rawValue": "1450",
                    "units": "C",
                }
            ],
        }
    ],
}


class MetricsExtractTests(unittest.TestCase):
    def test_extract_status_and_params(self) -> None:
        endpoints = {
            "status": {"ok": True, "data": STATUS_OK},
            "status-check": {"ok": True, "data": STATUS_CHECK},
            "counters": {"ok": True, "data": COUNTERS},
            "system-parameters": {"ok": True, "data": PARAMS},
        }
        pts = extract_points(endpoints)
        by = {p.metric: p for p in pts}
        self.assertEqual(by["status.business_online"].value_bool, True)
        self.assertEqual(by["status.queue_count"].value_num, 2.0)
        self.assertEqual(by["syscheck.failed"].value_num, 1.0)
        self.assertEqual(by["odometer.Combustion"].value_num, 1234.0)
        self.assertEqual(by["counter.Tube.expires_in"].value_num, 12.5)
        self.assertEqual(by["param.FurnaceTemp"].value_num, 1450.0)
        self.assertEqual(by["param.FurnaceTemp"].unit, "C")

    def test_extract_gauges(self) -> None:
        endpoints = {
            "status-widgets": {
                "ok": True,
                "data": {
                    "ok": True,
                    "widgets": [
                        {
                            "id": "FurnaceTemperature",
                            "label": "Furnace",
                            "units": "°C",
                            "value": "1450",
                            "warning": False,
                        },
                        {
                            "id": "IncomingPressure",
                            "label": "Incoming",
                            "units": "psi",
                            "value": "40.2",
                            "warning": True,
                        },
                    ],
                },
            }
        }
        pts = extract_points(endpoints)
        by = {p.metric: p for p in pts}
        self.assertEqual(by["gauge.Furnace"].value_num, 1450.0)
        self.assertEqual(by["gauge.Furnace"].unit, "°C")
        self.assertEqual(by["gauge.Incoming"].value_num, 40.2)
        self.assertTrue(by["gauge.Incoming.warning"].value_bool)

    def test_extract_ambients(self) -> None:
        endpoints = {
            "ambients": {
                "ok": True,
                "data": {
                    "ok": True,
                    "items": [
                        {
                            "name": "LabTemp",
                            "key": "1",
                            "value": "22.5",
                            "valueRaw": "22.5",
                            "units": "C",
                            "inWarning": "false",
                        }
                    ],
                },
            }
        }
        pts = extract_points(endpoints)
        by = {p.metric: p for p in pts}
        self.assertEqual(by["ambient.LabTemp"].value_num, 22.5)
        self.assertEqual(by["ambient.LabTemp"].unit, "C")

    def test_skip_failed_alias(self) -> None:
        endpoints = {
            "status": {"ok": False, "error": {"message": "down"}},
        }
        self.assertEqual(extract_points(endpoints), [])


class TimeseriesStoreTests(unittest.TestCase):
    def test_insert_query_export_prune(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = TimeseriesStore(Path(td) / "ts.sqlite3")
            endpoints = {"status": {"ok": True, "data": STATUS_OK}}
            pts = extract_points(endpoints)
            sid = store.insert_sample(
                instrument_id="GC8",
                lab_id="lab-2lg",
                ok=True,
                points=pts,
            )
            self.assertGreater(sid, 0)
            stats = store.sample_stats("GC8", hours=24)
            self.assertEqual(stats["samples"], 1)
            self.assertEqual(stats["ok"], 1)
            series = store.query_series("GC8", "status.business_online", hours=24)
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]["value"], 1.0)
            csv_text = store.export_csv("GC8", hours=24)
            self.assertIn("status.business_online", csv_text)
            metrics = store.list_metrics("GC8", hours=24)
            self.assertIn("status.queue_count", metrics)
            latest = store.latest_values("GC8")
            self.assertIsNotNone(latest["sample"])
            by_id = store.sample_values(int(latest["sample"]["id"]))
            self.assertEqual(by_id["sample"]["id"], latest["sample"]["id"])
            self.assertEqual(len(by_id["values"]), len(latest["values"]))
            self.assertEqual(store.sample_values(999999)["sample"], None)
            n = store.prune(retention_days=1)
            self.assertEqual(n, 0)
            # 强制过期
            store._conn.execute("UPDATE samples SET ts_epoch = 1")
            store._conn.execute("UPDATE points SET ts_epoch = 1")
            store._conn.commit()
            self.assertEqual(store.prune(retention_days=1), 1)
            self.assertEqual(store.sample_stats("GC8", hours=24)["samples"], 0)
            store.close()

    def test_migrate_job_id_on_legacy_db(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "legacy.sqlite3"
            conn = sqlite3.connect(str(path))
            conn.executescript(
                """
                CREATE TABLE samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ts_epoch REAL NOT NULL,
                    instrument_id TEXT NOT NULL,
                    lab_id TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL DEFAULT '',
                    ok INTEGER NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    point_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id INTEGER NOT NULL,
                    ts_epoch REAL NOT NULL,
                    instrument_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value_num REAL,
                    value_bool INTEGER,
                    value_text TEXT,
                    unit TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO samples (ts, ts_epoch, instrument_id, ok, duration_ms, point_count)
                VALUES ('2026-01-01T00:00:00Z', 1767225600, 'GC8', 1, 10, 0);
                """
            )
            conn.commit()
            conn.close()
            store = TimeseriesStore(path)
            cols = {str(r[1]) for r in store._conn.execute("PRAGMA table_info(samples)")}
            self.assertIn("job_id", cols)
            sid = store.insert_sample(instrument_id="GC8", ok=True, job_id="widgets")
            self.assertGreater(sid, 1)
            sample = store.latest_sample("GC8", job_id="widgets")
            self.assertIsNotNone(sample)
            self.assertEqual(sample["job_id"], "widgets")
            store.close()


class CollectOnceTests(unittest.TestCase):
    def test_tick_writes_points(self) -> None:
        bridge = FakeBridge(
            {
                "/api/status": STATUS_OK,
                "/api/diagnostic/status-check": STATUS_CHECK,
                "/api/instrument/counters": COUNTERS,
                "/api/instrument/system-parameters": PARAMS,
            }
        )
        with tempfile.TemporaryDirectory() as td:
            store = TimeseriesStore(Path(td) / "ts.sqlite3")
            out = collect_once(
                bridge=bridge,  # type: ignore[arg-type]
                store=store,
                instrument_id="GC8",
                lab_id="lab-2lg",
                agent_id="agent-2lg-gc8",
                endpoints=["status", "status-check", "counters", "system-parameters"],
                job_id="status",
            )
            self.assertTrue(out["ok"])
            self.assertGreater(out["point_count"], 3)
            series = store.query_series("GC8", "status.business_online", hours=24)
            self.assertEqual(len(series), 1)
            store.close()

    def test_bridge_down_records_fail(self) -> None:
        bridge = FakeBridge(fail={"/api/status"})
        with tempfile.TemporaryDirectory() as td:
            store = TimeseriesStore(Path(td) / "ts.sqlite3")
            out = collect_once(
                bridge=bridge,  # type: ignore[arg-type]
                store=store,
                instrument_id="GC8",
                endpoints=["status"],
            )
            # 单 endpoint 失败时 _collect_pass 记 alias ok=False，整体判 fail
            self.assertFalse(out["ok"])
            sample = store.latest_sample("GC8")
            self.assertIsNotNone(sample)
            self.assertEqual(sample["ok"], 0)
            store.close()


class ConfigTimeseriesTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = AgentConfig.from_mapping({})
        self.assertTrue(cfg.timeseries.enabled)
        self.assertEqual(cfg.timeseries.interval_s, 10.0)
        ids = [j.id for j in cfg.timeseries.jobs]
        self.assertEqual(ids, ["widgets", "ambients"])
        widgets = cfg.timeseries.job_by_id("widgets")
        self.assertIsNotNone(widgets)
        assert widgets is not None
        self.assertEqual(widgets.interval_s, 10.0)
        self.assertEqual(widgets.retention_days, 3)
        self.assertEqual(widgets.endpoints, ["status-widgets"])
        amb = cfg.timeseries.job_by_id("ambients")
        self.assertIsNotNone(amb)
        assert amb is not None
        self.assertEqual(amb.interval_s, 300.0)
        self.assertEqual(amb.retention_days, 90)
        self.assertIn("status-widgets", cfg.timeseries.endpoints)
        self.assertIn("ambients", cfg.timeseries.endpoints)

    def test_interval_floor(self) -> None:
        cfg = AgentConfig.from_mapping(
            {
                "timeseries": {
                    "enabled": False,
                    "jobs": [
                        {
                            "id": "x",
                            "endpoints": ["status"],
                            "interval_s": 1,
                            "retention_days": 7,
                        }
                    ],
                }
            }
        )
        self.assertFalse(cfg.timeseries.enabled)
        self.assertEqual(cfg.timeseries.jobs[0].interval_s, 5.0)

    def test_scope_single(self) -> None:
        from cornerstone_agent.config import InstrumentEndpoint

        cfg = AgentConfig.from_mapping(
            {
                "timeseries": {
                    "jobs": [
                        {
                            "id": "widgets",
                            "endpoints": ["status-widgets"],
                            "interval_s": 10,
                            "retention_days": 3,
                            "scope": "single",
                            "instrument_ids": ["GC8"],
                        }
                    ]
                }
            }
        )
        job = cfg.timeseries.jobs[0]
        inst = [
            InstrumentEndpoint("GC8", "http://a"),
            InstrumentEndpoint("GO7", "http://b"),
        ]
        picked = cfg.timeseries.select_instruments(job, inst)
        self.assertEqual([e.instrument_id for e in picked], ["GC8"])
        self.assertTrue(job.matches_instrument("GC8"))
        self.assertFalse(job.matches_instrument("GO7"))

    def test_save_jobs_preserves_instruments(self) -> None:
        from cornerstone_agent.config import save_timeseries_config, timeseries_to_mapping

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cfg.json"
            path.write_text(
                json.dumps(
                    {
                        "instruments": [
                            {
                                "instrument_id": "GC8",
                                "bridge_url": "http://192.0.2.10:8080",
                            }
                        ],
                        "timeseries": {"enabled": True},
                    }
                ),
                encoding="utf-8",
            )
            cfg = AgentConfig.from_mapping(
                json.loads(path.read_text(encoding="utf-8")),
                config_path=str(path),
            )
            save_timeseries_config(cfg, cfg.timeseries)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(raw["instruments"]), 1)
            self.assertEqual(raw["instruments"][0]["instrument_id"], "GC8")
            self.assertEqual(
                [j["id"] for j in raw["timeseries"]["jobs"]],
                ["widgets", "ambients"],
            )
            self.assertEqual(timeseries_to_mapping(cfg.timeseries)["jobs"][0]["interval_s"], 10.0)


if __name__ == "__main__":
    unittest.main()
