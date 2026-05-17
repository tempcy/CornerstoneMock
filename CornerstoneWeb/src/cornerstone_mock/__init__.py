"""已弃用：请使用 ``cornerstone_web`` 与 ``cornerstone-bridge``。"""
from __future__ import annotations

import warnings

warnings.warn(
    "cornerstone_mock 已拆分为 cornerstone_web 与 cornerstone_bridge；"
    "请安装 cornerstone-web / cornerstone-bridge。",
    DeprecationWarning,
    stacklevel=2,
)
