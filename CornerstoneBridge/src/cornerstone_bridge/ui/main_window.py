from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .bridge_api import BridgeApiClient, BridgeApiError
from .build_info import packaging_time_label


class _MenuToolButtonSync(QObject):
    """MenuButtonPopup：窄箭头区；主区文字居中；下拉菜单与整钮同宽。"""

    _MENU_ARROW_WIDTH = 28

    def __init__(self, btn: QToolButton, menu: QMenu) -> None:
        super().__init__(btn)
        self._btn = btn
        self._menu = menu
        menu.aboutToShow.connect(self._sync_menu_width)
        btn.installEventFilter(self)
        self._apply_style()

    def _sync_menu_width(self) -> None:
        w = self._btn.width()
        if w > 0:
            self._menu.setFixedWidth(w)

    def _apply_style(self) -> None:
        aw = self._MENU_ARROW_WIDTH
        self._btn.setStyleSheet(
            "QToolButton { text-align: center; }"
            "QToolButton::menu-button {"
            f" width: {aw}px;"
            " subcontrol-origin: border;"
            " subcontrol-position: right center;"
            " border-left: 1px solid palette(mid);"
            " }"
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._btn and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.StyleChange,
        ):
            self._apply_style()
        return False
from .config_io import (
    api_base_url,
    load_config_dict,
    log_file_path,
    merge_config_update,
    resolve_config_path,
    save_config_dict,
)
from .win_admin import (
    windows_service_start,
    windows_service_state,
    windows_service_stop,
)


class MainWindow(QMainWindow):
    _APP_TITLE = "Cornerstone Bridge 控制台"
    _LOG_LEVEL_RE = re.compile(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s"
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self._build_window_title())
        self.resize(920, 640)

        self._config_path = resolve_config_path()
        self._cfg: Dict[str, Any] = load_config_dict(self._config_path)
        self._api = BridgeApiClient(api_base_url(self._cfg))
        self._log_offset = 0
        self._connections_syncing = False

        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        bar = self.statusBar()
        self._status_bridge = QLabel("Bridge: —")
        self._status_config = QLabel(f"配置: {self._config_path}")
        bar.addWidget(self._status_bridge, 1)
        bar.addPermanentWidget(self._status_config)

        self._build_monitor_tab()
        self._build_queue_tab()
        self._build_config_tab()
        self._build_logs_tab()

        self._poll = QTimer(self)
        self._poll.setInterval(2000)
        self._poll.timeout.connect(self._on_poll)
        self._poll.start()

        self._log_poll = QTimer(self)
        self._log_poll.setInterval(1500)
        self._log_poll.timeout.connect(self._refresh_logs)

        QTimer.singleShot(0, self._refresh_all)

    def show_normal(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    @classmethod
    def _build_window_title(cls) -> str:
        try:
            ver = version("cornerstone-bridge")
        except PackageNotFoundError:
            ver = ""
        parts = [cls._APP_TITLE]
        if ver:
            parts.append(f"v{ver}")
        built = packaging_time_label()
        if built:
            parts.append(f"打包 {built}")
        return "  ".join(parts)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        s = max(0, int(seconds))
        if s < 60:
            return f"{s} 秒"
        m, rem = divmod(s, 60)
        if m < 60:
            return f"{m} 分 {rem} 秒"
        h, rem_m = divmod(m, 60)
        return f"{h} 时 {rem_m} 分 {rem} 秒"

    @staticmethod
    def _menu_tool_button(
        text: str,
        default_cb,
        menu_items: List[tuple[str, object]],
    ) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = QMenu(btn)
        for label, cb in menu_items:
            menu.addAction(label, cb)
        btn.setMenu(menu)
        btn.clicked.connect(default_cb)
        MainWindow._style_action_button(btn)
        btn._menu_sync = _MenuToolButtonSync(btn, menu)  # type: ignore[attr-defined]
        return btn

    @staticmethod
    def _style_action_button(btn: QWidget) -> None:
        btn.setMinimumHeight(34)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # --- 连接监控 ---
    def _build_monitor_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        self._btn_refresh_mon = QPushButton("立即刷新")
        self._btn_refresh_mon.clicked.connect(self._refresh_monitor)
        self._chk_auto = QCheckBox("自动刷新 (2s)")
        self._chk_auto.setChecked(True)
        top.addWidget(self._btn_refresh_mon)
        top.addWidget(self._chk_auto)
        top.addStretch()
        layout.addLayout(top)

        sw = QGroupBox("连接开关")
        sw_lay = QVBoxLayout(sw)
        up_row = QHBoxLayout()
        up_row.addWidget(QLabel("上游仪器"))
        self._chk_upstream_conn = QCheckBox("保持连接")
        self._chk_upstream_conn.setChecked(True)
        self._chk_upstream_conn.toggled.connect(self._on_upstream_conn_toggled)
        up_row.addWidget(self._chk_upstream_conn)
        self._lbl_upstream = QLabel("—")
        up_row.addWidget(self._lbl_upstream, 1)
        sw_lay.addLayout(up_row)

        gw_row = QHBoxLayout()
        gw_row.addWidget(QLabel("TCP 网关"))
        self._chk_tcp_gateway = QCheckBox("接受客户端")
        self._chk_tcp_gateway.setChecked(True)
        self._chk_tcp_gateway.toggled.connect(self._on_tcp_gateway_toggled)
        gw_row.addWidget(self._chk_tcp_gateway)
        self._lbl_tcp_listen = QLabel("—")
        gw_row.addWidget(self._lbl_tcp_listen, 1)
        sw_lay.addLayout(gw_row)
        layout.addWidget(sw)

        grid = QGridLayout()
        self._lbl_api_listen = QLabel("—")
        self._lbl_queue = QLabel("—")
        self._lbl_rcs = QLabel("—")
        self._lbl_encoding = QLabel("—")
        grid.addWidget(QLabel("REST API"), 0, 0)
        grid.addWidget(self._lbl_api_listen, 0, 1)
        grid.addWidget(QLabel("通讯队列"), 0, 2)
        grid.addWidget(self._lbl_queue, 0, 3)
        grid.addWidget(QLabel("远程控制状态"), 1, 0)
        grid.addWidget(self._lbl_rcs, 1, 1, 1, 3)
        grid.addWidget(QLabel("编码"), 2, 0)
        grid.addWidget(self._lbl_encoding, 2, 1)
        layout.addLayout(grid)

        layout.addWidget(QLabel("TCP 远程客户端"))
        self._tbl_clients = QTableWidget(0, 6)
        self._tbl_clients.setHorizontalHeaderLabels(
            ["客户端地址", "连接时长", "特权 IP", "登录用户", "收到", "发出"]
        )
        hdr = self._tbl_clients.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._tbl_clients)

        self._tabs.addTab(w, "连接监控")

    # --- 队列 ---
    def _build_queue_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        row = QHBoxLayout()
        self._btn_refresh_q = QPushButton("刷新队列")
        self._btn_refresh_q.clicked.connect(self._refresh_queue)
        row.addWidget(self._btn_refresh_q)
        row.addStretch()
        layout.addLayout(row)

        self._tbl_queue = QTableWidget(0, 5)
        self._tbl_queue.setHorizontalHeaderLabels(
            ["ID", "收到时间", "来源", "样品名", "描述"]
        )
        self._tbl_queue.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._tbl_queue.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._tbl_queue)
        self._tabs.addTab(w, "通讯队列")

    # --- 配置 ---
    def _build_config_tab(self) -> None:
        w = QWidget()
        outer = QVBoxLayout(w)

        form_box = QGroupBox("网关与上游")
        form = QFormLayout(form_box)
        self._ed_host = QLineEdit()
        self._sp_port = QSpinBox()
        self._sp_port.setRange(1, 65535)
        self._ed_api_host = QLineEdit()
        self._sp_api_port = QSpinBox()
        self._sp_api_port.setRange(1, 65535)
        self._ed_upstream_host = QLineEdit()
        self._sp_upstream_port = QSpinBox()
        self._sp_upstream_port.setRange(1, 65535)
        self._ed_priv_host = QLineEdit()
        form.addRow("TCP 监听 host", self._ed_host)
        form.addRow("TCP 监听 port", self._sp_port)
        form.addRow("REST host", self._ed_api_host)
        form.addRow("REST port", self._sp_api_port)
        form.addRow("上游 host", self._ed_upstream_host)
        form.addRow("上游 port", self._sp_upstream_port)
        form.addRow("特权 AddSamples IP", self._ed_priv_host)
        outer.addWidget(form_box)

        toggles = QGroupBox("功能开关")
        tlay = QFormLayout(toggles)
        self._chk_long_conn = QCheckBox("仪器长连接 (instrument_long_connection)")
        self._chk_auto_reco = QCheckBox("上游自动重连 (upstream_auto_reconnect)")
        self._sp_recv_idle_clear = QSpinBox()
        self._sp_recv_idle_clear.setRange(0, 3600)
        self._sp_recv_idle_clear.setSuffix(" s")
        self._sp_hb_fail_max = QSpinBox()
        self._sp_hb_fail_max.setRange(1, 20)
        self._sp_cmd_fail_max = QSpinBox()
        self._sp_cmd_fail_max.setRange(1, 20)
        self._sp_client_fwd_timeout = QSpinBox()
        self._sp_client_fwd_timeout.setRange(5, 600)
        self._sp_client_fwd_timeout.setSuffix(" s")
        self._sp_hb_wait_timeout = QSpinBox()
        self._sp_hb_wait_timeout.setRange(0, 600)
        self._sp_hb_wait_timeout.setSuffix(" s")
        self._sp_hb_wait_timeout.setSpecialValueText("自动")
        self._sp_activity_stale = QSpinBox()
        self._sp_activity_stale.setRange(0, 3600)
        self._sp_activity_stale.setSuffix(" s")
        self._sp_activity_stale.setSpecialValueText("自动")
        self._sp_read_cancel_timeout = QSpinBox()
        self._sp_read_cancel_timeout.setRange(1, 60)
        self._sp_read_cancel_timeout.setSuffix(" s")
        self._sp_stale_check_interval = QSpinBox()
        self._sp_stale_check_interval.setRange(0, 600)
        self._sp_stale_check_interval.setSuffix(" s")
        self._sp_stale_check_interval.setSpecialValueText("关闭")
        self._chk_no_syn_logon = QCheckBox("禁用合成 Logon (no_synthetic_logon)")
        self._chk_verbose_gw = QCheckBox("网关 XML 详细日志 (log_verbose_gateway，应用后立即生效)")
        self._chk_persist_q = QCheckBox("持久化队列 (persist_add_samples_queue)")
        self._sp_queue_max = QSpinBox()
        self._sp_queue_max.setRange(1, 256)
        tlay.addRow(self._chk_long_conn)
        tlay.addRow(self._chk_auto_reco)
        tlay.addRow("recv 空闲清缓冲", self._sp_recv_idle_clear)
        tlay.addRow("心跳失败上限", self._sp_hb_fail_max)
        tlay.addRow("指令失败上限", self._sp_cmd_fail_max)
        tlay.addRow("TCP转发超时", self._sp_client_fwd_timeout)
        tlay.addRow("Heartbeat等待", self._sp_hb_wait_timeout)
        tlay.addRow("无活动回收", self._sp_activity_stale)
        tlay.addRow("读循环cancel", self._sp_read_cancel_timeout)
        tlay.addRow("活性巡检间隔", self._sp_stale_check_interval)
        tlay.addRow(self._chk_no_syn_logon)
        tlay.addRow(self._chk_verbose_gw)
        tlay.addRow(self._chk_persist_q)
        tlay.addRow("队列容量", self._sp_queue_max)
        outer.addWidget(toggles)

        log_box = QGroupBox("日志")
        lform = QFormLayout(log_box)
        self._cmb_log_level = QComboBox()
        self._cmb_log_level.addItems(["debug", "info", "warning", "error"])
        self._ed_log_file = QLineEdit()
        lform.addRow("log_level", self._cmb_log_level)
        lform.addRow("log_file", self._ed_log_file)
        outer.addWidget(log_box)

        act_box = QGroupBox("配置与服务")
        btn_grid = QGridLayout(act_box)
        btn_grid.setColumnStretch(0, 1)
        btn_grid.setColumnStretch(1, 1)
        btn_grid.setHorizontalSpacing(12)
        btn_grid.setVerticalSpacing(10)

        self._btn_reload_cfg = QPushButton("重新加载")
        self._style_action_button(self._btn_reload_cfg)
        self._btn_save_cfg = self._menu_tool_button(
            "保存配置",
            self._save_config_file,
            [("应用到运行中的 Bridge…", self._apply_runtime_settings)],
        )
        self._btn_open_cfg_dir = QPushButton("打开配置目录")
        self._style_action_button(self._btn_open_cfg_dir)
        self._btn_restart_svc = self._menu_tool_button(
            "重启服务",
            self._restart_bridge_service,
            [("停止 Bridge 服务", self._stop_bridge_service)],
        )

        btn_grid.addWidget(self._btn_reload_cfg, 0, 0)
        btn_grid.addWidget(self._btn_save_cfg, 0, 1)
        btn_grid.addWidget(self._btn_open_cfg_dir, 1, 0)
        btn_grid.addWidget(self._btn_restart_svc, 1, 1)
        outer.addWidget(act_box)
        outer.addStretch()

        self._btn_reload_cfg.clicked.connect(self._load_config_form)
        self._btn_open_cfg_dir.clicked.connect(self._open_config_dir)

        self._tabs.addTab(w, "配置")
        self._load_config_form(reload_api=False)

    # --- 日志 ---
    def _build_logs_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        row = QHBoxLayout()
        self._btn_clear_log_view = QPushButton("清空视图")
        self._btn_clear_log_view.clicked.connect(lambda: self._log_view.clear())
        row.addWidget(self._btn_clear_log_view)
        row.addWidget(QLabel("级别:"))
        self._chk_log_debug = QCheckBox("DEBUG")
        self._chk_log_info = QCheckBox("INFO")
        self._chk_log_warning = QCheckBox("WARNING")
        self._chk_log_error = QCheckBox("ERROR")
        for chk in (
            self._chk_log_debug,
            self._chk_log_info,
            self._chk_log_warning,
            self._chk_log_error,
        ):
            chk.setChecked(True)
            chk.toggled.connect(self._on_log_filter_changed)
            row.addWidget(chk)
        row.addStretch()
        layout.addLayout(row)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._log_view)
        layout.addWidget(
            QLabel(
                "说明：RQ 类 XML（Status / Sets / Heartbeat 等）默认不写入日志文件；"
                "勾选「详细日志」并点「应用到运行中的 Bridge」后才会出现在此。"
                "向上滚动查看历史时，新日志不会自动跳到底部。"
            )
        )
        self._tabs.addTab(w, "日志")

    def _on_poll(self) -> None:
        self._update_bridge_connection_status()
        if not self._chk_auto.isChecked():
            return
        if self._tabs.currentIndex() == 0:
            self._refresh_monitor()
        elif self._tabs.currentIndex() == 1:
            self._refresh_queue()
        if self._tabs.currentIndex() == 3:
            self._refresh_logs()

    def _refresh_all(self) -> None:
        self._reload_api_client()
        self._refresh_monitor()
        self._refresh_queue()
        self._refresh_logs()

    def _update_bridge_connection_status(self) -> None:
        """左下状态栏：探测 REST API 是否可达（与当前页签无关，随轮询刷新）。"""
        ok, err = self._api.ping()
        base = api_base_url(self._cfg)
        if ok:
            self._status_bridge.setText(f"Bridge: 已连接 {base}")
            self._status_bridge.setStyleSheet("")
        else:
            self._status_bridge.setText(f"Bridge: 未连接 — {err}")
            self._status_bridge.setStyleSheet("color: #c0392b;")

    def _reload_api_client(self) -> None:
        self._cfg = load_config_dict(self._config_path)
        self._api = BridgeApiClient(api_base_url(self._cfg))
        self._update_bridge_connection_status()

    def _refresh_monitor(self) -> None:
        try:
            mon = self._api.get_monitor()
        except BridgeApiError as e:
            self._lbl_upstream.setText(f"错误: {e}")
            return
        up = mon.get("upstream") or {}
        host = up.get("host", "")
        port = up.get("port", "")
        up_enabled = bool(up.get("enabled", True))
        connected = up.get("connected")
        hb = float(up.get("lastHeartbeatReplyAt") or 0)
        hb_txt = (
            datetime.fromtimestamp(hb).strftime("%Y-%m-%d %H:%M:%S")
            if hb > 0
            else "—"
        )
        biz_online = up.get("businessOnline")
        hb_fail = int(up.get("heartbeatFailStreak") or 0)
        cmd_fail = int(up.get("commandFailStreak") or 0)
        buf_n = int(up.get("recvBufferBytes") or 0)
        if not up_enabled:
            state = "已手动断开"
        elif biz_online is not None:
            state = "业务在线" if biz_online else "业务离线"
        else:
            state = "已连接" if connected else "未连接"
        extra = ""
        if hb_fail or cmd_fail:
            extra = f" · HB失败{hb_fail} CMD失败{cmd_fail}"
        if buf_n:
            extra += f" · recv缓冲{buf_n}B"
        self._lbl_upstream.setText(f"{host}:{port} — {state}（心跳 {hb_txt}{extra}）")

        gw = mon.get("tcpGateway") or {}
        gw_enabled = bool(gw.get("enabled", True))
        listen = str(gw.get("listen") or mon.get("tcpListen") or "—")
        self._lbl_tcp_listen.setText(
            f"{listen} — {'接受连接' if gw_enabled else '已暂停（拒绝新连接）'}"
        )

        self._connections_syncing = True
        self._chk_upstream_conn.setChecked(up_enabled)
        self._chk_tcp_gateway.setChecked(gw_enabled)
        self._connections_syncing = False

        self._lbl_api_listen.setText(str(mon.get("apiListen") or "—"))
        q = mon.get("queue") or {}
        self._lbl_queue.setText(f"{q.get('current', 0)} / {q.get('max', 0)}")
        rc = mon.get("remoteControl") or {}
        rcs = rc.get("state", "—")
        rerr = rc.get("error") or ""
        self._lbl_rcs.setText(f"{rcs}" + (f" ({rerr})" if rerr else ""))
        self._lbl_encoding.setText(str(mon.get("encoding") or "—"))

        clients: List[Dict[str, Any]] = list(mon.get("tcpClients") or [])
        self._tbl_clients.setRowCount(len(clients))
        for i, c in enumerate(clients):
            self._tbl_clients.setItem(i, 0, QTableWidgetItem(str(c.get("peer") or "—")))
            dur = c.get("connectedSeconds")
            if dur is None:
                dur = 0.0
            self._tbl_clients.setItem(
                i, 1, QTableWidgetItem(self._format_duration(float(dur)))
            )
            self._tbl_clients.setItem(
                i, 2, QTableWidgetItem("是" if c.get("privileged") else "否")
            )
            self._tbl_clients.setItem(
                i, 3, QTableWidgetItem(str(c.get("logonUser") or "未登录"))
            )
            self._tbl_clients.setItem(
                i, 4, QTableWidgetItem(str(c.get("rxFrames", 0)))
            )
            self._tbl_clients.setItem(
                i, 5, QTableWidgetItem(str(c.get("txFrames", 0)))
            )

    def _apply_connections(self, *, upstream: Optional[bool] = None, tcp_gateway: Optional[bool] = None) -> None:
        body: Dict[str, Any] = {}
        if upstream is not None:
            body["upstreamEnabled"] = upstream
        if tcp_gateway is not None:
            body["tcpGatewayEnabled"] = tcp_gateway
        try:
            out = self._api.put_connections(body)
        except BridgeApiError as e:
            QMessageBox.warning(self, "连接开关", str(e))
            self._refresh_monitor()
            return
        notes = out.get("notes") or []
        if notes:
            QMessageBox.information(self, "连接开关", "\n".join(str(n) for n in notes))
        self._refresh_monitor()

    def _on_upstream_conn_toggled(self, checked: bool) -> None:
        if self._connections_syncing:
            return
        self._apply_connections(upstream=checked)

    def _on_tcp_gateway_toggled(self, checked: bool) -> None:
        if self._connections_syncing:
            return
        if not checked:
            if (
                QMessageBox.question(
                    self,
                    "TCP 网关",
                    "关闭后将断开所有已连接的 TCP 客户端，并拒绝新连接。继续？",
                )
                != QMessageBox.StandardButton.Yes
            ):
                self._connections_syncing = True
                self._chk_tcp_gateway.setChecked(True)
                self._connections_syncing = False
                return
        self._apply_connections(tcp_gateway=checked)

    def _refresh_queue(self) -> None:
        try:
            data = self._api.get_queue()
        except BridgeApiError as e:
            QMessageBox.warning(self, "队列", str(e))
            return
        items: List[Dict[str, Any]] = list(data.get("items") or [])
        self._tbl_queue.setRowCount(len(items))
        for i, it in enumerate(items):
            self._tbl_queue.setItem(i, 0, QTableWidgetItem(str(it.get("id") or "")))
            self._tbl_queue.setItem(
                i, 1, QTableWidgetItem(str(it.get("receivedAtText") or ""))
            )
            self._tbl_queue.setItem(i, 2, QTableWidgetItem(str(it.get("peer") or "")))
            self._tbl_queue.setItem(
                i, 3, QTableWidgetItem(str(it.get("sampleName") or ""))
            )
            self._tbl_queue.setItem(
                i, 4, QTableWidgetItem(str(it.get("sampleDescription") or ""))
            )

    def _load_config_form(self, *, reload_api: bool = True) -> None:
        self._config_path = resolve_config_path()
        self._cfg = load_config_dict(self._config_path)
        self._status_config.setText(f"配置: {self._config_path}")
        self._ed_host.setText(str(self._cfg.get("host") or "0.0.0.0"))
        self._sp_port.setValue(int(self._cfg.get("port") or 54321))
        self._ed_api_host.setText(str(self._cfg.get("bridge_api_host") or "127.0.0.1"))
        self._sp_api_port.setValue(int(self._cfg.get("bridge_api_port") or 8081))
        self._ed_upstream_host.setText(str(self._cfg.get("upstream_host") or ""))
        self._sp_upstream_port.setValue(int(self._cfg.get("upstream_port") or 12345))
        self._ed_priv_host.setText(str(self._cfg.get("privileged_add_samples_host") or ""))
        self._chk_long_conn.setChecked(bool(self._cfg.get("instrument_long_connection", True)))
        self._chk_auto_reco.setChecked(bool(self._cfg.get("upstream_auto_reconnect", True)))
        self._sp_recv_idle_clear.setValue(int(self._cfg.get("upstream_recv_idle_clear") or 5))
        self._sp_hb_fail_max.setValue(int(self._cfg.get("upstream_heartbeat_fail_max") or 2))
        self._sp_cmd_fail_max.setValue(int(self._cfg.get("upstream_command_fail_max") or 3))
        self._sp_client_fwd_timeout.setValue(
            int(self._cfg.get("upstream_client_forward_timeout") or 10)
        )
        self._sp_hb_wait_timeout.setValue(
            int(self._cfg.get("upstream_heartbeat_wait_timeout") or 0)
        )
        self._sp_activity_stale.setValue(
            int(self._cfg.get("upstream_activity_stale_seconds") or 0)
        )
        self._sp_read_cancel_timeout.setValue(
            int(self._cfg.get("upstream_read_cancel_timeout") or 5)
        )
        self._sp_stale_check_interval.setValue(
            int(self._cfg.get("upstream_stale_check_interval") or 30)
        )
        self._chk_no_syn_logon.setChecked(bool(self._cfg.get("no_synthetic_logon", False)))
        self._chk_verbose_gw.setChecked(bool(self._cfg.get("log_verbose_gateway", False)))
        self._chk_persist_q.setChecked(bool(self._cfg.get("persist_add_samples_queue", True)))
        self._sp_queue_max.setValue(int(self._cfg.get("add_samples_queue_size") or 8))
        lvl = str(self._cfg.get("log_level") or "info").lower()
        idx = self._cmb_log_level.findText(lvl)
        self._cmb_log_level.setCurrentIndex(idx if idx >= 0 else 1)
        self._ed_log_file.setText(str(self._cfg.get("log_file") or ""))
        if reload_api:
            self._reload_api_client()

    def _form_to_updates(self) -> Dict[str, Any]:
        return {
            "host": self._ed_host.text().strip(),
            "port": self._sp_port.value(),
            "bridge_api_host": self._ed_api_host.text().strip(),
            "bridge_api_port": self._sp_api_port.value(),
            "upstream_host": self._ed_upstream_host.text().strip(),
            "upstream_port": self._sp_upstream_port.value(),
            "privileged_add_samples_host": self._ed_priv_host.text().strip(),
            "instrument_long_connection": self._chk_long_conn.isChecked(),
            "upstream_auto_reconnect": self._chk_auto_reco.isChecked(),
            "upstream_recv_idle_clear": self._sp_recv_idle_clear.value(),
            "upstream_heartbeat_fail_max": self._sp_hb_fail_max.value(),
            "upstream_command_fail_max": self._sp_cmd_fail_max.value(),
            "upstream_client_forward_timeout": self._sp_client_fwd_timeout.value(),
            "upstream_heartbeat_wait_timeout": self._sp_hb_wait_timeout.value(),
            "upstream_activity_stale_seconds": self._sp_activity_stale.value(),
            "upstream_read_cancel_timeout": self._sp_read_cancel_timeout.value(),
            "upstream_stale_check_interval": self._sp_stale_check_interval.value(),
            "no_synthetic_logon": self._chk_no_syn_logon.isChecked(),
            "log_verbose_gateway": self._chk_verbose_gw.isChecked(),
            "persist_add_samples_queue": self._chk_persist_q.isChecked(),
            "add_samples_queue_size": self._sp_queue_max.value(),
            "log_level": self._cmb_log_level.currentText(),
            "log_file": self._ed_log_file.text().strip(),
        }

    def _save_config_file(self) -> None:
        updates = self._form_to_updates()
        merged = merge_config_update(load_config_dict(self._config_path), updates)
        try:
            path = save_config_dict(merged, self._config_path)
        except OSError as e:
            QMessageBox.critical(self, "保存配置", str(e))
            return
        self._config_path = path
        self._cfg = merged
        QMessageBox.information(
            self,
            "保存配置",
            f"已写入:\n{path}\n\n多数项需重启 Bridge 后生效。",
        )
        self._reload_api_client()

    def _apply_runtime_settings(self) -> None:
        updates = self._form_to_updates()
        merged = merge_config_update(load_config_dict(self._config_path), updates)
        try:
            save_config_dict(merged, self._config_path)
        except OSError as e:
            QMessageBox.critical(self, "应用设置", f"保存配置失败: {e}")
            return
        self._cfg = merged
        self._reload_api_client()

        body = {
            "tcpListenHost": self._ed_host.text().strip(),
            "tcpListenPort": self._sp_port.value(),
            "upstreamHost": self._ed_upstream_host.text().strip(),
            "upstreamPort": self._sp_upstream_port.value(),
            "privilegedAddSamplesHost": self._ed_priv_host.text().strip(),
            "queueMax": self._sp_queue_max.value(),
            "logVerboseGateway": self._chk_verbose_gw.isChecked(),
            "logLevel": self._cmb_log_level.currentText(),
            "persistToConfigFile": True,
        }
        try:
            out = self._api.put_settings(body)
        except BridgeApiError as e:
            QMessageBox.warning(
                self,
                "应用设置",
                f"配置已保存，但 Bridge API 未响应:\n{e}\n\n请确认 Bridge 已启动。",
            )
            return
        notes = out.get("notes") or []
        msg = (
            "已保存配置并提交到运行中的 Bridge。\n\n"
            "无需重启即可生效：上游地址（自动重连）、队列上限、特权 IP、"
            "log_verbose_gateway、控制台 log_level。\n"
            "须重启 Bridge 服务：TCP/REST 监听端口、instrument_long_connection、"
            "log_file 路径等。"
        )
        if out.get("restartRequired"):
            msg += "\n\n本次修改包含监听地址变更：请重启 Bridge 后才会真正绑定新端口。"
        if notes:
            msg += "\n\n" + "\n".join(str(n) for n in notes)
        if out.get("persistOk") is False and out.get("persistError"):
            msg += f"\n\nHub 写回配置文件: {out.get('persistError')}"
        QMessageBox.information(self, "应用设置", msg)
        self._log_offset = 0
        self._log_view.clear()
        self._refresh_monitor()

    def _open_config_dir(self) -> None:
        path = self._config_path.parent
        subprocess.Popen(["explorer", str(path)])

    def _stop_bridge_service(self) -> None:
        svc = "CornerstoneBridge"
        if windows_service_state(svc) == "stopped":
            QMessageBox.information(self, "停止服务", f"{svc} 服务当前未运行。")
            self._reload_api_client()
            return
        if (
            QMessageBox.question(
                self,
                "停止服务",
                f"将停止 Windows 服务 {svc}（需要管理员权限）。继续？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        ok, err = windows_service_stop(svc)
        if not ok:
            QMessageBox.warning(self, "停止服务", f"停止失败:\n{err}")
            return
        QMessageBox.information(self, "停止服务", f"{svc} 服务已停止。")
        self._reload_api_client()

    def _restart_bridge_service(self) -> None:
        svc = "CornerstoneBridge"
        was_stopped = windows_service_state(svc) == "stopped"
        if was_stopped:
            title, prompt = (
                "启动服务",
                f"将启动 Windows 服务 {svc}（需要管理员权限）。继续？",
            )
        else:
            title, prompt = (
                "重启服务",
                f"将重启 Windows 服务 {svc}（需要管理员权限）。继续？",
            )
        if (
            QMessageBox.question(self, title, prompt)
            != QMessageBox.StandardButton.Yes
        ):
            return
        if not was_stopped:
            ok, err = windows_service_stop(svc)
            if not ok:
                QMessageBox.warning(self, "重启服务", f"停止失败:\n{err}")
                return
        ok, err = windows_service_start(svc)
        if not ok:
            QMessageBox.warning(
                self,
                title,
                f"{'启动' if was_stopped else '启动（重启）'}失败:\n{err}",
            )
            return
        done = f"{svc} 服务已启动。" if was_stopped else f"{svc} 服务已重启。"
        QMessageBox.information(self, title, done)
        time.sleep(1.0)
        self._refresh_all()

    def _enabled_log_levels(self) -> set[str]:
        levels: set[str] = set()
        if self._chk_log_debug.isChecked():
            levels.add("DEBUG")
        if self._chk_log_info.isChecked():
            levels.add("INFO")
        if self._chk_log_warning.isChecked():
            levels.add("WARNING")
        if self._chk_log_error.isChecked():
            levels.update({"ERROR", "CRITICAL"})
        return levels

    def _filter_log_text(self, text: str) -> str:
        if not text:
            return ""
        enabled = self._enabled_log_levels()
        if not enabled:
            return ""
        out: List[str] = []
        show_continuation = False
        for line in text.splitlines(keepends=True):
            m = self._LOG_LEVEL_RE.match(line)
            if m:
                show_continuation = m.group(1) in enabled
                if show_continuation:
                    out.append(line)
            elif show_continuation:
                out.append(line)
        return "".join(out)

    def _log_scroll_at_bottom(self) -> bool:
        sb = self._log_view.verticalScrollBar()
        return sb.maximum() <= 0 or sb.value() >= sb.maximum() - 2

    def _append_log_text(self, text: str) -> None:
        if not text:
            return
        at_bottom = self._log_scroll_at_bottom()
        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        if at_bottom:
            sb = self._log_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_log_filter_changed(self) -> None:
        self._log_offset = 0
        self._log_view.clear()
        self._refresh_logs(force_full=True)

    def _refresh_logs(self, *, force_full: bool = False) -> None:
        path = log_file_path(self._cfg)
        if not path.is_file():
            self._log_view.setPlainText(f"日志文件不存在: {path}")
            self._log_offset = 0
            return
        replace_all = force_full
        try:
            size = path.stat().st_size
            if size < self._log_offset:
                self._log_offset = 0
                self._log_view.clear()
                replace_all = True
            elif force_full:
                self._log_offset = 0
                self._log_view.clear()
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_offset)
                chunk = f.read()
                self._log_offset = f.tell()
        except OSError as e:
            self._log_view.setPlainText(str(e))
            return
        filtered = self._filter_log_text(chunk)
        if not filtered:
            return
        if replace_all:
            self._log_view.setPlainText(filtered)
            sb = self._log_view.verticalScrollBar()
            sb.setValue(sb.maximum())
        else:
            self._append_log_text(filtered)
