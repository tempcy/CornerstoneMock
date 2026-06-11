"""TCP 客户端列表：阻止列表 IP 在无连接时仍可见。"""
from cornerstone_bridge.hub import GatewayHub


def _hub(**kwargs):
    defaults = {
        "upstream_host": "127.0.0.1",
        "upstream_port": 1,
        "encoding": "utf-16",
        "add_samples_queue_size": 8,
        "synthetic_logon_after_first": True,
        "instrument_short_connection": False,
        "upstream_heartbeat_interval_s": 60.0,
        "upstream_auto_reconnect": True,
        "web_user": "",
        "web_password": "",
    }
    defaults.update(kwargs)
    return GatewayHub(**defaults)


def test_policy_only_entries_from_blocklists():
    hub = _hub(
        blocked_connect_hosts=["192.168.1.10"],
        blocked_logon_hosts=["10.0.0.5", "192.168.1.10"],
    )
    entries = hub._policy_only_tcp_client_entries(set())
    hosts = {e["peerHost"] for e in entries}
    assert hosts == {"10.0.0.5", "192.168.1.10"}
    by_host = {e["peerHost"]: e for e in entries}
    assert by_host["192.168.1.10"]["connectBlocked"] is True
    assert by_host["192.168.1.10"]["logonBlocked"] is True
    assert by_host["10.0.0.5"]["connectBlocked"] is False
    assert by_host["10.0.0.5"]["logonBlocked"] is True
    assert all(e.get("policyOnly") for e in entries)


def test_policy_only_skips_active_hosts():
    hub = _hub(blocked_connect_hosts=["192.168.1.10"])
    assert hub._policy_only_tcp_client_entries({"192.168.1.10"}) == []


def test_policy_only_ignores_corrupt_empty_marker():
    hub = _hub(blocked_connect_hosts=["[]"], blocked_logon_hosts=["10.0.0.5", "[]"])
    entries = hub._policy_only_tcp_client_entries(set())
    assert len(entries) == 1
    assert entries[0]["peerHost"] == "10.0.0.5"


def test_remove_blocked_hosts():
    hub = _hub(blocked_connect_hosts=["1.2.3.4"], blocked_logon_hosts=["5.6.7.8"])
    assert hub.remove_blocked_connect_host("1.2.3.4") is True
    assert hub.remove_blocked_connect_host("1.2.3.4") is False
    assert hub.blocked_connect_hosts_snapshot() == []
    assert hub.remove_blocked_logon_host("5.6.7.8") is True
    assert hub.blocked_logon_hosts_snapshot() == []
