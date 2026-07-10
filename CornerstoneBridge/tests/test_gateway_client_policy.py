"""gateway：客户端 IP 策略丢弃登录 / RQ 类 XML。"""
from cornerstone_bridge.bridge_logging import is_logon_block_xml_tag, is_rq_xml_tag


def test_logon_block_xml_tags():
    assert is_logon_block_xml_tag("Logon")
    assert is_logon_block_xml_tag("Logoff")
    assert not is_logon_block_xml_tag("AddSamples")
    assert not is_logon_block_xml_tag("LastRemoteAddedSets")
    assert not is_logon_block_xml_tag("Status")
    assert not is_logon_block_xml_tag("Heartbeat")


def test_rq_xml_tags():
    assert is_rq_xml_tag("Status")
    assert is_rq_xml_tag("SetsEx")
    assert is_rq_xml_tag("heartbeat")
    assert not is_rq_xml_tag("Logon")
    assert not is_rq_xml_tag("AddSamples")
