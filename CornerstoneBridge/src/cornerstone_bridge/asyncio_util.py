"""asyncio 关闭辅助（兼容 Python 3.14 深层 Task 子树）。"""
from __future__ import annotations

import asyncio
import contextlib


async def async_yield_shutdown() -> None:
    """
    让事件循环再跑几轮以便 ``server.close()`` / ``hub.shutdown_gracefully()`` 收尾。

    勿对 ``asyncio.all_tasks()`` 批量 ``Task.cancel()``：在 3.14 上会沿子任务链
    递归 cancel，易触发 ``RecursionError: maximum recursion depth exceeded``。
    """
    for _ in range(3):
        await asyncio.sleep(0)
