"""Gateway XML log summaries (no Cookie in log lines)."""
import logging

from cornerstone_bridge.bridge_logging import log_gateway_xml
from cornerstone_bridge.protocol import _gateway_xml_log_detail


def test_logon_out_shows_user_not_cookie():
    xml = (
        '<Logon Cookie="secret-cookie-value">'
        "<User>remote</User><Password>hidden</Password></Logon>"
    )
    detail = _gateway_xml_log_detail(xml)
    assert "user=remote" in detail
    assert "cookie" not in detail.lower()
    assert "hidden" not in detail
    assert "password" not in detail.lower()


def test_logon_in_shows_error_fields():
    xml = (
        '<Logon Cookie="abc">'
        "<ErrorCode>0</ErrorCode><ErrorMessage>Success.</ErrorMessage></Logon>"
    )
    detail = _gateway_xml_log_detail(xml)
    assert "ec=0" in detail
    assert "msg=Success." in detail
    assert "cookie" not in detail.lower()


def test_addsamples_shows_name():
    xml = (
        '<AddSamples Cookie="xyz"><Set><Field Id="Name">Steel Sample</Field>'
        '<Field Id="Description">Batch A</Field></Set></AddSamples>'
    )
    detail = _gateway_xml_log_detail(xml)
    assert "name=Steel Sample" in detail
    assert "desc=" not in detail
    assert "Batch A" not in detail


def test_addsamples_omits_setkey_only():
    xml = (
        '<AddSamples Cookie="xyz"><SetKey>520b9b268bd911e14b5fe551d099a2fe</SetKey></AddSamples>'
    )
    detail = _gateway_xml_log_detail(xml)
    assert "setkey" not in detail.lower()
    assert "520b9b26" not in detail


def test_heartbeat_no_detail():
    xml = '<Heartbeat ErrorCode="0" ErrorMessage="Success" Cookie="hb" />'
    assert _gateway_xml_log_detail(xml) == ""


def test_log_gateway_xml_line_format():
    logger = logging.getLogger("cornerstone.bridge.test_gw_xml")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    captured: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger.addHandler(_H())
    xml = '<Logon Cookie="c"><User>remote</User><Password>x</Password></Logon>'
    log_gateway_xml(logger, "client IN", xml)
    assert len(captured) == 1
    assert "client IN tag=Logon user=remote bytes=" in captured[0]
    assert "cookie" not in captured[0].lower()
