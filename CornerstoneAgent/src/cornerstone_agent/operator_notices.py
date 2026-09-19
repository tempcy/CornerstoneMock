"""操作员建议下行审计：本地镜像 Bridge inbox，并同步 ack。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class OperatorNoticeAudit:
    """Agent 侧 notice 审计日志（JSONL + 内存索引）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._by_id: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("notice_id"):
                    self._by_id[str(row["notice_id"])] = row
        except OSError:
            return

    def _append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def record_sent(self, notice: dict[str, Any], *, bridge_result: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            nid = str(notice.get("notice_id") or notice.get("noticeId") or uuid.uuid4())
            row = {
                "notice_id": nid,
                "lab_id": notice.get("lab_id") or notice.get("labId") or "",
                "instrument_id": notice.get("instrument_id") or notice.get("instrumentId") or "",
                "source": notice.get("source") or "orchestrator",
                "severity": notice.get("severity") or "info",
                "title": notice.get("title") or "",
                "message": notice.get("message") or "",
                "status": "pending",
                "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "bridge_created": bool((bridge_result or {}).get("created", True)),
                "acked_at": "",
                "acked_by": "",
                "ack_note": "",
            }
            self._by_id[nid] = row
            self._append({"event": "sent", **row})
            return row

    def record_ack(
        self,
        notice_id: str,
        *,
        status: str = "acked",
        acked_by: str = "",
        note: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._by_id.get(notice_id)
            if row is None:
                row = {
                    "notice_id": notice_id,
                    "status": status,
                    "sent_at": "",
                }
            row["status"] = status
            row["acked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            row["acked_by"] = acked_by
            row["ack_note"] = note
            self._by_id[notice_id] = row
            self._append({"event": "ack", **row})
            return row

    def list(self, *, pending_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._by_id.values())
        rows.sort(key=lambda r: str(r.get("sent_at") or ""), reverse=True)
        if pending_only:
            rows = [r for r in rows if r.get("status") == "pending"]
        return rows[: max(1, int(limit))]

    def sync_from_bridge_items(self, items: list[dict[str, Any]]) -> int:
        """用 Bridge inbox 状态回写本地 ack（返回更新条数）。"""
        updated = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("noticeId") or it.get("notice_id") or "")
            status = str(it.get("status") or "")
            if not nid or status not in ("acked", "dismissed"):
                continue
            with self._lock:
                local = self._by_id.get(nid)
                if local is None:
                    continue
                if local.get("status") == status:
                    continue
            self.record_ack(
                nid,
                status=status,
                acked_by=str(it.get("ackedBy") or it.get("acked_by") or ""),
                note=str(it.get("ackNote") or it.get("ack_note") or ""),
            )
            updated += 1
        return updated
