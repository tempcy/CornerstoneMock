"""操作员建议下行 inbox（Agent / 智宝 → Bridge UI）。

仅展示与确认，不触发写仪器。内存 + 可选 JSON 落盘。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_FILE_VERSION = 1
_MAX_NOTICES = 100
_VALID_SEVERITIES = frozenset({"info", "review", "reject", "maintain"})
_VALID_SOURCES = frozenset({"agent_rule", "zhibao", "orchestrator", "manual"})
_POPUP_SEVERITIES = frozenset({"reject", "maintain"})


@dataclass
class OperatorNotice:
    notice_id: str
    source: str
    severity: str
    title: str
    message: str
    created_at: float
    evidence: List[Any] = field(default_factory=list)
    actions: List[Any] = field(default_factory=list)
    expires_at: Optional[float] = None
    require_ack: bool = True
    status: str = "pending"  # pending | acked | dismissed
    acked_at: Optional[float] = None
    acked_by: str = ""
    ack_note: str = ""
    lab_id: str = ""
    instrument_id: str = ""
    agent_id: str = ""
    trace_id: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "noticeId": self.notice_id,
            "source": self.source,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "createdAt": self.created_at,
            "createdAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
            "evidence": list(self.evidence),
            "actions": list(self.actions),
            "expiresAt": self.expires_at,
            "requireAck": self.require_ack,
            "status": self.status,
            "ackedAt": self.acked_at,
            "ackedBy": self.acked_by,
            "ackNote": self.ack_note,
            "labId": self.lab_id,
            "instrumentId": self.instrument_id,
            "agentId": self.agent_id,
            "traceId": self.trace_id,
            "popup": self.severity in _POPUP_SEVERITIES and self.status == "pending",
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> Optional["OperatorNotice"]:
        if not isinstance(raw, dict):
            return None
        nid = str(raw.get("notice_id") or raw.get("noticeId") or "").strip()
        title = str(raw.get("title") or "").strip()
        message = str(raw.get("message") or "").strip()
        if not title and not message:
            return None
        if not nid:
            nid = str(uuid.uuid4())
        sev = str(raw.get("severity") or "info").strip().lower()
        if sev not in _VALID_SEVERITIES:
            sev = "info"
        src = str(raw.get("source") or "orchestrator").strip().lower()
        if src not in _VALID_SOURCES:
            src = "orchestrator"
        created = raw.get("created_at") or raw.get("createdAt")
        try:
            created_at = float(created) if created is not None else time.time()
        except (TypeError, ValueError):
            created_at = time.time()
        expires = raw.get("expires_at") if "expires_at" in raw else raw.get("expiresAt")
        expires_at: Optional[float]
        try:
            expires_at = float(expires) if expires is not None and expires != "" else None
        except (TypeError, ValueError):
            expires_at = None
        status = str(raw.get("status") or "pending").strip().lower()
        if status not in ("pending", "acked", "dismissed"):
            status = "pending"
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        actions = raw.get("actions") if isinstance(raw.get("actions"), list) else []
        acked_at_raw = raw.get("acked_at") if "acked_at" in raw else raw.get("ackedAt")
        try:
            acked_at = float(acked_at_raw) if acked_at_raw not in (None, "") else None
        except (TypeError, ValueError):
            acked_at = None
        return cls(
            notice_id=nid,
            source=src,
            severity=sev,
            title=title or (message[:40] + ("…" if len(message) > 40 else "")),
            message=message or title,
            created_at=created_at,
            evidence=list(evidence),
            actions=list(actions),
            expires_at=expires_at,
            require_ack=bool(raw.get("require_ack", raw.get("requireAck", True))),
            status=status,
            acked_at=acked_at,
            acked_by=str(raw.get("acked_by") or raw.get("ackedBy") or ""),
            ack_note=str(raw.get("ack_note") or raw.get("ackNote") or ""),
            lab_id=str(raw.get("lab_id") or raw.get("labId") or ""),
            instrument_id=str(raw.get("instrument_id") or raw.get("instrumentId") or ""),
            agent_id=str(raw.get("agent_id") or raw.get("agentId") or ""),
            trace_id=str(raw.get("trace_id") or raw.get("traceId") or ""),
        )


class OperatorNoticeStore:
    def __init__(self, persist_path: Optional[Path] = None, *, max_notices: int = _MAX_NOTICES) -> None:
        self._lock = threading.RLock()
        self._path = persist_path
        self._max = max(1, int(max_notices))
        self._by_id: Dict[str, OperatorNotice] = {}
        self._order: List[str] = []
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[bridge] 无法读取操作员建议缓存 {self._path}: {e}", file=sys.stderr)
            return
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return
        for row in items:
            n = OperatorNotice.from_dict(row if isinstance(row, dict) else {})
            if n is None:
                continue
            self._by_id[n.notice_id] = n
            self._order.append(n.notice_id)
        self._trim_unlocked()

    def _persist_unlocked(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _FILE_VERSION,
                "items": [asdict(self._by_id[i]) for i in self._order if i in self._by_id],
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as e:
            print(f"[bridge] 无法写入操作员建议缓存 {self._path}: {e}", file=sys.stderr)

    def _trim_unlocked(self) -> None:
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._by_id.pop(old, None)

    def upsert(self, notice: OperatorNotice) -> tuple[OperatorNotice, bool]:
        """返回 (notice, created)。同 notice_id 幂等更新正文，保留已有 ack。"""
        with self._lock:
            existing = self._by_id.get(notice.notice_id)
            if existing is not None:
                if existing.status != "pending":
                    # 已确认则不覆盖状态，只允许补字段
                    return existing, False
                notice.status = existing.status
                notice.acked_at = existing.acked_at
                notice.acked_by = existing.acked_by
                notice.ack_note = existing.ack_note
                self._by_id[notice.notice_id] = notice
                self._persist_unlocked()
                return notice, False
            self._by_id[notice.notice_id] = notice
            self._order.append(notice.notice_id)
            self._trim_unlocked()
            self._persist_unlocked()
            return notice, True

    def list(
        self,
        *,
        status: Optional[str] = None,
        pending_only: bool = False,
    ) -> List[OperatorNotice]:
        with self._lock:
            now = time.time()
            out: List[OperatorNotice] = []
            for nid in reversed(self._order):
                n = self._by_id.get(nid)
                if n is None:
                    continue
                if n.expires_at is not None and n.expires_at < now and n.status == "pending":
                    n.status = "dismissed"
                    n.ack_note = n.ack_note or "expired"
                if pending_only and n.status != "pending":
                    continue
                if status and n.status != status:
                    continue
                out.append(n)
            return out

    def get(self, notice_id: str) -> Optional[OperatorNotice]:
        with self._lock:
            return self._by_id.get(notice_id)

    def ack(
        self,
        notice_id: str,
        *,
        action: str = "acked",
        acked_by: str = "",
        note: str = "",
    ) -> Optional[OperatorNotice]:
        action = (action or "acked").strip().lower()
        if action not in ("acked", "dismissed"):
            action = "acked"
        with self._lock:
            n = self._by_id.get(notice_id)
            if n is None:
                return None
            n.status = action
            n.acked_at = time.time()
            n.acked_by = (acked_by or "").strip() or "operator"
            if note:
                n.ack_note = str(note)
            self._persist_unlocked()
            return n

    def pending_popup_ids(self) -> List[str]:
        return [
            n.notice_id
            for n in self.list(pending_only=True)
            if n.severity in _POPUP_SEVERITIES
        ]


def default_notices_persist_path(config_file_path: Optional[Path]) -> Path:
    if config_file_path is not None:
        return (Path(config_file_path).resolve().parent / "cornerstone-bridge.operator-notices.json").resolve()
    from .paths import appdata_cornerstone_dir

    return (appdata_cornerstone_dir() / "cornerstone-bridge.operator-notices.json").resolve()
