"""Bridge 操作员建议 inbox 单元测试（不依赖 tomllib / 全量 hub）。"""

from __future__ import annotations

from pathlib import Path

from cornerstone_bridge.operator_notices import OperatorNotice, OperatorNoticeStore


def test_upsert_idempotent_and_ack(tmp_path: Path) -> None:
    store = OperatorNoticeStore(tmp_path / "notices.json")
    n = OperatorNotice.from_dict(
        {
            "notice_id": "n-1",
            "title": "漏气",
            "message": "请检查载气",
            "severity": "maintain",
            "source": "zhibao",
        }
    )
    assert n is not None
    stored, created = store.upsert(n)
    assert created is True
    assert stored.to_public_dict()["popup"] is True

    _, created2 = store.upsert(n)
    assert created2 is False

    pending = store.list(pending_only=True)
    assert len(pending) == 1
    assert store.pending_popup_ids() == ["n-1"]

    acked = store.ack("n-1", action="acked", acked_by="tech", note="已处理")
    assert acked is not None
    assert acked.status == "acked"
    assert store.list(pending_only=True) == []
    assert store.pending_popup_ids() == []


def test_persist_reload(tmp_path: Path) -> None:
    path = tmp_path / "notices.json"
    store = OperatorNoticeStore(path)
    n = OperatorNotice.from_dict(
        {"notice_id": "n-2", "title": "info", "message": "hello", "severity": "info"}
    )
    assert n is not None
    store.upsert(n)
    store2 = OperatorNoticeStore(path)
    got = store2.get("n-2")
    assert got is not None
    assert got.message == "hello"
