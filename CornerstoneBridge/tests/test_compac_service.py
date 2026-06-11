"""COMPAC 服务：内存串口握手发送与状态查询。"""
import asyncio

import pytest

from cornerstone_bridge.compac_protocol import ACK, ENQ, build_status_request
from cornerstone_bridge.compac_serial_port import MemorySerialPort, link_memory_ports
from cornerstone_bridge.compac_service import CompacSerialConfig, CompacSerialService


@pytest.mark.asyncio
async def test_send_sample_handshake():
    client_port = MemorySerialPort("memory://client")
    server_port = MemorySerialPort("memory://server")
    link_memory_ports(client_port, server_port)
    server_port.open()

    async def instrument_side():
        while True:
            chunk = server_port.read(4096)
            if not chunk:
                await asyncio.sleep(0.01)
                continue
            if chunk == bytes([ENQ]):
                server_port.write(bytes([ACK]))
            elif chunk[:1] == bytes([0x02]):
                server_port.write(bytes([ACK]))
                return

    svc = CompacSerialService(
        CompacSerialConfig(
            enabled=True,
            port="memory://client",
            force_memory_port=True,
            timeout_seconds=2.0,
            retry_count=2,
        )
    )
    await svc.bind_memory_peer(client_port)
    task = asyncio.create_task(instrument_side())
    try:
        r = await svc.send_sample("TEST-001", "Iron")
        assert r["ok"] is True
        assert client_port.tx_buffer[:1] == bytes([ENQ])
        assert b"TEST-001" in bytes(client_port.tx_buffer)
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await svc.shutdown()


@pytest.mark.asyncio
async def test_query_status():
    client_port = MemorySerialPort("memory://client")
    svc = CompacSerialService(
        CompacSerialConfig(
            enabled=True,
            port="memory://client",
            force_memory_port=True,
            timeout_seconds=2.0,
        )
    )
    await svc.bind_memory_peer(client_port)

    async def reply_status():
        await asyncio.sleep(0.05)
        client_port.inject_rx(b"\x01A10000000003\r\n")

    task = asyncio.create_task(reply_status())
    r = await svc.query_status()
    await task
    assert r["ok"] is True
    assert r["automaticMode"] is True
    assert r["errorCode"] == 3
    await svc.shutdown()


@pytest.mark.asyncio
async def test_queue_enqueue_and_send():
    port = MemorySerialPort("memory://q")
    peer = MemorySerialPort("memory://peer")
    link_memory_ports(port, peer)
    peer.open()

    svc = CompacSerialService(
        CompacSerialConfig(enabled=True, port="memory://q", force_memory_port=True, timeout_seconds=2.0)
    )
    await svc.bind_memory_peer(port)

    async def auto_ack():
        while True:
            data = peer.read(4096)
            if data == bytes([ENQ]) or (data and data[0] == 0x02):
                peer.write(bytes([ACK]))
            await asyncio.sleep(0.01)

    task = asyncio.create_task(auto_ack())
    entry = svc.enqueue_sample("Q1", "TypeA")
    try:
        out = await svc.send_queued({entry.entry_id})
        assert out["ok"] is True
        assert out["results"][0]["ok"] is True
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await svc.shutdown()


@pytest.mark.asyncio
async def test_reply_a_request():
    port = MemorySerialPort("memory://reply")
    peer = MemorySerialPort("memory://peer")
    link_memory_ports(port, peer)
    peer.open()

    svc = CompacSerialService(
        CompacSerialConfig(
            enabled=True,
            port="memory://reply",
            force_memory_port=True,
            reply_a_request=True,
            reply_status_chars="0100000000",
            reply_status_error=5,
        )
    )
    await svc.bind_memory_peer(port)
    peer.write(build_status_request())
    await asyncio.sleep(0.15)
    assert b"A01000000005\r\n" in bytes(peer.rx_buffer)
    await svc.shutdown()
