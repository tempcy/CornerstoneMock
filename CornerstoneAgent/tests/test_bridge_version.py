"""Bridge 版本字段为可选：缺失不得影响连通性语义。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_agent.bridge_client import extract_bridge_version  # noqa: E402
from cornerstone_agent.jobs import _status_summary  # noqa: E402


class BridgeVersionOptionalTests(unittest.TestCase):
    def test_extract_missing(self) -> None:
        self.assertEqual(extract_bridge_version(None), "")
        self.assertEqual(extract_bridge_version({}), "")
        self.assertEqual(extract_bridge_version({"ok": True}), "")

    def test_extract_present(self) -> None:
        self.assertEqual(extract_bridge_version({"bridgeVersion": "0.1.17"}), "0.1.17")
        self.assertEqual(extract_bridge_version({"bridge_version": " 1.2 "}), "1.2")

    def test_status_summary_omits_when_absent(self) -> None:
        s = _status_summary({"ok": True, "businessOnline": True, "upstreamConnected": True})
        self.assertNotIn("bridge_version", s)
        self.assertEqual(s["ready_hint"], "online_ok")

    def test_status_summary_includes_when_present(self) -> None:
        s = _status_summary({"ok": True, "bridgeVersion": "0.1.17"})
        self.assertEqual(s["bridge_version"], "0.1.17")


if __name__ == "__main__":
    unittest.main()
