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
    """Bridge REST 客户端（stdlib）。

    默认只读；``post_json`` 仅用于白名单下行（如 operator-notices），禁止映射为写仪器。
    仪器 Bridge 在工控网，始终直连，不走 HTTP(S)_PROXY（避免公司代理劫持内网）。
    """

    def __init__(self, base_url: str, timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        # 空 ProxyHandler：忽略环境变量 / Windows 系统代理
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
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
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(resp_body) if resp_body else None
            except json.JSONDecodeError:
                parsed = resp_body
            raise BridgeError(f"Bridge HTTP {e.code} for {path}", status=e.code, body=parsed) from e
        except urllib.error.URLError as e:
            raise BridgeError(f"Bridge unreachable: {e.reason}", status=None) from e
        except (TimeoutError, OSError, http.client.HTTPException) as e:
            raise BridgeError(f"Bridge unreachable: {e}", status=None) from e

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._request_json("GET", path, query=query)

    def post_json(self, path: str, body: dict[str, Any] | None = None) -> Any:
        """白名单写：目前仅 operator-notices 下行展示。"""
        return self._request_json("POST", path, body=body or {})

    def ping(self) -> bool:
        try:
            self.get_json("/api/status")
            return True
        except BridgeError:
            return False

    def probe_status(self) -> tuple[bool, dict[str, Any]]:
        """探测 Bridge 可达性，并尽量返回 /api/status 正文。

        返回 ``(reachable, status_dict)``。旧 Bridge 或异常时 ``status_dict`` 可为 ``{}``，
        不影响调用方仅关心连通性时的判断。
        """
        try:
            raw = self.get_json("/api/status")
            if isinstance(raw, dict):
                return True, raw
            return True, {}
        except BridgeError:
            return False, {}


def extract_bridge_version(status: dict[str, Any] | None) -> str:
    """从 status JSON 中安全取出 Bridge 版本；缺失则返回空串。"""
    if not isinstance(status, dict):
        return ""
    for key in ("bridgeVersion", "bridge_version"):
        val = status.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""
