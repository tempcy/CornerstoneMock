"""Web 配置 TOML / JSON 解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cornerstone_web.config import load_web_config_defaults


def test_load_web_example_toml() -> None:
    p = Path(__file__).resolve().parents[2] / "CornerstoneWeb" / "cornerstone-web.config.example.toml"
    d = load_web_config_defaults(p)
    assert d["web_port"] == 8080
    assert d["bridge_api_port"] == 8081


def test_load_web_json(tmp_path: Path) -> None:
    cfg = tmp_path / "web.json"
    cfg.write_text('{"web_port": 9090, "bridge_api_port": 8082}\n', encoding="utf-8")
    d = load_web_config_defaults(cfg)
    assert d["web_port"] == 9090
    assert d["bridge_api_port"] == 8082
