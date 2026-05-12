"""
使用 JSON 配置文件启动 cornerstone-mock（TCP 网关 + 网页），便于本地开发 mock_web_static。

用法（在仓库根目录或 CornerstoneMock 目录下）::

    python -m cornerstone_mock.dev_mock_web
    python -m cornerstone_mock.dev_mock_web --web-port 9000

配置文件查找顺序：

1. 环境变量 ``CORNERSTONE_MOCK_CONFIG``（文件路径）
2. 当前工作目录下的 ``cornerstone-mock.config.json``
3. 当前工作目录下的 ``cornerstone-mock.config.example.json``
4. 开发仓库内 ``CornerstoneMock/cornerstone-mock.config.example.json``（相对本文件推断）

其余参数与 ``cornerstone-mock`` 相同，会追加在 ``-c`` 之后，用于覆盖文件中的字段。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _resolve_default_config() -> Optional[Path]:
    env = (os.environ.get("CORNERSTONE_MOCK_CONFIG") or "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    cwd = Path.cwd()
    for name in ("cornerstone-mock.config.json", "cornerstone-mock.config.example.json"):
        cand = cwd / name
        if cand.is_file():
            return cand
    # .../CornerstoneMock/src/cornerstone_mock/dev_mock_web.py -> parents[2] == CornerstoneMock
    here = Path(__file__).resolve()
    repo_example = here.parents[2] / "cornerstone-mock.config.example.json"
    if repo_example.is_file():
        return repo_example
    return None


def main() -> int:
    cfg = _resolve_default_config()
    if cfg is None:
        print(
            "[dev-mock-web] 未找到配置文件。请设置 CORNERSTONE_MOCK_CONFIG，"
            "或在当前目录放置 cornerstone-mock.config.json / cornerstone-mock.config.example.json。",
            file=sys.stderr,
        )
        return 2
    sys.argv = [sys.argv[0], "-c", str(cfg), *sys.argv[1:]]
    from cornerstone_mock.mock_server import main as mock_main

    return mock_main()


if __name__ == "__main__":
    raise SystemExit(main())
