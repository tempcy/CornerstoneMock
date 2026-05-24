from __future__ import annotations

import sys


def main() -> int:
    from .win_admin import relaunch_as_admin

    relaunch_as_admin()

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
    except ImportError:
        print(
            "cornerstone-bridge-ui 需要 PySide6。请执行:\n"
            "  pip install -e \".[ui]\"   # 在 CornerstoneBridge 目录\n"
            "或: pip install PySide6",
            file=sys.stderr,
        )
        return 1

    from .main_window import MainWindow
    from .single_instance import SingleInstanceGuard

    app = QApplication(sys.argv)
    app.setApplicationName("Cornerstone Bridge 控制台")
    app.setQuitOnLastWindowClosed(False)

    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        QMessageBox.information(
            None,
            "Cornerstone Bridge 控制台",
            "控制台已在运行，已在托盘中激活。",
        )
        return 0

    window = MainWindow()
    guard.bind_activate(window.show_normal)

    def _warn_if_bridge_down() -> None:
        ok, err = window._api.ping()
        if not ok:
            QMessageBox.warning(
                window,
                "Bridge 未连接",
                "本程序是管理控制台，通过 REST API 连接已在运行的 Bridge，"
                "不会再次启动 TCP 网关。\n\n"
                "• 请保持 Windows 服务「CornerstoneBridge」运行（或单独运行 cornerstone-bridge）\n"
                "• 无需为打开控制台而停止该服务\n"
                "• 若出现端口 54321 被占用，说明误运行了 cornerstone-bridge.exe，"
                "请只运行 cornerstone-bridge-ui.exe\n\n"
                f"当前无法连接: {err}",
            )

    tray = QSystemTrayIcon(app)
    style = app.style()
    if style:
        tray.setIcon(style.standardIcon(style.StandardPixmap.SP_ComputerIcon))
    tray.setToolTip("Cornerstone Bridge 控制台")

    menu = QMenu()
    act_show = QAction("打开控制台", menu)
    act_show.triggered.connect(window.show_normal)
    act_quit = QAction("退出", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)

    def _on_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            window.show_normal()

    tray.activated.connect(_on_activated)
    tray.show()

    window.show_normal()
    QTimer.singleShot(200, _warn_if_bridge_down)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
