from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from cornerstone_bridge.config import (
    parse_bridge_config_text,
    write_toml_config_file,
)
from cornerstone_bridge.paths import (
    WEB_CONFIG_SEARCH_NAMES,
    default_web_config_path,
    expand_config_path,
    new_default_web_config_path,
)

_WEB_PKG_DIR = Path(__file__).resolve().parents[2]

_WEB_ALLOWED_KEYS = frozenset(
    {
        "web_host",
        "web_port",
        "bridge_api_host",
        "bridge_api_port",
        "bridge_api_url",
    }
)


def resolve_explicit_config_path(path: str) -> Optional[Path]:
    """解析 ``-c`` 路径（cwd → Web 包目录 → 仓库根）。"""
    raw = Path(path).expanduser()
    if raw.is_file():
        return raw.resolve()
    search_roots = (Path.cwd(), _WEB_PKG_DIR, _WEB_PKG_DIR.parent)
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


def repo_web_config_path() -> Optional[Path]:
    """仓库内 ``CornerstoneWeb/cornerstone-web.config.{toml,json}``（本地开发用）。"""
    return _first_existing_in_dir(_WEB_PKG_DIR, WEB_CONFIG_SEARCH_NAMES)


def resolve_web_config_path() -> Optional[Path]:
    """查找 Web 配置：环境变量 → ``%APPDATA%\\CornerstoneMock`` → cwd → 包目录（TOML 优先）。"""
    for env_name in ("CORNERSTONE_WEB_CONFIG",):
        env = (os.environ.get(env_name) or "").strip()
        if env:
            p = Path(expand_config_path(env))
            return p.resolve() if p.is_file() else None
    pd = default_web_config_path()
    if pd.is_file():
        return pd
    found = _first_existing_in_dir(Path.cwd(), WEB_CONFIG_SEARCH_NAMES)
    if found is not None:
        return found
    return _first_existing_in_dir(_WEB_PKG_DIR, WEB_CONFIG_SEARCH_NAMES)


def resolve_dev_web_config_path() -> Optional[Path]:
    """``cornerstone-web-dev``：环境变量 → 仓库 Web 配置 → 其余同 :func:`resolve_web_config_path`。"""
    env = (os.environ.get("CORNERSTONE_WEB_CONFIG") or "").strip()
    if env:
        explicit = resolve_explicit_config_path(env)
        if explicit is not None:
            return explicit
        p = Path(expand_config_path(env))
        return p.resolve() if p.is_file() else None
    repo = repo_web_config_path()
    if repo is not None:
        return repo
    return resolve_web_config_path()


def write_web_config_file(path: Path, values: Dict[str, Any]) -> None:
    """写入 Web 配置（新文件推荐 TOML）。"""
    if path.suffix.lower() == ".json":
        path.parent.mkdir(parents=True, exist_ok=True)
        filtered = {k: values[k] for k in sorted(values) if k in _WEB_ALLOWED_KEYS}
        path.write_text(
            json.dumps(filtered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    write_toml_config_file(path, values, _WEB_ALLOWED_KEYS)


def load_web_config_defaults(config_path: Path) -> Dict[str, Any]:
    """读取 Web 配置（浏览器监听与 Bridge REST 代理目标）。"""
    text = config_path.read_text(encoding="utf-8")
    raw = parse_bridge_config_text(text, path=config_path)
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _WEB_ALLOWED_KEYS:
            print(f"[cornerstone-web] 配置文件忽略未知键: {k!r}", file=sys.stderr)
            continue
        if k in ("web_port", "bridge_api_port"):
            out[k] = int(v)
            continue
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else str(v)
    return out


def bridge_base_url_from_args(
    *,
    bridge_api_url: str,
    bridge_api_host: str,
    bridge_api_port: int,
    web_host: str,
    web_port: int,
) -> str:
    url = (bridge_api_url or "").strip().rstrip("/")
    if url:
        return url
    host = (bridge_api_host or web_host or "127.0.0.1").strip()
    port = bridge_api_port if bridge_api_port else int(web_port) + 1
    return f"http://{host}:{port}"
