from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceGuard:
    """本机单实例；第二实例连接触发 ``activate`` 回调。"""

    SERVER_NAME = "cornerstone-bridge-ui-v1"

    def __init__(self) -> None:
        self._server: QLocalServer | None = None
        self.already_running = False

    def try_acquire(self) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.SERVER_NAME)
        if sock.waitForConnected(300):
            sock.write(QByteArray(b"raise"))
            sock.flush()
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            self.already_running = True
            return False
        self._server = QLocalServer()
        QLocalServer.removeServer(self.SERVER_NAME)
        if not self._server.listen(self.SERVER_NAME):
            self.already_running = False
            return True
        return True

    def bind_activate(self, callback) -> None:
        if not self._server:
            return

        def _on_new() -> None:
            conn = self._server.nextPendingConnection()
            if conn is None:
                return
            conn.waitForReadyRead(200)
            callback()
            conn.disconnectFromServer()

        self._server.newConnection.connect(_on_new)
