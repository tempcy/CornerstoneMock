from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from .paths import appdata_cornerstone_dir, expand_config_path
from .parsers import _xml_local_tag

_LOG_ROOT = "cornerstone.bridge"
_LOG_FMT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Remote Query / 轮询类 XML 根标签（INFO 默认不写文件）
RQ_XML_TAGS = frozenset(
    {
        "Status",
        "Prerequisites",
        "RemoteControlState",
        "Heartbeat",
        "Version",
        "InstrumentInfo",
        "Sets",
        "SetReps",
        "RepPlot",
        "RepDetail",
        "SetStats",
        "SetsEx",
        "LastRemoteAddedSets",
        "Ambients",
        "Ambient",
        "Solenoids",
        "Switches",
        "ValveStates",
        "Counters",
        "Counter",
        "AutomationStatus",
        "SystemParameters",
        "Transports",
        "Transport",
        "Methods",
        "Method",
        "Standards",
        "Standard",
        "DigitalIO",
        "StatusCheck",
        "RemoteImportSets",
    }
)

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_throttle = None  # set in setup


class _RQInfoFileFilter(logging.Filter):
    """RQ 类 INFO 不写入文件。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "rq", False) and record.levelno == logging.INFO:
            return False
        return True


class _RQInfoConsoleFilter(logging.Filter):
    """未开启 verbose 时，控制台也不显示 RQ INFO。"""

    def __init__(self, verbose: bool) -> None:
        super().__init__()
        self._verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        if self._verbose:
            return True
        if getattr(record, "rq", False) and record.levelno == logging.INFO:
            return False
        return True


class LogThrottle:
    def __init__(self, interval_s: float = 300.0) -> None:
        self._interval = max(float(interval_s), 1.0)
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def consume(self, key: str) -> Tuple[bool, int]:
        now = time.monotonic()
        last = self._last.get(key, 0.0)
        if now - last >= self._interval:
            suppressed = self._suppressed.pop(key, 0)
            self._last[key] = now
            return True, suppressed
        self._suppressed[key] = self._suppressed.get(key, 0) + 1
        return False, 0


def is_rq_xml_tag(tag: str) -> bool:
    local = _xml_local_tag(tag or "")
    if not local:
        return False
    if local in RQ_XML_TAGS:
        return True
    return local.lower() == "heartbeat"


def _parse_level(name: str, default: int = logging.INFO) -> int:
    return _LEVEL_MAP.get((name or "").strip().lower(), default)


def default_bridge_log_file() -> Path:
    return appdata_cornerstone_dir() / "logs" / "bridge.log"


def _path_is_under(child: Path, parent: Path) -> bool:
    """``Path.is_relative_to`` 的 Python 3.8 兼容实现（PyInstaller 目标机常为 3.8）。"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_bridge_log_file_path(
    log_file: str,
    *,
    config_dir: Optional[Path] = None,
) -> Path:
    """
    解析轮转日志路径。服务以 LocalSystem 运行时 ``%APPDATA%`` 会落到 systemprofile；
    若已通过 ``-c`` 指定用户配置目录，则优先写入该目录下的 ``logs\\``。
    """
    if config_dir is not None:
        config_dir = Path(config_dir).resolve()
    if (log_file or "").strip():
        expanded = expand_config_path(log_file)
        p = Path(expanded)
        if config_dir is not None:
            sys_appdata = (Path(os.environ.get("APPDATA", "")) / "CornerstoneMock").resolve()
            if _path_is_under(p, sys_appdata) or "systemprofile" in str(p).lower():
                return config_dir / "logs" / p.name
        return p
    if config_dir is not None:
        return config_dir / "logs" / "bridge.log"
    return default_bridge_log_file()


def setup_bridge_logging(
    *,
    log_level: str = "info",
    log_verbose_gateway: bool = False,
    log_file: str = "",
    log_file_level: str = "info",
    log_file_max_bytes: int = 2 * 1024 * 1024,
    log_file_backup_count: int = 3,
    log_throttle_interval_s: float = 300.0,
    config_dir: Optional[Path] = None,
) -> None:
    global _throttle
    _throttle = LogThrottle(log_throttle_interval_s)

    root = logging.getLogger(_LOG_ROOT)
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.propagate = False

    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    # StreamHandler() 默认绑定 stderr，NSSM 会把全部 INFO 记进 *-stderr.log
    out = sys.stdout if sys.stdout is not None else sys.stderr
    console = logging.StreamHandler(out)
    console.setLevel(_parse_level(log_level, logging.INFO))
    console.setFormatter(formatter)
    console.addFilter(_RQInfoConsoleFilter(log_verbose_gateway))
    root.addHandler(console)

    file_path = resolve_bridge_log_file_path(log_file, config_dir=config_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=max(int(log_file_max_bytes), 64 * 1024),
        backupCount=max(int(log_file_backup_count), 1),
        encoding="utf-8",
    )
    file_handler.setLevel(_parse_level(log_file_level, logging.INFO))
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RQInfoFileFilter())
    root.addHandler(file_handler)

    logging.getLogger(_LOG_ROOT).info(
        "logging initialized console=%s verbose_gateway=%s file=%s file_level=%s",
        log_level,
        log_verbose_gateway,
        file_path,
        log_file_level,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOG_ROOT}.{name}")


def log_rq(logger: logging.Logger, tag: str, msg: str, *args: object) -> None:
    logger.info(msg, *args, extra={"rq": True, "rq_tag": tag or ""})


def log_gateway_xml(
    logger: logging.Logger,
    direction: str,
    text: str,
    *,
    cookie: str = "",
    web_rq: bool = False,
) -> None:
    """网关 XML 帧：RQ 类为 INFO+rq（不写文件）；其它为 INFO 可写文件。"""
    tag = _xml_local_tag(_root_tag_safe(text))
    ck = cookie or _cookie_safe(text)
    summary = f"{direction} tag={tag or '?'} cookie={ck!r} bytes={len(text)}"
    if web_rq or is_rq_xml_tag(tag):
        log_rq(logger, tag, summary)
    else:
        logger.info(summary)


def log_throttled_warning(logger: logging.Logger, key: str, msg: str, *args: object) -> None:
    global _throttle
    if _throttle is None:
        logger.warning(msg, *args)
        return
    ok, suppressed = _throttle.consume(key)
    if not ok:
        return
    if suppressed:
        logger.warning(msg + " (另有 %d 条同类已抑制)", *args, suppressed)
    else:
        logger.warning(msg, *args)


def _root_tag_safe(text: str) -> str:
    from .protocol import _root_tag

    return _root_tag(text)


def _cookie_safe(text: str) -> str:
    from .protocol import _parse_cookie_from_payload

    return _parse_cookie_from_payload(text)
