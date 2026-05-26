"""UpstreamRecvBuffer：粘包 / 拆包 / 错码 / 空闲清缓冲。"""
import struct
import time

from cornerstone_bridge.upstream_framing import UpstreamRecvBuffer

ENC = "utf-16-le"


def _inner_framed(xml: str) -> bytes:
    raw = xml.encode(ENC)
    return struct.pack("<I", len(raw)) + raw


def test_plain_utf16_xml_without_inner_header():
    """外层 TCP 正文仅为 UTF-16 XML（无 inner_len 前缀）。"""
    xml = '<Logon ErrorCode="0" ErrorMessage="Success" Cookie="ab" />'
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=0)
    buf.append(xml.encode(ENC))
    dr = buf.drain()
    assert len(dr.packets) == 1
    assert "Logon" in dr.packets[0].decode(ENC)


def test_single_complete_packet():
    hb = '<Heartbeat ErrorCode="0" Cookie="x" />'
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=0)
    buf.append(_inner_framed(hb))
    dr = buf.drain()
    assert len(dr.packets) == 1
    assert dr.packets[0].decode(ENC) == hb
    assert buf.buf == b""
    assert not buf.needs_more()


def test_sticky_two_packets():
    hb = '<Heartbeat ErrorCode="0" Cookie="a" />'
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="b">'
        "false</RemoteControlState>"
    )
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=0)
    buf.append(_inner_framed(hb) + _inner_framed(rcs))
    dr = buf.drain()
    assert len(dr.packets) == 2
    assert "Heartbeat" in dr.packets[0].decode(ENC)
    assert "RemoteControlState" in dr.packets[1].decode(ENC)


def test_split_packet_across_appends():
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="ab">'
        "false</RemoteControlState>"
    )
    raw = rcs.encode(ENC)
    inner = len(raw)
    tcp1 = struct.pack("<I", inner) + raw[:80]
    tcp2 = raw[80:]
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=30)
    buf.append(tcp1)
    assert buf.needs_more()
    assert buf.bytes_needed() == len(tcp2)
    dr1 = buf.drain()
    assert dr1.packets == []
    buf.append(tcp2)
    dr2 = buf.drain()
    assert len(dr2.packets) == 1
    assert dr2.packets[0] == raw


def test_oversize_inner_clears_buffer():
    bad = struct.pack("<I", 32 * 1024 * 1024) + b"<\x00/\x00>\x00"
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=0)
    buf.append(bad)
    dr = buf.drain()
    assert dr.buffer_cleared
    assert dr.anomalies and dr.anomalies[0].kind == "sync_lost"
    assert buf.buf == b""


def test_bad_magic_clears_buffer():
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=0)
    buf.append(struct.pack("<I", 8) + b"\xff\xff\xff\xff")
    dr = buf.drain()
    assert dr.buffer_cleared
    assert buf.buf == b""


def test_idle_clear_before_append():
    buf = UpstreamRecvBuffer(idle_clear_sec=0.05, incomplete_timeout_s=0)
    # 留半包在缓冲内，空闲超时后下次 append 应清空旧数据
    partial = struct.pack("<I", 40) + b"<\x00H\x00"
    buf.append(partial)
    assert buf.needs_more()
    time.sleep(0.08)
    cleared = buf.append(_inner_framed('<Heartbeat ErrorCode="0" Cookie="y" />'))
    assert cleared is True
    dr = buf.drain()
    assert len(dr.packets) == 1


def test_field_layout_248_inner152_plus_rcs_tail():
    """现场 tcp_outer=248：inner152 Heartbeat + 92 字节 RCS 前缀，须续包后再 drain。"""
    hb_prefix = '<Heartbeat ErrorCode="0" ErrorMessage="Success" Cookie="'
    hb_suffix = '" />'
    cookie_len = (152 // 2) - len(hb_prefix) - len(hb_suffix)
    hb_raw = (hb_prefix + ("0" * max(cookie_len, 1)) + hb_suffix).encode(ENC)
    assert len(hb_raw) == 152
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="ab">'
        "false</RemoteControlState>"
    )
    rcs_raw = rcs.encode(ENC)
    tcp248 = struct.pack("<I", 152) + hb_raw + rcs_raw[:92]
    assert len(tcp248) == 248

    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=30)
    buf.append(tcp248)
    dr1 = buf.drain()
    assert len(dr1.packets) == 1
    assert "Heartbeat" in dr1.packets[0].decode(ENC)
    assert len(buf.buf) == 92
    assert buf.needs_more() is False

    buf.append(rcs_raw[92:])
    dr2 = buf.drain()
    assert len(dr2.packets) == 1
    assert "RemoteControlState" in dr2.packets[0].decode(ENC)
    assert buf.buf == b""


def test_bad_xml_packet_dropped_but_buffer_continues():
    """单条坏包清空同步；规划 Phase1 对 magic 错清 buf。后续 good 包独立 append。"""
    good = _inner_framed('<Heartbeat ErrorCode="0" Cookie="z" />')
    buf = UpstreamRecvBuffer(idle_clear_sec=0, incomplete_timeout_s=0)
    buf.append(struct.pack("<I", 4) + b"\xff\x00\xff\x00")
    dr_bad = buf.drain()
    assert dr_bad.buffer_cleared
    buf.append(good)
    dr_good = buf.drain()
    assert len(dr_good.packets) == 1


if __name__ == "__main__":
    test_field_layout_248_inner152_plus_rcs_tail()
    test_single_complete_packet()
    test_sticky_two_packets()
    test_split_packet_across_appends()
    test_oversize_inner_clears_buffer()
    test_bad_magic_clears_buffer()
    test_idle_clear_before_append()
    test_bad_xml_packet_dropped_but_buffer_continues()
    print("ok")
