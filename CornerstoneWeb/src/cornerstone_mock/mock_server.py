"""兼容入口：重定向到 cornerstone-web-dev。"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "[cornerstone-mock] 已重命名为 cornerstone-web / cornerstone-bridge。"
        " 请使用: cornerstone-web-dev 或 cornerstone-bridge + cornerstone-web",
        file=sys.stderr,
    )
    from cornerstone_web.dev_web import main as dev_main

    return dev_main()
