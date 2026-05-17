from __future__ import annotations

import asyncio
import contextlib
import urllib.parse
from pathlib import Path
from typing import Dict, Optional, Tuple

_STATIC_DIR = Path(__file__).resolve().parent / "web_static"


async def _async_close_stream_writer(writer: Optional[asyncio.StreamWriter]) -> None:
    if writer is None or writer.is_closing():
        return
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def _http_send(
    writer: asyncio.StreamWriter,
    status: int,
    body: bytes,
    content_type: str = "application/octet-stream",
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 502: "Bad Gateway", 500: "Internal Server Error"}.get(
        status, "OK"
    )
    lines = [
        f"HTTP/1.1 {status} {reason}\r\n",
        f"Content-Type: {content_type}\r\n",
        f"Content-Length: {len(body)}\r\n",
    ]
    for k, v in (extra_headers or {}).items():
        lines.append(f"{k}: {v}\r\n")
    lines.append("Connection: close\r\n\r\n")
    writer.write("".join(lines).encode("latin-1", errors="replace") + body)
    await writer.drain()


def _safe_static_path(path: str) -> Optional[Path]:
    if not path.startswith("/static/"):
        return None
    rel = path[len("/static/") :].lstrip("/").replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    base = _STATIC_DIR.resolve()
    fp = (base / rel).resolve()
    try:
        fp.relative_to(base)
    except ValueError:
        return None
    return fp if fp.is_file() else None


def _parse_http_request(first: bytes) -> Tuple[str, str, str, bytes, Dict[str, str]]:
    header_end = first.find(b"\r\n\r\n")
    body = b""
    headers: Dict[str, str] = {}
    if header_end >= 0:
        header_blob = first[:header_end].decode("latin-1", errors="replace")
        body = first[header_end + 4 :]
        first_line = header_blob.split("\r\n", 1)[0]
        for hl in header_blob.split("\r\n")[1:]:
            if ":" in hl:
                k, v = hl.split(":", 1)
                headers[k.strip().lower()] = v.strip()
    else:
        first_line = first.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
    parts = first_line.split()
    method = parts[0].upper() if parts else "GET"
    raw_target = parts[1] if len(parts) > 1 else "/"
    path_only, _, qstr = raw_target.partition("?")
    path = path_only.split("#", 1)[0]
    return method, path, qstr, body, headers


async def _read_body(reader: asyncio.StreamReader, first: bytes, headers: Dict[str, str]) -> bytes:
    header_end = first.find(b"\r\n\r\n")
    body = first[header_end + 4 :] if header_end >= 0 else b""
    cl = int(headers.get("content-length", "0") or "0")
    while len(body) < cl:
        chunk = await reader.read(cl - len(body))
        if not chunk:
            break
        body += chunk
    return body


def _bridge_target(bridge_base_url: str, path: str, qstr: str) -> Tuple[str, int, str]:
    parsed = urllib.parse.urlparse(bridge_base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target = path
    if qstr:
        target = f"{path}?{qstr}"
    return host, port, target


async def _proxy_to_bridge(
    writer: asyncio.StreamWriter,
    *,
    bridge_base_url: str,
    method: str,
    path: str,
    qstr: str,
    body: bytes,
    req_headers: Dict[str, str],
) -> None:
    host, port, target = _bridge_target(bridge_base_url, path, qstr)
    try:
        br, bw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10.0)
    except Exception as e:
        msg = f"Bridge 不可达 ({bridge_base_url}): {e}".encode("utf-8")
        await _http_send(writer, 502, msg, "text/plain; charset=utf-8")
        return
    hop = []
    if req_headers.get("host"):
        hop.append(f"Host: {req_headers['host']}\r\n")
    ct = req_headers.get("content-type")
    if ct:
        hop.append(f"Content-Type: {ct}\r\n")
    req = (
        f"{method} {target} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        + "".join(hop)
        + "\r\n"
    ).encode("latin-1", errors="replace") + body
    bw.write(req)
    await bw.drain()
    resp = await asyncio.wait_for(br.read(16 * 1024 * 1024), timeout=300.0)
    await _async_close_stream_writer(bw)
    if not resp:
        await _http_send(writer, 502, b"Empty bridge response", "text/plain; charset=utf-8")
        return
    writer.write(resp)
    await writer.drain()


async def handle_web_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    bridge_base_url: str,
) -> None:
    try:
        first = await reader.read(65536)
        if not first:
            return
        method, path, qstr, body, headers = _parse_http_request(first)
        body = await _read_body(reader, first, headers)

        if method == "GET" and path.startswith("/static/"):
            fp = _safe_static_path(path)
            if fp is None:
                await _http_send(writer, 404, b"Not Found", "text/plain; charset=utf-8")
                return
            mime = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
            }.get(fp.suffix.lower(), "application/octet-stream")
            await _http_send(writer, 200, fp.read_bytes(), mime)
            return

        if method == "GET" and path in ("/", "/index.html"):
            idx = _STATIC_DIR / "index.html"
            if idx.is_file():
                await _http_send(writer, 200, idx.read_bytes(), "text/html; charset=utf-8")
            else:
                await _http_send(writer, 404, b"index.html missing", "text/plain; charset=utf-8")
            return

        if method == "GET" and path == "/legacy":
            await _http_send(
                writer,
                302,
                b"",
                "text/plain",
                extra_headers={"Location": "/"},
            )
            return

        if path.startswith("/api/") or path in ("/send",):
            await _proxy_to_bridge(
                writer,
                bridge_base_url=bridge_base_url,
                method=method,
                path=path,
                qstr=qstr,
                body=body,
                req_headers=headers,
            )
            return

        await _http_send(writer, 404, b"Not Found", "text/plain; charset=utf-8")
    finally:
        await _async_close_stream_writer(writer)
