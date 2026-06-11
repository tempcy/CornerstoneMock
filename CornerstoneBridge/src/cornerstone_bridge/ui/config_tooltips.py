"""从 cornerstone-bridge.config.example.toml 解析配置项悬停说明。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from ..paths import BRIDGE_CONFIG_EXAMPLE_NAMES


def _example_toml_path() -> Path:
    bridge_pkg_dir = Path(__file__).resolve().parents[3]
    for name in BRIDGE_CONFIG_EXAMPLE_NAMES:
        cand = bridge_pkg_dir / name
        if cand.is_file():
            return cand
    return bridge_pkg_dir / "cornerstone-bridge.config.example.toml"


def load_bridge_config_tooltips() -> Dict[str, str]:
    path = _example_toml_path()
    if not path.is_file():
        return {}
    tips: Dict[str, str] = {}
    pending: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            pending = []
            continue
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            if not text or text.startswith("="):
                pending = []
                continue
            pending.append(text)
            continue
        if "=" not in line:
            pending = []
            continue
        key = line.split("=", 1)[0].strip()
        if not key or not key.replace("_", "").isalnum():
            pending = []
            continue
        if pending:
            tips[key] = " ".join(pending)
        pending = []
    return tips
