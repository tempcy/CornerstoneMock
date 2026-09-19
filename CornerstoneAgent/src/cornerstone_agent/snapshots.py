"""acquisition-snapshot 落盘（P1）。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional


def new_snapshot_id() -> str:
    return f"snap-{uuid.uuid4().hex[:16]}"


class SnapshotStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, snapshot_id: str) -> Path:
        safe = "".join(c for c in snapshot_id if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError("invalid snapshot_id")
        return self.root / f"{safe}.json"

    def save(self, snapshot: dict[str, Any]) -> str:
        sid = str(snapshot.get("snapshot_id") or "").strip() or new_snapshot_id()
        snapshot = dict(snapshot)
        snapshot["snapshot_id"] = sid
        snapshot.setdefault("saved_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        path = self.path_for(sid)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return sid

    def load(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        path = self.path_for(snapshot_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
