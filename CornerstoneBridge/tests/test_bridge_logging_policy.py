"""bridge_logging：策略丢弃日志受 log_verbose_gateway 约束。"""
import logging

from cornerstone_bridge.bridge_logging import (
    _RQInfoConsoleFilter,
    _RQInfoFileFilter,
    log_policy_drop,
)


def test_policy_drop_suppressed_when_not_verbose() -> None:
    console = _RQInfoConsoleFilter(verbose=False)
    file_f = _RQInfoFileFilter(verbose=False)
    record = logging.LogRecord(
        name="cornerstone.bridge.client",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="client rejected (connect blocked IP %r): %s",
        args=("10.0.0.1", "('10.0.0.1', 1)"),
        exc_info=None,
    )
    record.policy_drop = True
    assert console.filter(record) is False
    assert file_f.filter(record) is False


def test_policy_drop_visible_when_verbose() -> None:
    console = _RQInfoConsoleFilter(verbose=True)
    file_f = _RQInfoFileFilter(verbose=True)
    record = logging.LogRecord(
        name="cornerstone.bridge.client",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Logon dropped",
        args=(),
        exc_info=None,
    )
    record.policy_drop = True
    assert console.filter(record) is True
    assert file_f.filter(record) is True


def test_log_policy_drop_sets_extra() -> None:
    logger = logging.getLogger("cornerstone.bridge.test_policy")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger.addHandler(_Capture())
    log_policy_drop(logger, "client rejected test")
    assert len(captured) == 1
    assert getattr(captured[0], "policy_drop", False) is True
