"""短时探测上游 190.2.96.210:12345 的 TCP/inner 解包（需网络可达）。"""
from __future__ import annotations

import asyncio
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cornerstone_bridge.protocol import decode_frame_payload_bytes, validate_frame_length
from cornerstone_bridge.upstream_framing import UpstreamRecvBuffer

HOST = "190.2.96.210"
PORT = 12345
ENC = "utf-16-le"
PROBE_SEC = 8.0


async def main() -> None:
    recv = UpstreamRecvBuffer(idle_clear_sec=30.0, incomplete_timeout_s=5.0)
    print(f"connecting {HOST}:{PORT} ...")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(HOST, PORT), timeout=10.0
    )
    try:
        deadline = asyncio.get_running_loop().time() + PROBE_SEC
        frames = 0
        while asyncio.get_running_loop().time() < deadline:
            header = await asyncio.wait_for(reader.readexactly(4), timeout=3.0)
            (length,) = struct.unpack("<I", header)
            err = validate_frame_length(length, ENC)
            if err:
                print(f"bad outer len={length} err={err}")
                break
            payload = await reader.readexactly(length)
            cleared = recv.append(payload)
            if cleared:
                print("recv buffer idle-cleared")
            dr = recv.drain()
            for anomaly in dr.anomalies:
                print(f"ANOMALY {anomaly.kind}: {anomaly.message}")
            for packet in dr.packets:
                frames += 1
                text, dec_err = decode_frame_payload_bytes(packet, ENC)
                tag = (text or "")[:80].replace("\n", " ")
                print(f"frame#{frames} bytes={len(packet)} decode_err={dec_err!r} preview={tag!r}")
            if recv.needs_more():
                need = recv.bytes_needed()
                print(f"needs_more buf={len(recv.buf)} need={need}")
                try:
                    extra = await asyncio.wait_for(reader.read(need), timeout=2.0)
                except asyncio.TimeoutError:
                    extra = b""
                if extra:
                    recv.append(extra)
                    dr2 = recv.drain()
                    for packet in dr2.packets:
                        frames += 1
                        text, _ = decode_frame_payload_bytes(packet, ENC)
                        print(f"frame#{frames} (cont) bytes={len(packet)} preview={(text or '')[:60]!r}")
        print(f"done frames={frames} buf_remain={len(recv.buf)}")
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
