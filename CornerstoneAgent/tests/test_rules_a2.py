"""Unit tests for A2 rules engine (daily maintenance counters)."""

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
from cornerstone_agent.envelopes import make_job  # noqa: E402
from cornerstone_agent.jobs import execute_job, handle_rules_eval  # noqa: E402
from cornerstone_agent.rules_audit import RulesAuditStore  # noqa: E402
from cornerstone_agent.rules_engine import (  # noqa: E402
    RULE_DAILY_MAINTENANCE,
    RuleDef,
    evaluate_daily_maintenance_counters,
    evaluate_rules,
    load_rule_defs,
)


COUNTERS_OK = {
    "ok": True,
    "items": [
        {
            "key": "Tube",
            "name": "Combustion Tube",
            "description": "Replace tube",
            "expiresIn": "12.5",
            "lastModified": "9/1/2026 8:00:00",
            "excluded": False,
            "isExpired": False,
        },
        {
            "key": "DailyLeak",
            "name": "Daily Leak Check",
            "description": "Daily leak check",
            "expiresIn": "0.0",
            "lastModified": "9/27/2026 7:00:00",
            "excluded": False,
            "isExpired": True,
        },
        {
            "key": "OldFilter",
            "name": "Excluded Filter",
            "expiresIn": "0",
            "excluded": True,
            "isExpired": True,
        },
    ],
}


class FakeBridge:
    def __init__(self, payloads: dict[str, Any] | None = None, fail: set[str] | None = None):
        self.base_url = "http://fake"
        self.payloads = payloads or {}
        self.fail = fail or set()
        self.posts: list[tuple[str, Any]] = []

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> Any:
        if path in self.fail:
            raise BridgeError(f"fail {path}", status=500, body={"ok": False})
        if path not in self.payloads:
            return {"ok": True}
        return self.payloads[path]

    def post_json(self, path: str, body: Any = None) -> Any:
        self.posts.append((path, body))
        return {"ok": True, "created": True}


class DailyMaintenanceRuleTests(unittest.TestCase):
    def test_load_bundled_rules(self) -> None:
        rules = load_rule_defs()
        ids = {r.id for r in rules}
        self.assertIn(RULE_DAILY_MAINTENANCE, ids)

    def test_hit_on_expired_not_excluded(self) -> None:
        rule = RuleDef(
            id=RULE_DAILY_MAINTENANCE,
            domain="instrument",
            severity="maintain",
        )
        out = evaluate_daily_maintenance_counters(
            COUNTERS_OK, rule=rule, instrument_id="GC8", lab_id="lab-2lg"
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["hit"])
        self.assertEqual(out["expired_missing_count"], 1)
        self.assertEqual(len(out["suggestions"]), 1)
        sug = out["suggestions"][0]
        self.assertEqual(sug["rule_id"], RULE_DAILY_MAINTENANCE)
        self.assertEqual(sug["severity"], "maintain")
        self.assertEqual(sug["evidence"][0]["key"], "DailyLeak")
        self.assertIn("Daily Leak Check", sug["message"])

    def test_pass_when_none_expired(self) -> None:
        rule = RuleDef(id=RULE_DAILY_MAINTENANCE)
        payload = {
            "ok": True,
            "items": [
                {"key": "A", "name": "Ok", "excluded": False, "isExpired": False},
            ],
        }
        out = evaluate_daily_maintenance_counters(payload, rule=rule)
        self.assertTrue(out["ok"])
        self.assertFalse(out["hit"])
        self.assertEqual(out["suggestions"], [])

    def test_evaluate_rules_fetches_counters(self) -> None:
        bridge = FakeBridge({"/api/instrument/counters": COUNTERS_OK})
        out = evaluate_rules(
            bridge,  # type: ignore[arg-type]
            load_rule_defs(),
            instrument_id="GC8",
            lab_id="lab-2lg",
            rule_ids=[RULE_DAILY_MAINTENANCE],
        )
        self.assertTrue(out["hit"])
        self.assertEqual(len(out["suggestions"]), 1)

    def test_evaluate_rules_bridge_error(self) -> None:
        bridge = FakeBridge(fail={"/api/instrument/counters"})
        out = evaluate_rules(
            bridge,  # type: ignore[arg-type]
            load_rule_defs(),
            rule_ids=[RULE_DAILY_MAINTENANCE],
        )
        self.assertFalse(out["hit"])
        self.assertTrue(out["fetch_error"])
        self.assertFalse(out["rule_results"][0]["ok"])


class RulesJobAndAuditTests(unittest.TestCase):
    def test_job_records_hit(self) -> None:
        bridge = FakeBridge({"/api/instrument/counters": COUNTERS_OK})
        with tempfile.TemporaryDirectory() as td:
            audit = RulesAuditStore(Path(td) / "rules_audit.jsonl")
            data = handle_rules_eval(
                bridge,  # type: ignore[arg-type]
                {"rule_ids": [RULE_DAILY_MAINTENANCE], "record": True},
                {
                    "instrument_id": "GC8",
                    "lab_id": "lab-2lg",
                    "agent_id": "agent-2lg-gc8",
                    "rules_audit": audit,
                },
            )
            self.assertTrue(data["hit"])
            self.assertTrue(data["audit_recorded"])
            recent = audit.list_recent(hit_only=True)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["instrument_id"], "GC8")
            self.assertEqual(recent[0]["suggestions"][0]["evidence"][0]["name"], "Daily Leak Check")
            # file persisted
            lines = (Path(td) / "rules_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["rule_results"][0]["expired_missing_count"], 1)

    def test_execute_job_rules_eval(self) -> None:
        bridge = FakeBridge({"/api/instrument/counters": COUNTERS_OK})
        with tempfile.TemporaryDirectory() as td:
            audit = RulesAuditStore(Path(td) / "a.jsonl")
            job = make_job("rules_eval", {"rule_ids": [RULE_DAILY_MAINTENANCE]})
            result = execute_job(
                job,
                bridge=bridge,  # type: ignore[arg-type]
                agent_id="a1",
                instrument_id="GC8",
                lab_id="lab-2lg",
                rules_audit=audit,
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["data"]["hit"])


if __name__ == "__main__":
    unittest.main()
