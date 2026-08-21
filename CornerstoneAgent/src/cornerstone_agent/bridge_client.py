from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class BridgeError(Exception):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class BridgeClient:
    """只读 Bridge REST 客户端（stdlib）。

    仪器 Bridge 在工控网，始终直连，不走 HTTP(S)_PROXY（避免公司代理劫持内网）。
    """

    def __init__(self, base_url: str, timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        # 空 ProxyHandler：忽略环境变量 / Windows 系统代理
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> Any:
        q = ""
        if query:
            items = []
            for k, v in query.items():
                if v is None:
                    continue
                items.append((k, str(v)))
            if items:
                q = "?" + urllib.parse.urlencode(items)
        url = f"{self.base_url}{path}{q}"
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body else None
            except json.JSONDecodeError:
                parsed = body
            raise BridgeError(f"Bridge HTTP {e.code} for {path}", status=e.code, body=parsed) from e
        except urllib.error.URLError as e:
            raise BridgeError(f"Bridge unreachable: {e.reason}", status=None) from e
        except (TimeoutError, OSError, http.client.HTTPException) as e:
            raise BridgeError(f"Bridge unreachable: {e}", status=None) from e

    def ping(self) -> bool:
        try:
            self.get_json("/api/status")
            return True
        except BridgeError:
            return False
