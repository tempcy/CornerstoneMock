from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .bridge_api import BridgeApiClient, BridgeApiError
from .config_io import (
    api_base_url,
    load_config_dict,
    log_file_path,
    merge_config_update,
    resolve_config_path,
    save_config_dict,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cornerstone Bridge 控制台")
        self.resize(920, 640)

        self._config_path = resolve_config_path()
        self._cfg: Dict[str, Any] = load_config_dict(self._config_path)
        self._api = BridgeApiClient(api_base_url(self._cfg))
        self._log_offset = 0

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

    # --- 监控 ---
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

        grid = QGridLayout()
        self._lbl_upstream = QLabel("—")
        self._lbl_tcp_listen = QLabel("—")
        self._lbl_api_listen = QLabel("—")
        self._lbl_queue = QLabel("—")
        self._lbl_rcs = QLabel("—")
        self._lbl_encoding = QLabel("—")
        grid.addWidget(QLabel("上游仪器"), 0, 0)
        grid.addWidget(self._lbl_upstream, 0, 1)
        grid.addWidget(QLabel("TCP 网关"), 0, 2)
        grid.addWidget(self._lbl_tcp_listen, 0, 3)
        grid.addWidget(QLabel("REST API"), 1, 0)
        grid.addWidget(self._lbl_api_listen, 1, 1)
        grid.addWidget(QLabel("通讯队列"), 1, 2)
        grid.addWidget(self._lbl_queue, 1, 3)
        grid.addWidget(QLabel("远程控制状态"), 2, 0)
        grid.addWidget(self._lbl_rcs, 2, 1, 1, 3)
        grid.addWidget(QLabel("编码"), 3, 0)
        grid.addWidget(self._lbl_encoding, 3, 1)
        layout.addLayout(grid)

        layout.addWidget(QLabel("已连接的 TCP 远程客户端"))
        self._tbl_clients = QTableWidget(0, 2)
        self._tbl_clients.setHorizontalHeaderLabels(["客户端地址", "正在关闭"])
        self._tbl_clients.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._tbl_clients)

        self._tabs.addTab(w, "连接与监控")

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
        self._chk_no_syn_logon = QCheckBox("禁用合成 Logon (no_synthetic_logon)")
        self._chk_verbose_gw = QCheckBox("网关 XML 详细日志 (log_verbose_gateway，应用后立即生效)")
        self._chk_persist_q = QCheckBox("持久化队列 (persist_add_samples_queue)")
        self._sp_queue_max = QSpinBox()
        self._sp_queue_max.setRange(1, 256)
        tlay.addRow(self._chk_long_conn)
        tlay.addRow(self._chk_auto_reco)
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

        btns = QHBoxLayout()
        self._btn_reload_cfg = QPushButton("重新加载")
        self._btn_save_cfg = QPushButton("保存到配置文件")
        self._btn_apply_runtime = QPushButton("应用到运行中的 Bridge")
        self._btn_open_cfg_dir = QPushButton("打开配置目录")
        self._btn_restart_svc = QPushButton("重启 Bridge 服务")
        for b in (
            self._btn_reload_cfg,
            self._btn_save_cfg,
            self._btn_apply_runtime,
            self._btn_open_cfg_dir,
            self._btn_restart_svc,
        ):
            btns.addWidget(b)
        outer.addLayout(btns)
        outer.addStretch()

        self._btn_reload_cfg.clicked.connect(self._load_config_form)
        self._btn_save_cfg.clicked.connect(self._save_config_file)
        self._btn_apply_runtime.clicked.connect(self._apply_runtime_settings)
        self._btn_open_cfg_dir.clicked.connect(self._open_config_dir)
        self._btn_restart_svc.clicked.connect(self._restart_bridge_service)

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
            )
        )
        self._tabs.addTab(w, "日志")

    def _on_poll(self) -> None:
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

    def _reload_api_client(self) -> None:
        self._cfg = load_config_dict(self._config_path)
        self._api = BridgeApiClient(api_base_url(self._cfg))
        ok, err = self._api.ping()
        if ok:
            self._status_bridge.setText(f"Bridge: 已连接 {api_base_url(self._cfg)}")
            self._status_bridge.setStyleSheet("")
        else:
            self._status_bridge.setText(f"Bridge: 未连接 — {err}")
            self._status_bridge.setStyleSheet("color: #c0392b;")

    def _refresh_monitor(self) -> None:
        try:
            mon = self._api.get_monitor()
        except BridgeApiError as e:
            self._lbl_upstream.setText(f"错误: {e}")
            return
        up = mon.get("upstream") or {}
        host = up.get("host", "")
        port = up.get("port", "")
        connected = up.get("connected")
        hb = float(up.get("lastHeartbeatReplyAt") or 0)
        hb_txt = (
            datetime.fromtimestamp(hb).strftime("%Y-%m-%d %H:%M:%S")
            if hb > 0
            else "—"
        )
        state = "已连接" if connected else "未连接"
        self._lbl_upstream.setText(f"{host}:{port} — {state}（最近心跳应答 {hb_txt}）")
        self._lbl_tcp_listen.setText(str(mon.get("tcpListen") or "—"))
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
            self._tbl_clients.setItem(
                i, 1, QTableWidgetItem("是" if c.get("closing") else "否")
            )

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

    def _restart_bridge_service(self) -> None:
        if (
            QMessageBox.question(
                self,
                "重启服务",
                "将尝试重启 Windows 服务 CornerstoneBridge（需要管理员权限）。继续？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for cmd in (
            ["net", "stop", "CornerstoneBridge"],
            ["net", "start", "CornerstoneBridge"],
        ):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                QMessageBox.warning(
                    self,
                    "重启服务",
                    f"{' '.join(cmd)} 失败:\n{r.stderr or r.stdout}",
                )
                return
        QMessageBox.information(self, "重启服务", "CornerstoneBridge 服务已重启。")
        time.sleep(1.0)
        self._refresh_all()

    def _refresh_logs(self) -> None:
        path = log_file_path(self._cfg)
        if not path.is_file():
            self._log_view.setPlainText(f"日志文件不存在: {path}")
            self._log_offset = 0
            return
        try:
            size = path.stat().st_size
            if size < self._log_offset:
                self._log_offset = 0
                self._log_view.clear()
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_offset)
                chunk = f.read()
                self._log_offset = f.tell()
        except OSError as e:
            self._log_view.setPlainText(str(e))
            return
        if chunk:
            self._log_view.moveCursor(self._log_view.textCursor().MoveOperation.End)
            self._log_view.insertPlainText(chunk)
            self._log_view.moveCursor(self._log_view.textCursor().MoveOperation.End)
