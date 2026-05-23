#!/usr/bin/env python3
"""
本地开发用 Cornerstone 上游 TCP 占位服务（UTF-16-LE 长度帧）。

默认监听 127.0.0.1:12345，供 cornerstone-web-dev / Bridge 的 upstream 连接。

用法::

    python scripts/dev-instrument-mock.py
    python scripts/dev-instrument-mock.py --port 12345
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import struct

ENC = "utf-16-le"


def _frame(text: str) -> bytes:
    raw = text.encode(ENC)
    return struct.pack("<I", len(raw)) + raw


def _cookie(text: str) -> str:
    m = re.search(r'\bCookie="([^"]*)"', text)
    return m.group(1) if m else "mock-cookie"


def _tag(text: str) -> str:
    m = re.match(r"<(\w+)", (text or "").lstrip())
    return m.group(1) if m else ""


def _reply(tag: str, cookie: str, *, body: str = "") -> str:
    if tag == "Logon":
        inner = body or (
            f'<Logon ErrorCode="0" ErrorMessage="Success" Cookie="{cookie}" />'
        )
    elif tag == "Heartbeat":
        inner = f'<Heartbeat ErrorCode="0" ErrorMessage="Success" Cookie="{cookie}" />'
    elif tag == "RemoteControlState":
        inner = (
            f'<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="{cookie}">'
            "false</RemoteControlState>"
        )
    elif tag == "InstrumentInfo":
        inner = (
            f'<InstrumentInfo ErrorCode="0" ErrorMessage="Success" Cookie="{cookie}">'
            '<Field Label="Product">Mock</Field>'
            '<Field Label="Serial">00000</Field>'
            "</InstrumentInfo>"
        )
    elif tag == "Prerequisites":
        inner = (
            f'<Prerequisites ErrorCode="0" ErrorMessage="Success" Cookie="{cookie}">'
            '<Prerequisite Name="LMLC Loaded" Value="true" />'
            "</Prerequisites>"
        )
    else:
        tn = tag or "Response"
        inner = f'<{tn} ErrorCode="0" ErrorMessage="Success" Cookie="{cookie}"></{tn}>'
    return inner


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    print(f"[mock] client {peer}")
    try:
        while True:
            header = await reader.readexactly(4)
            (length,) = struct.unpack("<I", header)
            if length <= 0 or length > 8 * 1024 * 1024:
                break
            payload = await reader.readexactly(length)
            text = payload.decode(ENC, errors="replace")
            tag = _tag(text)
            cookie = _cookie(text)
            print(f"[mock] IN tag={tag} cookie={cookie!r} bytes={length}")
            if tag == "Logoff":
                break
            resp = _reply(tag, cookie)
            writer.write(_frame(resp))
            await writer.drain()
            print(f"[mock] OUT tag={_tag(resp)} bytes={len(resp.encode(ENC))}")
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        print(f"[mock] closed {peer}")


async def _main(host: str, port: int) -> None:
    srv = await asyncio.start_server(_handle_client, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in srv.sockets or [])
    print(f"[mock] listening {addrs} encoding={ENC}")
    async with srv:
        await srv.serve_forever()


def main() -> int:
    p = argparse.ArgumentParser(description="Cornerstone upstream TCP mock for local dev")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=12345)
    args = p.parse_args()
    try:
        asyncio.run(_main(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[mock] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
