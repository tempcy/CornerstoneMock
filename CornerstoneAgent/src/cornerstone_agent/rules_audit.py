"""A2 规则评估审计：JSONL 落盘（命中与检查摘要）。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RulesAuditStore:
    """追加写入 ``rules_audit.jsonl``；可选内存最近 N 条。"""

    def __init__(self, path: str | Path, *, memory_limit: int = 200) -> None:
        self.path = Path(path)
        self.memory_limit = max(1, int(memory_limit))
        self._lock = threading.RLock()
        self._recent: list[dict[str, Any]] = []

    def record(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            out = dict(row)
            out.setdefault("event", "rules_eval")
            out.setdefault("recorded_at", _utc_iso())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(out, ensure_ascii=False) + "\n")
            self._recent.append(out)
            if len(self._recent) > self.memory_limit:
                self._recent = self._recent[-self.memory_limit :]
            return out

    def list_recent(
        self,
        *,
        limit: int = 50,
        instrument_id: str = "",
        hit_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._recent)
        if not items and self.path.is_file():
            items = self._load_tail(limit=max(limit, 100))
        iid = (instrument_id or "").strip()
        out: list[dict[str, Any]] = []
        for row in reversed(items):
            if iid and str(row.get("instrument_id") or "") != iid:
                continue
            if hit_only and not row.get("hit"):
                continue
            out.append(row)
            if len(out) >= limit:
                break
        return out

    def _load_tail(self, *, limit: int) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
