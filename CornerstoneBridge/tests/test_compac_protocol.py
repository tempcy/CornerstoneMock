"""COMPAC 协议：电文编解码、缓冲粘拆包、状态行、异常丢弃。"""
import time

from cornerstone_bridge.compac_protocol import (
    ACK,
    ENQ,
    STX,
    CompacRecvBuffer,
    build_sample_telegram,
    build_status_request,
    build_status_response,
    parse_sample_telegram,
    parse_status_response,
    validate_telegram,
)


def test_build_and_parse_roundtrip():
    frame = build_sample_telegram("SAMPLE-001", "Carbon Steel")
    ok, err = validate_telegram(frame)
    assert ok, err
    fields, perr = parse_sample_telegram(frame)
    assert perr is None
    assert fields is not None
    assert fields.sample_id == "SAMPLE-001"
    assert fields.sample_type == "Carbon Steel"
    assert len(frame) == 46
    assert frame[0] == STX


def test_status_request_and_response():
    req = build_status_request()
    assert req == b"\x01 A REQUEST\r\n"
    line = b"\x01 A 1000000000 12\r\n"
    msg, err = parse_status_response(line)
    assert err is None
    assert msg is not None
    assert msg.automatic_mode is True
    assert msg.error_code == 12


def test_recv_buffer_single_telegram():
    frame = build_sample_telegram("ABC", "Type1")
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(frame)
    dr = buf.drain()
    assert len(dr.telegrams) == 1
    assert dr.telegrams[0] == frame
    assert buf.buf == b""


def test_recv_buffer_sticky_two_telegrams():
    f1 = build_sample_telegram("A", "T1")
    f2 = build_sample_telegram("B", "T2")
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(f1 + f2)
    dr = buf.drain()
    assert len(dr.telegrams) == 2


def test_recv_buffer_split_telegram():
    frame = build_sample_telegram("SPLIT", "Test")
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(frame[:20])
    dr1 = buf.drain()
    assert dr1.telegrams == []
    buf.append(frame[20:])
    dr2 = buf.drain()
    assert len(dr2.telegrams) == 1


def test_recv_buffer_discards_garbage_until_stx():
    frame = build_sample_telegram("X", "Y")
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(b"GARBAGE" + frame)
    dr = buf.drain()
    assert dr.bytes_discarded == 7
    assert len(dr.telegrams) == 1


def test_recv_buffer_bad_checksum_discarded():
    frame = bytearray(build_sample_telegram("BAD", "CKS"))
    frame[-4] = ord("0") ^ 0x01  # corrupt CKS
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(bytes(frame))
    dr = buf.drain()
    assert dr.telegrams == []
    assert dr.anomalies


def test_validate_skip_bct_cks():
    frame = bytearray(build_sample_telegram("BAD", "CKS"))
    frame[-4] = ord("0") ^ 0x01
    ok, err = validate_telegram(bytes(frame), verify_bct_cks=False)
    assert ok, err
    fields, perr = parse_sample_telegram(bytes(frame), verify_bct_cks=False)
    assert perr is None
    assert fields is not None


def test_recv_buffer_skip_bct_cks_accepts_bad_frame():
    frame = bytearray(build_sample_telegram("OK", "Skip"))
    frame[2] = ord("9")
    buf = CompacRecvBuffer(idle_clear_sec=0, verify_bct_cks=False)
    buf.append(bytes(frame))
    dr = buf.drain()
    assert len(dr.telegrams) == 1


def test_recv_buffer_status_request():
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(build_status_request())
    dr = buf.drain()
    assert len(dr.status_requests) == 1
    assert dr.status_lines == []


def test_build_status_response_roundtrip():
    resp = build_status_response("1000000000", 7)
    msg, err = parse_status_response(resp)
    assert err is None
    assert msg.error_code == 7
    assert msg.automatic_mode is True


def test_recv_buffer_control_byte():
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(bytes([ACK]))
    dr = buf.drain()
    assert dr.control_bytes == [ACK]


def test_recv_buffer_status_line():
    line = b"\x01 A 0100000000 5\r\n"
    buf = CompacRecvBuffer(idle_clear_sec=0)
    buf.append(line)
    dr = buf.drain()
    assert len(dr.status_lines) == 1


def test_recv_buffer_idle_clear():
    buf = CompacRecvBuffer(idle_clear_sec=0.01)
    buf.append(b"partial", now=1.0)
    cleared = buf.append(b"X", now=2.0)
    assert cleared
    assert buf.buf == b"X"
