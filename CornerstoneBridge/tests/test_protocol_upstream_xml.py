"""Upstream XML framing helpers (InstrumentInfo / Prerequisites / concat)."""
from cornerstone_bridge.protocol import (
    frame_xml_defect,
    frame_xml_routable,
    split_concatenated_xml_documents,
    _parse_cookie_from_payload,
    _root_tag,
)


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


if __name__ == "__main__":
    test_split_instrument_info_and_status()
    test_routable_unclosed_prerequisites()
    print("ok")
