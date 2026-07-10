"""TCP 客户端列表：策略列表 IP 在无连接时仍可见。"""
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


def test_policy_only_entries_from_policy_lists():
    hub = _hub(
        blocked_connect_hosts=["192.168.1.10"],
        allowed_logon_hosts=["10.0.0.5", "192.168.1.10"],
        allowed_query_hosts=["10.0.0.6"],
    )
    entries = hub._policy_only_tcp_client_entries(set())
    hosts = {e["peerHost"] for e in entries}
    assert hosts == {"10.0.0.5", "10.0.0.6", "192.168.1.10"}
    by_host = {e["peerHost"]: e for e in entries}
    assert by_host["192.168.1.10"]["connectBlocked"] is True
    assert by_host["192.168.1.10"]["logonAllowed"] is True
    assert by_host["10.0.0.5"]["connectBlocked"] is False
    assert by_host["10.0.0.5"]["logonAllowed"] is True
    assert by_host["10.0.0.6"]["queryAllowed"] is True
    assert all(e.get("policyOnly") for e in entries)


def test_policy_only_skips_active_hosts():
    hub = _hub(blocked_connect_hosts=["192.168.1.10"])
    assert hub._policy_only_tcp_client_entries({"192.168.1.10"}) == []


def test_policy_only_ignores_corrupt_empty_marker():
    hub = _hub(blocked_connect_hosts=["[]"], allowed_logon_hosts=["10.0.0.5", "[]"])
    entries = hub._policy_only_tcp_client_entries(set())
    assert len(entries) == 1
    assert entries[0]["peerHost"] == "10.0.0.5"


def test_allowlist_hosts():
    hub = _hub(
        blocked_connect_hosts=["1.2.3.4"],
        allowed_logon_hosts=["5.6.7.8"],
        allowed_query_hosts=["9.9.9.9"],
    )
    assert hub.remove_blocked_connect_host("1.2.3.4") is True
    assert hub.blocked_connect_hosts_snapshot() == []
    assert hub.remove_allowed_logon_host("5.6.7.8") is True
    assert hub.allowed_logon_hosts_snapshot() == []
    assert hub.remove_allowed_query_host("9.9.9.9") is True
    assert hub.allowed_query_hosts_snapshot() == []


def test_empty_allowlist_denies_all():
    hub = _hub(allowed_logon_hosts=[], allowed_query_hosts=[])
    assert hub.is_host_allowed_logon("1.2.3.4") is False
    assert hub.is_host_allowed_query("1.2.3.4") is False


def test_allowlist_permits_listed_hosts():
    hub = _hub(allowed_logon_hosts=["1.2.3.4"], allowed_query_hosts=["5.6.7.8"])
    assert hub.is_host_allowed_logon("1.2.3.4") is True
    assert hub.is_host_allowed_logon("9.9.9.9") is False
    assert hub.is_host_allowed_query("5.6.7.8") is True
    assert hub.is_host_allowed_query("1.2.3.4") is False


def test_synthesize_client_logon_when_web_creds_manage_upstream():
    hub = _hub(web_user="remote", web_password="secret", instrument_short_connection=False)
    assert hub.should_synthesize_client_logon() is True


def test_synthesize_client_logon_without_web_creds_needs_upstream_success():
    hub = _hub(web_user="", web_password="", instrument_short_connection=False)
    assert hub.should_synthesize_client_logon() is False
    hub._logon_seen_upstream_success = True
    assert hub.should_synthesize_client_logon() is True
