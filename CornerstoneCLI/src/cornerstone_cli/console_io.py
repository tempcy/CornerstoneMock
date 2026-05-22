"""Windows 服务 / NSSM 下 stdout 常为 cp1252，避免 print 中文或 Unicode 符号崩溃。"""
from __future__ import annotations

import io
import sys


def configure_stdio_utf8() -> None:
    """将 stdout/stderr 设为 UTF-8（NSSM 捕获日志前应先调用，避免中文乱码）。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
            elif hasattr(stream, "buffer"):
                wrapper = io.TextIOWrapper(
                    stream.buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                )
                if stream is sys.stdout:
                    sys.stdout = wrapper
                else:
                    sys.stderr = wrapper
        except Exception:
            pass
