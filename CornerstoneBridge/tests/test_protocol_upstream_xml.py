"""Upstream XML framing helpers (InstrumentInfo / Prerequisites / concat)."""
import struct

from cornerstone_bridge.protocol import (
    frame_xml_defect,
    frame_xml_routable,
    format_payload_segmentation_diagnostics,
    inner_framed_needs_more_tcp,
    inner_framed_tcp_payload_complete,
    inner_framed_total_bytes,
    segment_cornerstone_payload,
    split_concatenated_xml_documents,
    unwrap_cornerstone_payload_segments,
    utf16le_inner_length_at,
    _parse_cookie_from_payload,
    _root_tag,
)

ENC = "utf-16-le"


def _inner_framed(xml: str) -> bytes:
    raw = xml.encode(ENC)
    return struct.pack("<I", len(raw)) + raw


def test_split_instrument_info_and_status():
    ii = (
        '<InstrumentInfo ErrorCode="0" Cookie="abc">'
        '<Field Label="x">y</Field></InstrumentInfo>'
    )
    st = '<Status ErrorCode="0" Cookie="def"><Elements></Elements></Status>'
    parts = split_concatenated_xml_documents(ii + st)
    assert len(parts) == 2
    assert frame_xml_defect(parts[0]) is None
    assert frame_xml_defect(parts[1]) is None
    assert _parse_cookie_from_payload(parts[0]) == "abc"
    assert _root_tag(parts[1]) == "Status"


def test_routable_unclosed_prerequisites():
    pr = (
        '<Prerequisites ErrorCode="0" Cookie="520b9b268bd911e14b5fe551d099a2fe">\n'
        '  <Prerequisite Name="LMLC Loaded" Value="true" />'
    )
    assert frame_xml_defect(pr) is not None
    assert frame_xml_routable(pr) is True
    assert _parse_cookie_from_payload(pr) == "520b9b268bd911e14b5fe551d099a2fe"
    assert _root_tag(pr) == "Prerequisites"


def test_segment_two_inner_frames_in_one_tcp_payload():
    """同一 TCP 外层正文内连续两段 [inner_len][xml] 应拆成 2 段且均可解析。"""
    hb = '<Heartbeat ErrorCode="0" ErrorMessage="Success" Cookie="aa" />'
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="bb">'
        "false</RemoteControlState>"
    )
    payload = _inner_framed(hb) + _inner_framed(rcs)
    meta = segment_cornerstone_payload(payload, ENC)
    assert len(meta) == 2
    assert meta[0].inner_trusted and meta[0].mode == "trusted_inner"
    assert meta[1].inner_trusted and meta[1].mode == "trusted_inner"
    segs = unwrap_cornerstone_payload_segments(payload, ENC)
    assert len(segs) == 2
    assert frame_xml_defect(segs[0].decode(ENC)) is None
    assert frame_xml_defect(segs[1].decode(ENC)) is None
    assert _root_tag(segs[0].decode(ENC)) == "Heartbeat"
    assert _root_tag(segs[1].decode(ENC)) == "RemoteControlState"


def test_segment_inner_len_exceeds_tcp_payload():
    """inner 声称长度大于 TCP 正文：不信任 inner，整段余量作一条（现场 20:29:19 类）。"""
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="49dc3512a">'
        "false</RemoteControlState>"
    )
    raw = rcs.encode(ENC)
    # 故意写 inner_len=248，但 TCP 外层只有 4+部分正文
    bad = struct.pack("<I", 248) + raw[:140]
    assert len(bad) == 144
    meta = segment_cornerstone_payload(bad, ENC)
    assert len(meta) == 1
    assert meta[0].inner_length_header == 248
    assert meta[0].inner_trusted is False
    assert meta[0].mode == "untrusted_inner_remainder"
    assert len(meta[0].data) == 140


def test_inner_framed_complete_rule():
    """inner_len == len(tcp_payload) - 4 判定完整（用户现场规则）。"""
    hb = '<Heartbeat ErrorCode="0" ErrorMessage="Success" Cookie="x" />'
    raw = hb.encode(ENC)
    payload = struct.pack("<I", len(raw)) + raw
    assert utf16le_inner_length_at(payload) == len(raw)
    assert inner_framed_tcp_payload_complete(payload)
    assert not inner_framed_needs_more_tcp(payload)
    assert inner_framed_total_bytes(len(raw)) == len(payload)


def test_inner_framed_incomplete_needs_more():
    """20:29:19 类：inner_hdr=248 但 TCP 正文仅 152。"""
    rcs = '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="49">'
    raw = rcs.encode(ENC)
    partial = struct.pack("<I", 248) + raw[: min(140, len(raw))]
    assert utf16le_inner_length_at(partial) == 248
    assert not inner_framed_tcp_payload_complete(partial)
    assert inner_framed_needs_more_tcp(partial)


def test_inner_framed_split_across_two_tcp_chunks():
    """两段 TCP 正文拼接后满足 inner_len == len-4。"""
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="ab">'
        "false</RemoteControlState>"
    )
    raw = rcs.encode(ENC)
    inner = len(raw)
    tcp1 = struct.pack("<I", inner) + raw[:140]
    tcp2 = raw[140:]
    assert inner_framed_needs_more_tcp(tcp1)
    merged = tcp1 + tcp2
    assert inner_framed_tcp_payload_complete(merged)
    assert frame_xml_defect(merged[4:].decode(ENC)) is None


def test_field_layout_248_inner152_plus_rcs_tail():
    """现场 06:55:04：单条 TCP outer=248 = [inner152][HB×152][RCS 前缀×92]。"""
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
    meta = segment_cornerstone_payload(tcp248, ENC)
    assert len(meta) == 2
    assert meta[0].inner_length_header == 152
    assert meta[1].mode == "raw_xml_tail"
    assert frame_xml_defect(tcp248[4:156].decode(ENC)) is None
    assert frame_xml_defect(tcp248[156:].decode(ENC)) is not None
    merged_rcs = tcp248[156:] + rcs_raw[92:]
    assert inner_framed_tcp_payload_complete(merged_rcs) or frame_xml_defect(
        merged_rcs.decode(ENC)
    ) is None


def test_segment_first_inner_second_plain_xml():
    """第一段 inner framing，第二段无 inner 前缀、直接以 < 开头。"""
    hb = '<Heartbeat ErrorCode="0" ErrorMessage="Success" Cookie="c1" />'
    rcs = (
        '<RemoteControlState ErrorCode="0" ErrorMessage="Success" Cookie="c2">'
        "true</RemoteControlState>"
    )
    payload = _inner_framed(hb) + rcs.encode(ENC)
    meta = segment_cornerstone_payload(payload, ENC)
    assert len(meta) == 2
    assert meta[0].mode == "trusted_inner"
    assert meta[1].mode == "raw_xml_tail"
    assert _root_tag(meta[1].data.decode(ENC)) == "RemoteControlState"


if __name__ == "__main__":
    test_split_instrument_info_and_status()
    test_routable_unclosed_prerequisites()
    test_inner_framed_complete_rule()
    test_inner_framed_incomplete_needs_more()
    test_inner_framed_split_across_two_tcp_chunks()
    test_field_layout_248_inner152_plus_rcs_tail()
    test_segment_two_inner_frames_in_one_tcp_payload()
    test_segment_inner_len_exceeds_tcp_payload()
    test_segment_first_inner_second_plain_xml()
    print("ok")
