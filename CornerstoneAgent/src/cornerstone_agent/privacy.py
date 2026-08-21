from __future__ import annotations

import hashlib
import re
from typing import Any


_SAMPLE_KEY_RE = re.compile(
    r"(sample|试样|样品|customer|client|客户).*(name|id|code|名称|编号)?",
    re.I,
)


def _hash_text(s: str) -> str:
    return "h_" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def redact_value(obj: Any, *, enabled: bool) -> Any:
    if not enabled:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k)
            if _SAMPLE_KEY_RE.search(key) and isinstance(v, str) and v.strip():
                out[k] = _hash_text(v)
            else:
                out[k] = redact_value(v, enabled=True)
        return out
    if isinstance(obj, list):
        return [redact_value(x, enabled=True) for x in obj]
    return obj
