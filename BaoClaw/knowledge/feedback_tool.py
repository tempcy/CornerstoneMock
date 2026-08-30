#!/usr/bin/env python3
"""BaoClaw knowledge feedback — Plan A: append/list/promote JSONL on company side.

No lab orchestrator DB. Storage: BaoClaw/knowledge/feedback/YYYY-MM.jsonl

Usage:
  py -3 feedback_tool.py append --file payload.json
  py -3 feedback_tool.py append --stdin
  py -3 feedback_tool.py list [--kind case_closure] [--instrument GC8] [--queue]
  py -3 feedback_tool.py promote --feedback-id fb-20260830-demo-001 [--case-id fc-...]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
FEEDBACK_DIR = ROOT / "feedback"
PROMOTE_LOG = FEEDBACK_DIR / "promote_log.jsonl"
FAULT_CASES_DIR = ROOT / "raw" / "fault_cases"
SCHEMA_VERSION = "kb-feedback.v1"

CST = timezone.utc  # store UTC; display can localize


def _now_iso() -> str:
    return datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%SZ")


def _month_file(when: Optional[str] = None) -> Path:
    if when:
        ym = when[:7]
    else:
        ym = datetime.now(CST).strftime("%Y-%m")
    return FEEDBACK_DIR / f"{ym}.jsonl"


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{i}: invalid JSON: {e}") from e
    return rows


def _iter_all_feedback() -> Iterable[Dict[str, Any]]:
    if not FEEDBACK_DIR.is_dir():
        return
    for path in sorted(FEEDBACK_DIR.glob("*.jsonl")):
        if path.name == "promote_log.jsonl":
            continue
        for row in _load_jsonl(path):
            yield row


def _load_promote_log() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _load_jsonl(PROMOTE_LOG):
        fid = row.get("feedback_id")
        if fid:
            out[str(fid)] = row
    return out


def _validate_feedback(obj: Dict[str, Any]) -> None:
    if obj.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"schema_version must be {SCHEMA_VERSION!r}")
    kind = obj.get("kind")
    if kind not in {
        "chat_rating",
        "case_closure",
        "notice_ack",
        "correction",
        "escalation",
    }:
        raise SystemExit(f"invalid kind: {kind!r}")
    if not obj.get("feedback_id"):
        raise SystemExit("feedback_id is required (or use append without --no-id)")
    src = obj.get("source")
    if not isinstance(src, dict) or not src.get("channel"):
        raise SystemExit("source.channel is required")
    if kind == "chat_rating" and not isinstance(obj.get("rating"), dict):
        raise SystemExit("chat_rating requires rating object")
    if kind in ("case_closure", "correction") and not obj.get("outcome"):
        raise SystemExit(f"{kind} requires outcome")


def cmd_append(args: argparse.Namespace) -> None:
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    obj = json.loads(raw)
    if not args.no_id and not obj.get("feedback_id"):
        obj["feedback_id"] = f"fb-{uuid.uuid4()}"
    if not obj.get("created_at"):
        obj["created_at"] = _now_iso()
    _validate_feedback(obj)
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = _month_file(obj.get("created_at"))
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "accepted", "path": str(path), "feedback_id": obj["feedback_id"]}, ensure_ascii=False))


def cmd_list(args: argparse.Namespace) -> None:
    promoted = _load_promote_log()
    rows: List[Dict[str, Any]] = []
    for row in _iter_all_feedback():
        fid = str(row.get("feedback_id", ""))
        if args.kind and row.get("kind") != args.kind:
            continue
        ctx = row.get("context") or {}
        if args.instrument and ctx.get("instrument_id") != args.instrument:
            continue
        if args.queue:
            if fid in promoted:
                continue
            if not row.get("proposed_case") and row.get("kind") not in ("case_closure", "notice_ack", "correction"):
                continue
            if row.get("outcome") not in ("resolved", "partial", None) and not row.get("proposed_case"):
                continue
        rec = dict(row)
        if fid in promoted:
            rec["_promoted"] = promoted[fid]
        rows.append(rec)
    if args.limit:
        rows = rows[-args.limit :]
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


def cmd_promote(args: argparse.Namespace) -> None:
    target_id = args.feedback_id
    match: Optional[Dict[str, Any]] = None
    for row in _iter_all_feedback():
        if str(row.get("feedback_id")) == target_id:
            match = row
    if not match:
        raise SystemExit(f"feedback_id not found: {target_id}")
    promoted = _load_promote_log()
    if target_id in promoted:
        raise SystemExit(f"already promoted to {promoted[target_id].get('case_id')}")

    case = None
    if args.case_file:
        case = json.loads(Path(args.case_file).read_text(encoding="utf-8"))
    elif match.get("proposed_case"):
        case = match["proposed_case"]
    else:
        raise SystemExit("no proposed_case; pass --case-file")

    if case.get("schema_version") != "fault-case.v1":
        raise SystemExit("case must be fault-case.v1")
    case_id = args.case_id or case.get("case_id")
    if not case_id:
        raise SystemExit("case_id required")
    case["case_id"] = case_id
    case["status"] = args.status or "verified"
    meta = case.setdefault("metadata", {})
    meta.setdefault("feedback_ids", [])
    if target_id not in meta["feedback_ids"]:
        meta["feedback_ids"].append(target_id)
    meta["verified_at"] = _now_iso()
    if args.by:
        meta["verified_by"] = args.by

    out_dir = FAULT_CASES_DIR / "examples" if args.examples else FAULT_CASES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{case_id}.json"
    out_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    log_row = {
        "feedback_id": target_id,
        "case_id": case_id,
        "case_path": str(out_path.relative_to(ROOT)),
        "promoted_at": _now_iso(),
        "promoted_by": args.by or "",
    }
    with PROMOTE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_row, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "promoted", **log_row}, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description="BaoClaw knowledge feedback (Plan A JSONL)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Append one feedback record to monthly JSONL")
    a.add_argument("--file", help="JSON file path")
    a.add_argument("--stdin", action="store_true", help="Read JSON from stdin")
    a.add_argument("--no-id", action="store_true", help="Do not auto-generate feedback_id")
    a.set_defaults(func=cmd_append)

    l = sub.add_parser("list", help="List feedback records")
    l.add_argument("--kind")
    l.add_argument("--instrument")
    l.add_argument("--queue", action="store_true", help="Only promote candidates")
    l.add_argument("--limit", type=int)
    l.set_defaults(func=cmd_list)

    pr = sub.add_parser("promote", help="Promote feedback to fault_cases JSON")
    pr.add_argument("--feedback-id", required=True)
    pr.add_argument("--case-id")
    pr.add_argument("--case-file")
    pr.add_argument("--status", default="verified", choices=["draft", "verified", "deprecated"])
    pr.add_argument("--examples", action="store_true", help="Write under raw/fault_cases/examples/")
    pr.add_argument("--by", help="Reviewer id/name")
    pr.set_defaults(func=cmd_promote)

    args = p.parse_args()
    if args.cmd == "append" and not args.file and not args.stdin:
        p.error("append requires --file or --stdin")
    args.func(args)


if __name__ == "__main__":
    main()
