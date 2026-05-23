from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from .hub_types import PendingAddSamples
from .paths import appdata_cornerstone_dir, default_queue_persist_path, expand_config_path

_QUEUE_FILE_VERSION = 1


def resolve_queue_persist_path(
    *,
    config_file_path: Optional[Path],
    explicit_path: Optional[str],
    persist_enabled: bool,
) -> Optional[Path]:
    if not persist_enabled:
        return None
    env = (os.environ.get("CORNERSTONE_BRIDGE_QUEUE_FILE") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if explicit_path and str(explicit_path).strip():
        raw = str(explicit_path).strip()
        expanded = Path(expand_config_path(raw))
        if config_file_path is not None:
            config_dir = Path(config_file_path).resolve().parent
            name = expanded.name or "cornerstone-bridge.add-samples-queue.json"
            sys_root = (appdata_cornerstone_dir()).resolve()
            try:
                resolved = expanded.resolve()
                if "systemprofile" in str(resolved).lower() or str(resolved).lower().startswith(
                    str(sys_root).lower()
                ):
                    return (config_dir / name).resolve()
            except OSError:
                if "systemprofile" in str(expanded).lower():
                    return (config_dir / name).resolve()
        return expanded.resolve()
    if config_file_path is not None:
        return (Path(config_file_path).resolve().parent / "cornerstone-bridge.add-samples-queue.json").resolve()
    return default_queue_persist_path()


def load_add_samples_queue(path: Optional[Path]) -> List[PendingAddSamples]:
    if path is None or not path.is_file():
        return []
    try:
        # utf-8-sig：兼容无 BOM（Python 写入）与带 BOM（PowerShell Set-Content -Encoding UTF8）
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[bridge] 无法读取样品队列缓存 {path}: {e}", file=sys.stderr)
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("items")
    if not isinstance(items, list):
        return []
    out: List[PendingAddSamples] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        payload = str(row.get("payload_xml") or "").strip()
        if not payload:
            continue
        out.append(
            PendingAddSamples(
                entry_id=str(row.get("entry_id") or "").strip() or _new_entry_id(),
                received_at=float(row.get("received_at") or 0.0),
                source_peer=str(row.get("source_peer") or ""),
                payload_xml=payload,
                sample_name=str(row.get("sample_name") or ""),
                sample_description=str(row.get("sample_description") or ""),
            )
        )
    return out


def save_add_samples_queue(path: Optional[Path], items: List[PendingAddSamples]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _QUEUE_FILE_VERSION,
        "items": [asdict(p) for p in items],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _new_entry_id() -> str:
    import secrets

    return secrets.token_hex(8)
