from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


class BridgeApiError(Exception):
    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class BridgeApiClient:
    def __init__(self, base_url: str, *, timeout_s: float = 2.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self._base}{path}"
        data: Optional[bytes] = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise BridgeApiError(f"HTTP {e.code}: {detail[:200]}", status=e.code) from e
        except urllib.error.URLError as e:
            raise BridgeApiError(f"无法连接 Bridge API ({self._base}): {e.reason}") from e
        try:
            obj = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise BridgeApiError("响应不是有效 JSON") from e
        if not isinstance(obj, dict):
            raise BridgeApiError("响应根节点须为 JSON 对象")
        return obj

    def get_monitor(self) -> Dict[str, Any]:
        return self._request("GET", "/api/monitor")

    def get_status(self) -> Dict[str, Any]:
        return self._request("GET", "/api/status")

    def get_config(self) -> Dict[str, Any]:
        return self._request("GET", "/api/config")

    def get_queue(self) -> Dict[str, Any]:
        return self._request("GET", "/api/queue")

    def get_settings(self) -> Dict[str, Any]:
        return self._request("GET", "/api/settings")

    def put_settings(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", "/api/settings", body=body)

    def put_connections(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", "/api/connections", body=body)

    def post_client_ip_policy(self, action: str, peer_host: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/clients/ip-policy",
            body={"action": action, "peerHost": peer_host},
        )

    def ping(self) -> Tuple[bool, str]:
        try:
            st = self.get_status()
            if st.get("ok"):
                return True, ""
            return False, str(st.get("error") or "未知错误")
        except BridgeApiError as e:
            return False, str(e)
