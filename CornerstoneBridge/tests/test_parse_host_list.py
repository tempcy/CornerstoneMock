"""blocked_*_hosts 配置解析：过滤误写的 [] 等占位符。"""
from cornerstone_bridge.hub_helpers import _parse_host_list


def test_empty_array_literals_ignored() -> None:
    assert _parse_host_list([]) == []
    assert _parse_host_list("[]") == []
    assert _parse_host_list('["[]"]') == []
    assert _parse_host_list(["[]"]) == []
    assert _parse_host_list(["10.50.10.11", "[]"]) == ["10.50.10.11"]


def test_json_array_string() -> None:
    assert _parse_host_list('["192.168.1.1", "10.0.0.2"]') == [
        "192.168.1.1",
        "10.0.0.2",
    ]


def test_normal_hosts() -> None:
    assert _parse_host_list("10.50.10.11") == ["10.50.10.11"]
    assert _parse_host_list(["10.50.10.11", "10.50.10.11"]) == ["10.50.10.11"]
