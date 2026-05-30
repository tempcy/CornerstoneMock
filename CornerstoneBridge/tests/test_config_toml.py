"""Bridge 配置 TOML / JSON 解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cornerstone_bridge.config import (
    load_bridge_config_defaults,
    parse_bridge_config_text,
    parse_bridge_config_toml,
    write_bridge_config_file,
)


def test_parse_example_toml() -> None:
    p = Path(__file__).resolve().parents[1] / "cornerstone-bridge.config.example.toml"
    text = p.read_text(encoding="utf-8")
    raw = parse_bridge_config_toml(text)
    assert raw["host"] == "0.0.0.0"
    assert raw["upstream_heartbeat_interval"] == 0


def test_load_example_toml_defaults() -> None:
    p = Path(__file__).resolve().parents[1] / "cornerstone-bridge.config.example.toml"
    d = load_bridge_config_defaults(p)
    assert d["port"] == 54321
    assert d["instrument_short_connection"] is False


def test_json_with_line_comments() -> None:
    raw = parse_bridge_config_text(
        '{\n// note\n"port": 99\n}',
        path="x.json",
    )
    assert raw["port"] == 99


def test_write_toml_preserves_comments(tmp_path: Path) -> None:
    cfg = tmp_path / "bridge.toml"
    cfg.write_text(
        '# keep me\nhost = "127.0.0.1"\nport = 1\n',
        encoding="utf-8",
    )
    write_bridge_config_file(cfg, {"host": "0.0.0.0", "port": 2})
    out = cfg.read_text(encoding="utf-8")
    assert "# keep me" in out
    assert 'host = "0.0.0.0"' in out
    d = load_bridge_config_defaults(cfg)
    assert d["port"] == 2


def test_flatten_toml_section() -> None:
    raw = parse_bridge_config_toml(
        '[upstream]\nhost = "10.0.0.1"\nport = 12345\n'
    )
    assert raw["upstream_host"] == "10.0.0.1"
    assert raw["upstream_port"] == 12345
