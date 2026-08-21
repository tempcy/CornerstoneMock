"""RQ ErrorCode success rules (Commands 6.x omits ErrorCode on success)."""
from cornerstone_bridge.hub import GatewayHub
from cornerstone_bridge.hub_helpers import _upstream_rq_error_code_ok


def test_rq_error_code_ok_empty_or_zero():
    assert _upstream_rq_error_code_ok(None)
    assert _upstream_rq_error_code_ok("")
    assert _upstream_rq_error_code_ok("0")
    assert _upstream_rq_error_code_ok(" 0 ")
    assert not _upstream_rq_error_code_ok("5")
    assert not _upstream_rq_error_code_ok("2")


def test_instrument_response_dict_omitted_error_code_ok():
    xml = (
        '<SystemParameters Cookie="abc">'
        '<field label="Mode" rawValue="Conservation">Conserve Gas</field>'
        "</SystemParameters>"
    )
    r = GatewayHub._instrument_response_dict(xml)
    assert r["ok"] is True
    assert r["rootTag"] == "SystemParameters"
    assert r["error"] == ""


def test_instrument_response_dict_error_code_zero_ok():
    xml = (
        '<Status ErrorCode="0" ErrorMessage="Success" Cookie="x">'
        "<Elements><User>USER</User></Elements>"
        "</Status>"
    )
    r = GatewayHub._instrument_response_dict(xml)
    assert r["ok"] is True


def test_instrument_response_dict_error_code_nonzero_fails():
    xml = '<Sets ErrorCode="5" ErrorMessage="Not authorized" Cookie="y"/>'
    r = GatewayHub._instrument_response_dict(xml)
    assert r["ok"] is False
    assert "ErrorCode=5" in r["error"]


def test_instrument_response_dict_child_element_error_code():
    xml = (
        "<Status Cookie=\"z\">"
        "<ErrorCode>5</ErrorCode>"
        "<ErrorMessage>Denied</ErrorMessage>"
        "</Status>"
    )
    r = GatewayHub._instrument_response_dict(xml)
    assert r["ok"] is False
    assert "ErrorCode=5" in r["error"]
