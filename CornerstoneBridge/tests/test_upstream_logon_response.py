"""Upstream Logon response parsing (attribute vs child-element ErrorCode)."""
from cornerstone_bridge.hub_helpers import (
    _upstream_logon_response_ok,
    _upstream_xml_error_code,
)


def test_logon_ok_attribute_format():
    xml = (
        '<Logon ErrorCode="0" ErrorMessage="Success" Cookie="abc" />'
    )
    assert _upstream_xml_error_code(xml) == "0"
    assert _upstream_logon_response_ok(xml)


def test_logon_ok_child_element_success_with_period():
    xml = (
        '<Logon Cookie="7798618ff1143cddb4612e3df73a0447">'
        "<ErrorCode>0</ErrorCode>"
        "<ErrorMessage>Success.</ErrorMessage>"
        "</Logon>"
    )
    assert _upstream_xml_error_code(xml) == "0"
    assert _upstream_logon_response_ok(xml)


def test_logon_ok_child_element_empty_message():
    xml = (
        '<Logon Cookie="2a06416b68ee170379423741a96f3653">'
        "<ErrorCode>0</ErrorCode>"
        "<ErrorMessage></ErrorMessage>"
        "</Logon>"
    )
    assert _upstream_logon_response_ok(xml)


def test_logon_rejected_child_element_error_code():
    xml = (
        '<Logon Cookie="7798618ff1143cddb4612e3df73a0447">'
        "<ErrorCode>2</ErrorCode>"
        "<ErrorMessage></ErrorMessage>"
        "</Logon>"
    )
    assert _upstream_xml_error_code(xml) == "2"
    assert not _upstream_logon_response_ok(xml)


def test_logon_rejected_nonzero_attribute():
    xml = '<Logon ErrorCode="2" ErrorMessage="" Cookie="x" />'
    assert not _upstream_logon_response_ok(xml)


def test_logon_establishes_session_error_code_2():
    from cornerstone_bridge.hub_helpers import _upstream_logon_establishes_session

    xml = (
        '<Logon Cookie="7798618ff1143cddb4612e3df73a0447">'
        "<ErrorCode>2</ErrorCode>"
        "<ErrorMessage></ErrorMessage>"
        "</Logon>"
    )
    assert not _upstream_logon_response_ok(xml)
    assert _upstream_logon_establishes_session(xml)
