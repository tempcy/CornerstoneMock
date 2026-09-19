"""Unit tests for P1 collect (stdlib unittest, mocked Bridge)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_agent.bridge_client import BridgeError  # noqa: E402
from cornerstone_agent.collect import resolve_endpoint_aliases  # noqa: E402
from cornerstone_agent.jobs import execute_job, handle_collect  # noqa: E402
from cornerstone_agent.snapshots import SnapshotStore  # noqa: E402


class FakeBridge:
    def __init__(self, payloads: dict[str, Any] | None = None, fail: set[str] | None = None):
        self.base_url = "http://fake"
        self.payloads = payloads or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, query))
        if path in self.fail:
            raise BridgeError(f"fail {path}", status=500, body={"ok": False})
        if path not in self.payloads:
            return {"ok": True, "path": path, "query": query}
        return self.payloads[path]


class CollectResolveTests(unittest.TestCase):
    def test_profiles(self) -> None:
        self.assertEqual(
            resolve_endpoint_aliases("status_light", None),
            ["status", "status-check"],
        )
        self.assertIn("system-parameters", resolve_endpoint_aliases("troubleshoot", None))

    def test_custom_requires_endpoints(self) -> None:
        with self.assertRaises(ValueError):
            resolve_endpoint_aliases("custom", [])

    def test_unknown_alias(self) -> None:
        with self.assertRaises(ValueError):
            resolve_endpoint_aliases("custom", ["not-a-real-endpoint"])


class CollectJobTests(unittest.TestCase):
    def test_status_light_snapshot(self) -> None:
        bridge = FakeBridge(
            {
                "/api/status": {"ok": True, "businessOnline": True, "upstreamConnected": True},
                "/api/diagnostic/status-check": {"ok": True, "items": []},
            }
        )
        with tempfile.TemporaryDirectory() as td:
            data = handle_collect(
                bridge,  # type: ignore[arg-type]
                {"profile": "status_light"},
                {"snapshot_store": SnapshotStore(td)},
            )
            self.assertTrue(str(data["snapshot_id"]).startswith("snap-"))
            self.assertEqual(data["profile"], "status_light")
            self.assertTrue(data["endpoints"]["status"]["ok"])
            self.assertTrue(data["persisted"])
            store = SnapshotStore(td)
            loaded = store.load(data["snapshot_id"])
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["snapshot_id"], data["snapshot_id"])

    def test_troubleshoot_infers_set_key(self) -> None:
        bridge = FakeBridge(
            {
                "/api/status": {"ok": True},
                "/api/instrument/system-parameters": {"ok": True, "sections": []},
                "/api/diagnostic/status-check": {"ok": True},
                "/api/instrument/counters": {"ok": True, "items": []},
                "/api/instrument/sets": {
                    "ok": True,
                    "items": [{"setKey": "KEY-1", "name": "s1"}],
                },
                "/api/instrument/set-stats": {"ok": True, "setKey": "KEY-1"},
            }
        )
        data = handle_collect(bridge, {"profile": "troubleshoot"}, {})  # type: ignore[arg-type]
        stats = data["endpoints"]["set-stats"]
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["query"].get("set_key"), "KEY-1")
        # sets was only used to infer key, not left in result for troubleshoot
        self.assertNotIn("sets", data["endpoints"])

    def test_execute_job_collect(self) -> None:
        bridge = FakeBridge(
            {
                "/api/status": {"ok": True},
                "/api/diagnostic/status-check": {"ok": True},
            }
        )
        with tempfile.TemporaryDirectory() as td:
            result = execute_job(
                {
                    "schema_version": "agent-job.v1",
                    "job_id": "j1",
                    "type": "collect",
                    "created_at": "2026-08-16T00:00:00Z",
                    "params": {"profile": "status_light"},
                },
                bridge=bridge,  # type: ignore[arg-type]
                agent_id="a1",
                instrument_id="GC8",
                snapshot_dir=td,
            )
            self.assertEqual(result["status"], "ok")
            self.assertIn("snapshot_id", result["data"])


if __name__ == "__main__":
    unittest.main()
