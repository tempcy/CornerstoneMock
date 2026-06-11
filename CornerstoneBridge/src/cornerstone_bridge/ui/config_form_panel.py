"""Bridge UI 配置页：按 example.toml 分类、四等分网格排布。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config_tooltips import load_bridge_config_tooltips

_GRID_COLS = 4


@dataclass(frozen=True)
class _FieldSpec:
    key: str
    label: str
    kind: str  # bool | int | float | text | combo
    span: int = 1  # 1 | 2 | 4，占窗口宽度的份数
    combo_items: Tuple[str, ...] = ()
    int_range: Tuple[int, int] = (0, 65535)
    float_range: Tuple[float, float] = (0.0, 86400.0)
    float_step: float = 0.1
    zero_label: str = ""


@dataclass
class _SectionSpec:
    title: str
    fields: Tuple[_FieldSpec, ...]


def _sections() -> Tuple[_SectionSpec, ...]:
    z = _FieldSpec
    return (
        _SectionSpec(
            "TCP 网关",
            (
                z("host", "监听地址", "text", 2),
                z("port", "监听端口", "int", 1, int_range=(1, 65535)),
            ),
        ),
        _SectionSpec(
            "对内 REST API",
            (
                z("bridge_api_host", "API 地址", "text", 2),
                z("bridge_api_port", "API 端口", "int", 1, int_range=(1, 65535)),
            ),
        ),
        _SectionSpec(
            "上游仪器",
            (
                z("upstream_host", "仪器地址", "text", 2),
                z("upstream_port", "仪器端口", "int", 1, int_range=(1, 65535)),
                z(
                    "upstream_heartbeat_interval",
                    "心跳间隔 (秒)",
                    "float",
                    1,
                    float_range=(0.0, 3600.0),
                    zero_label="禁用",
                ),
                z(
                    "upstream_inner_reassembly_timeout",
                    "内层重组超时 (秒)",
                    "float",
                    1,
                    float_range=(0.0, 600.0),
                    zero_label="不等待",
                ),
                z("upstream_recv_idle_clear", "recv 空闲清缓冲 (秒)", "int", 1, int_range=(0, 3600)),
                z("upstream_heartbeat_fail_max", "心跳失败上限", "int", 1, int_range=(1, 20)),
                z("upstream_command_fail_max", "指令失败上限", "int", 1, int_range=(1, 20)),
                z(
                    "upstream_client_forward_timeout",
                    "TCP 转发超时 (秒)",
                    "int",
                    1,
                    int_range=(5, 600),
                ),
                z(
                    "upstream_heartbeat_wait_timeout",
                    "Heartbeat 等待 (秒)",
                    "int",
                    1,
                    int_range=(0, 600),
                    zero_label="自动",
                ),
                z(
                    "upstream_activity_stale_seconds",
                    "无活动回收 (秒)",
                    "int",
                    1,
                    int_range=(0, 86400),
                    zero_label="自动",
                ),
                z(
                    "upstream_read_cancel_timeout",
                    "读循环 cancel (秒)",
                    "int",
                    1,
                    int_range=(1, 60),
                ),
                z(
                    "upstream_stale_check_interval",
                    "活性巡检间隔 (秒)",
                    "int",
                    1,
                    int_range=(0, 600),
                    zero_label="关闭",
                ),
                z("upstream_auto_reconnect", "自动重连", "bool", 1),
            ),
        ),
        _SectionSpec(
            "协议与队列",
            (
                z("encoding", "帧编码", "combo", 1, combo_items=("utf16", "utf-16-le")),
                z("add_samples_queue_size", "加样队列容量", "int", 1, int_range=(1, 100000)),
                z("persist_add_samples_queue", "持久化加样队列", "bool", 1),
                z("add_samples_queue_persist_file", "队列持久化文件", "text", 4),
                z("privileged_add_samples_host", "特权加样 IP", "text", 2),
                z("blocked_connect_hosts", "阻止连接 IP", "text", 4),
                z("blocked_logon_hosts", "阻止登录 IP", "text", 4),
            ),
        ),
        _SectionSpec(
            "会话与转发",
            (
                z("no_synthetic_logon", "禁用合成 Logon", "bool", 1),
                z(
                    "async_message_interval",
                    "异步消息间隔 (秒)",
                    "float",
                    1,
                    float_range=(0.0, 3600.0),
                    zero_label="仅连接时",
                ),
                z("instrument_long_connection", "仪器长连接", "bool", 1),
            ),
        ),
        _SectionSpec(
            "网页代登",
            (
                z("web_user", "Web 用户名", "text", 2),
                z("web_password", "Web 密码", "text", 2),
            ),
        ),
        _SectionSpec(
            "日志",
            (
                z(
                    "log_level",
                    "控制台级别",
                    "combo",
                    1,
                    combo_items=("debug", "info", "warning", "error"),
                ),
                z("log_verbose_gateway", "网关详细日志", "bool", 1),
                z("log_file", "日志文件路径", "text", 4),
                z(
                    "log_file_level",
                    "文件级别",
                    "combo",
                    1,
                    combo_items=("debug", "info", "warning", "error"),
                ),
                z("log_file_max_mb", "日志文件上限 (MB)", "int", 1, int_range=(1, 10240)),
                z("log_file_backup_count", "日志备份数", "int", 1, int_range=(0, 100)),
                z(
                    "log_throttle_interval_s",
                    "重复日志节流 (秒)",
                    "float",
                    1,
                    float_range=(0.0, 3600.0),
                    zero_label="禁用",
                ),
            ),
        ),
        _SectionSpec(
            "COMPAC 串口",
            (
                z("compac_enabled", "启用 COMPAC", "bool", 1),
                z("compac_port", "串口设备", "text", 2),
                z("compac_baud_rate", "波特率", "int", 1, int_range=(300, 921600)),
                z("compac_data_bits", "数据位", "int", 1, int_range=(5, 8)),
                z("compac_parity", "校验位", "combo", 1, combo_items=("N", "E", "O")),
                z("compac_stop_bits", "停止位", "int", 1, int_range=(1, 2)),
                z("compac_listen_enabled", "自动监听串口", "bool", 1),
                z(
                    "compac_timeout_seconds",
                    "握手/查询超时 (秒)",
                    "float",
                    1,
                    float_range=(0.1, 600.0),
                ),
                z("compac_retry_count", "握手重试次数", "int", 1, int_range=(0, 20)),
                z("compac_queue_max", "试样队列上限", "int", 1, int_range=(1, 1000)),
                z(
                    "compac_recv_idle_clear_seconds",
                    "接收空闲清空 (秒)",
                    "float",
                    1,
                    float_range=(0.0, 600.0),
                ),
                z("compac_verify_bct_cks", "校验 BCT/CKS", "bool", 1),
                z("compac_reply_a_request", "自动答复 AStatus", "bool", 1),
                z("compac_reply_status_chars", "默认状态字符", "text", 2),
                z("compac_reply_status_error", "默认错误码", "int", 1, int_range=(0, 99)),
            ),
        ),
    )


class _ConfigFieldCell(QWidget):
    def __init__(
        self,
        spec: _FieldSpec,
        editor: QWidget,
        tooltip: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.key = spec.key
        editor.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QLabel(spec.label)
        label.setToolTip(tooltip)
        editor.setToolTip(tooltip)
        self.setToolTip(tooltip)
        layout.addWidget(label)
        layout.addWidget(editor)


class _QuadrantGrid(QWidget):
    """固定四列网格：每项占 1、2 或 4 列，不在 resize 时重排。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(10)

    def add_cells(self, cells: List[Tuple[_ConfigFieldCell, int]]) -> None:
        row = 0
        col = 0
        for cell, span in cells:
            span = min(max(int(span), 1), _GRID_COLS)
            if span >= _GRID_COLS:
                if col > 0:
                    row += 1
                    col = 0
                self._grid.addWidget(cell, row, 0, 1, _GRID_COLS)
                row += 1
                col = 0
                continue
            if col + span > _GRID_COLS:
                row += 1
                col = 0
            self._grid.addWidget(cell, row, col, 1, span)
            col += span
            if col >= _GRID_COLS:
                row += 1
                col = 0
        for c in range(_GRID_COLS):
            self._grid.setColumnStretch(c, 1)


class ConfigFormPanel(QWidget):
    """完整 Bridge 配置表单。"""

    _HOST_LIST_KEYS = frozenset({"blocked_connect_hosts", "blocked_logon_hosts"})

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tooltips = load_bridge_config_tooltips()
        self._getters: Dict[str, Callable[[], Any]] = {}
        self._setters: Dict[str, Callable[[Any], None]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(12)

        for section in _sections():
            group = QGroupBox(section.title)
            grid = _QuadrantGrid()
            group_layout = QVBoxLayout(group)
            group_layout.addWidget(grid)
            cells: List[Tuple[_ConfigFieldCell, int]] = []
            for spec in section.fields:
                cell, _editor = self._make_field(spec)
                cells.append((cell, spec.span))
            grid.add_cells(cells)
            inner_layout.addWidget(group)

        inner_layout.addStretch(1)

    def _tip(self, key: str) -> str:
        return self._tooltips.get(key, "")

    def _make_field(self, spec: _FieldSpec) -> Tuple[_ConfigFieldCell, QWidget]:
        tooltip = self._tip(spec.key)
        editor: QWidget

        if spec.kind == "bool":
            editor = QCheckBox()
            self._getters[spec.key] = lambda e=editor: bool(e.isChecked())
            self._setters[spec.key] = lambda v, e=editor: e.setChecked(bool(v))
        elif spec.kind == "int":
            spin = QSpinBox()
            spin.setRange(spec.int_range[0], spec.int_range[1])
            if spec.zero_label:
                spin.setSpecialValueText(spec.zero_label)
            editor = spin
            self._getters[spec.key] = lambda s=spin: int(s.value())
            self._setters[spec.key] = lambda v, s=spin: s.setValue(int(v))
        elif spec.kind == "float":
            spin = QDoubleSpinBox()
            spin.setRange(spec.float_range[0], spec.float_range[1])
            spin.setDecimals(2 if spec.float_step < 1 else 1)
            spin.setSingleStep(spec.float_step)
            if spec.zero_label:
                spin.setSpecialValueText(spec.zero_label)
            editor = spin
            self._getters[spec.key] = lambda s=spin: float(s.value())
            self._setters[spec.key] = lambda v, s=spin: s.setValue(float(v))
        elif spec.kind == "combo":
            combo = QComboBox()
            combo.addItems(list(spec.combo_items))
            editor = combo
            self._getters[spec.key] = lambda c=combo: str(c.currentText())
            self._setters[spec.key] = lambda v, c=combo: c.setCurrentText(
                str(v).lower() if spec.key.startswith("log_") else str(v)
            )
        else:
            line = QLineEdit()
            if spec.key == "web_password":
                line.setEchoMode(QLineEdit.EchoMode.Password)
            if spec.key in self._HOST_LIST_KEYS:
                line.setPlaceholderText("多个 IP 用逗号分隔")
            editor = line
            self._getters[spec.key] = lambda e=line: str(e.text())
            self._setters[spec.key] = lambda v, e=line: e.setText("" if v is None else str(v))

        cell = _ConfigFieldCell(spec, editor, tooltip)
        return cell, editor

    @staticmethod
    def _format_host_list(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            text = raw.strip()
            if text in ("[]", '[""]'):
                return ""
            return text
        if isinstance(raw, (list, tuple)):
            parts = [
                str(x).strip()
                for x in raw
                if str(x).strip() and str(x).strip() not in ("[]", '[""]')
            ]
            return ", ".join(parts)
        return str(raw)

    @staticmethod
    def _parse_host_list(text: str) -> List[str]:
        return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]

    def load_from_dict(self, cfg: Dict[str, Any]) -> None:
        for key, setter in self._setters.items():
            if key not in cfg:
                continue
            value = cfg[key]
            if key in self._HOST_LIST_KEYS:
                setter(self._format_host_list(value))
            elif key in ("log_level", "log_file_level"):
                setter(str(value or "info").lower())
            else:
                setter(value)

    def to_updates(self) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        for key, getter in self._getters.items():
            value = getter()
            if key in self._HOST_LIST_KEYS:
                updates[key] = self._parse_host_list(str(value))
            else:
                updates[key] = value
        return updates

    def get(self, key: str, default: Any = None) -> Any:
        getter = self._getters.get(key)
        if getter is None:
            return default
        return getter()
