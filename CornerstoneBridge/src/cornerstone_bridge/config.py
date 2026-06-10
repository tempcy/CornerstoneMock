from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

import tomlkit
from tomlkit import TOMLDocument, table

from .paths import (
    BRIDGE_CONFIG_EXAMPLE_NAMES,
    BRIDGE_CONFIG_SEARCH_NAMES,
    appdata_cornerstone_dir,
    default_bridge_config_path,
    expand_config_path,
    new_default_bridge_config_path,
)
from .protocol import _normalize_encoding

_BRIDGE_PKG_DIR = Path(__file__).resolve().parents[2]

_WEB_KEYS_IN_DEV = (
    "web_host",
    "web_port",
    "bridge_api_host",
    "bridge_api_port",
    "bridge_api_url",
)

_BRIDGE_ALLOWED_KEYS = frozenset(
    {
        "host",
        "port",
        "bridge_api_host",
        "bridge_api_port",
        "web_host",
        "web_port",
        "upstream_host",
        "upstream_port",
        "encoding",
        "add_samples_queue_size",
        "no_synthetic_logon",
        "instrument_long_connection",
        "upstream_heartbeat_interval",
        "upstream_inner_reassembly_timeout",
        "upstream_recv_idle_clear",
        "upstream_heartbeat_fail_max",
        "upstream_command_fail_max",
        "upstream_client_forward_timeout",
        "upstream_heartbeat_wait_timeout",
        "upstream_activity_stale_seconds",
        "upstream_read_cancel_timeout",
        "upstream_stale_check_interval",
        "upstream_auto_reconnect",
        "async_message_interval",
        "web_user",
        "web_password",
        "privileged_add_samples_host",
        "persist_add_samples_queue",
        "add_samples_queue_persist_file",
        "log_level",
        "log_verbose_gateway",
        "log_file",
        "log_file_level",
        "log_file_max_mb",
        "log_file_backup_count",
        "log_throttle_interval_s",
        "compac_enabled",
        "compac_port",
        "compac_baud_rate",
        "compac_data_bits",
        "compac_parity",
        "compac_stop_bits",
        "compac_listen_enabled",
        "compac_timeout_seconds",
        "compac_retry_count",
        "compac_queue_max",
        "compac_recv_idle_clear_seconds",
    }
)


def resolve_explicit_config_path(path: str) -> Optional[Path]:
    """
    解析 ``-c`` 传入的路径：先按 cwd，再按 Bridge 包目录及其上级（仓库根）。

    便于在 ``CornerstoneWeb`` 下写 ``-c CornerstoneBridge/cornerstone-bridge.config.toml``。
    """
    raw = Path(path).expanduser()
    if raw.is_file():
        return raw.resolve()
    search_roots = (Path.cwd(), _BRIDGE_PKG_DIR, _BRIDGE_PKG_DIR.parent)
    for root in search_roots:
        cand = root / raw
        if cand.is_file():
            return cand.resolve()
        by_name = root / raw.name
        if by_name.is_file():
            return by_name.resolve()
    return None


def _first_existing_in_dir(directory: Path, names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        p = directory / name
        if p.is_file():
            return p.resolve()
    return None


def repo_bridge_config_path() -> Optional[Path]:
    """仓库内 ``CornerstoneBridge/cornerstone-bridge.config.{toml,json}``（本地开发用）。"""
    return _first_existing_in_dir(_BRIDGE_PKG_DIR, BRIDGE_CONFIG_SEARCH_NAMES)


def resolve_bridge_config_path() -> Optional[Path]:
    """查找 Bridge 配置：环境变量 → ``%APPDATA%\\CornerstoneMock`` → cwd → 包目录（TOML 优先于 JSON）。"""
    env = (os.environ.get("CORNERSTONE_BRIDGE_CONFIG") or "").strip()
    if env:
        p = Path(expand_config_path(env))
        return p.resolve() if p.is_file() else None
    pd = default_bridge_config_path()
    if pd.is_file():
        return pd
    cwd = Path.cwd()
    found = _first_existing_in_dir(cwd, BRIDGE_CONFIG_SEARCH_NAMES)
    if found is not None:
        return found
    return _first_existing_in_dir(_BRIDGE_PKG_DIR, BRIDGE_CONFIG_SEARCH_NAMES)


def resolve_dev_bridge_config_path() -> Optional[Path]:
    """
    ``cornerstone-web-dev`` 专用：环境变量 → 仓库 ``CornerstoneBridge/*.toml|json`` → 其余同
    :func:`resolve_bridge_config_path`。
    """
    env = (os.environ.get("CORNERSTONE_BRIDGE_CONFIG") or "").strip()
    if env:
        explicit = resolve_explicit_config_path(env)
        if explicit is not None:
            return explicit
        p = Path(expand_config_path(env))
        return p.resolve() if p.is_file() else None
    repo = repo_bridge_config_path()
    if repo is not None:
        return repo
    return resolve_bridge_config_path()


def merge_web_config_into_bridge(
    bridge: Dict[str, Any], web: Dict[str, Any]
) -> Dict[str, Any]:
    """``cornerstone-web-dev``：将 Web 专配中的监听/代理地址合并进 Bridge 参数字典。"""
    merged = dict(bridge)
    for k in _WEB_KEYS_IN_DEV:
        if k in web:
            merged[k] = web[k]
    return merged


def strip_json_line_comments(text: str) -> str:
    """去掉整行 ``//`` 注释（仅旧版 JSON 配置）。"""
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


def parse_bridge_config_json(text: str) -> Dict[str, Any]:
    raw = json.loads(strip_json_line_comments(text))
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点须为 JSON 对象 {...}")
    return raw


def _flatten_toml_tables(raw: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """将 ``[section]`` 表展平为 ``section_key``（仅一层；与顶层同名键时顶层优先）。"""
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        name = f"{prefix}{key}" if prefix else str(key)
        if isinstance(value, dict):
            for sub_k, sub_v in _flatten_toml_tables(value, prefix=f"{name}_").items():
                if sub_k not in out:
                    out[sub_k] = sub_v
        else:
            out[name] = value
    return out


def parse_bridge_config_toml(text: str) -> Dict[str, Any]:
    raw = tomllib.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点须为 TOML 表")
    return _flatten_toml_tables(raw)


def parse_bridge_config_text(text: str, *, path: Union[Path, str, None] = None) -> Dict[str, Any]:
    """按路径后缀解析 TOML 或 JSON（未知后缀时按内容尝试）。"""
    suffix = Path(path).suffix.lower() if path else ""
    if suffix == ".json":
        return parse_bridge_config_json(text)
    if suffix == ".toml":
        return parse_bridge_config_toml(text)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return parse_bridge_config_json(text)
    return parse_bridge_config_toml(text)


def write_toml_config_file(
    path: Path, values: Dict[str, Any], allowed_keys: frozenset[str]
) -> None:
    """写入 TOML；在已有文件上合并以保留 ``#`` 注释。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: TOMLDocument
    if path.is_file():
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    else:
        doc = table()
    for k, v in values.items():
        if k in allowed_keys:
            doc[k] = v
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def write_bridge_config_file(path: Path, values: Dict[str, Any]) -> None:
    """写入配置；TOML 时在已有文件上合并以保留 ``#`` 注释。"""
    if path.suffix.lower() == ".json":
        path.parent.mkdir(parents=True, exist_ok=True)
        filtered = {k: values[k] for k in sorted(values) if k in _BRIDGE_ALLOWED_KEYS}
        path.write_text(
            json.dumps(filtered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    write_toml_config_file(path, values, _BRIDGE_ALLOWED_KEYS)


def load_bridge_config_defaults(config_path: Path) -> Dict[str, Any]:
    """读取 Bridge 配置（网关、上游、REST、仪器账号等；不含浏览器 ``web_*`` 专配）。"""
    text = config_path.read_text(encoding="utf-8")
    raw = parse_bridge_config_text(text, path=config_path)
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _BRIDGE_ALLOWED_KEYS:
            print(f"[cornerstone-bridge] 配置文件忽略未知键: {k!r}", file=sys.stderr)
            continue
        if k == "encoding" and v is not None and str(v).strip() != "":
            out[k] = _normalize_encoding(str(v))
            continue
        if k in (
            "port",
            "web_port",
            "upstream_port",
            "add_samples_queue_size",
            "bridge_api_port",
            "compac_baud_rate",
            "compac_data_bits",
            "compac_stop_bits",
            "compac_retry_count",
            "compac_queue_max",
        ):
            out[k] = int(v)
            continue
        if k in ("compac_timeout_seconds", "compac_recv_idle_clear_seconds"):
            out[k] = float(v)
            continue
        if k in ("compac_enabled", "compac_listen_enabled"):
            out[k] = bool(v)
            continue
        if k in (
            "compac_port",
            "compac_parity",
        ):
            if v is not None and str(v).strip() != "":
                out[k] = str(v).strip()
            continue
        if k == "async_message_interval":
            out[k] = float(v)
            continue
        if k == "log_verbose_gateway":
            out[k] = bool(v)
            continue
        if k in ("log_file_max_mb", "log_throttle_interval_s"):
            out[k] = float(v)
            continue
        if k == "log_file_backup_count":
            out[k] = int(v)
            continue
        if k in ("log_level", "log_file", "log_file_level"):
            if v is not None and str(v).strip() != "":
                out[k] = str(v).strip()
            continue
        if k in ("no_synthetic_logon", "persist_add_samples_queue"):
            out[k] = bool(v)
            continue
        if k == "instrument_long_connection":
            out["instrument_short_connection"] = not bool(v)
            continue
        if k in (
            "upstream_heartbeat_interval",
            "upstream_inner_reassembly_timeout",
            "upstream_recv_idle_clear",
            "upstream_client_forward_timeout",
            "upstream_heartbeat_wait_timeout",
            "upstream_activity_stale_seconds",
            "upstream_read_cancel_timeout",
            "upstream_stale_check_interval",
        ):
            out[k] = float(v)
            continue
        if k in ("upstream_heartbeat_fail_max", "upstream_command_fail_max"):
            out[k] = int(v)
            continue
        if k == "upstream_auto_reconnect":
            out["no_upstream_auto_reconnect"] = not bool(v)
            continue
        if k == "log_file":
            out[k] = str(v).strip() if v is not None else ""
            continue
        if k == "add_samples_queue_persist_file":
            out[k] = str(v).strip() if v is not None else ""
            continue
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else str(v)
    return out


def ensure_app_data_config_dir() -> Path:
    """确保 ``%APPDATA%\\CornerstoneMock`` 存在。"""
    d = appdata_cornerstone_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_program_data_config_dir() -> Path:
    """已弃用别名，等同于 :func:`ensure_app_data_config_dir`。"""
    return ensure_app_data_config_dir()
