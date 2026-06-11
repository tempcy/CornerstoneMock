"""Upstream stale recover / reconnect helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from cornerstone_bridge.hub import GatewayHub


def _make_hub(**kwargs: object) -> GatewayHub:
    defaults = {
        "upstream_host": "127.0.0.1",
        "upstream_port": 12345,
        "encoding": "utf-16-le",
        "add_samples_queue_size": 8,
        "synthetic_logon_after_first": True,
        "instrument_short_connection": False,
        "upstream_heartbeat_interval_s": 60.0,
        "upstream_auto_reconnect": True,
        "upstream_client_forward_timeout_s": 10.0,
        "web_user": "",
        "web_password": "",
    }
    defaults.update(kwargs)
    return GatewayHub(**defaults)


def test_effective_heartbeat_wait_timeout_auto() -> None:
    hub = _make_hub(upstream_client_forward_timeout_s=10.0, upstream_heartbeat_wait_timeout_s=0.0)
    assert hub._effective_heartbeat_wait_timeout_s() == 15.0

    hub2 = _make_hub(upstream_client_forward_timeout_s=20.0, upstream_heartbeat_wait_timeout_s=0.0)
    assert hub2._effective_heartbeat_wait_timeout_s() == 20.0

    hub3 = _make_hub(upstream_heartbeat_wait_timeout_s=12.0)
    assert hub3._effective_heartbeat_wait_timeout_s() == 12.0


def test_activity_stale_seconds_auto_and_explicit() -> None:
    hub = _make_hub(upstream_heartbeat_interval_s=60.0, upstream_activity_stale_seconds=0.0)
    assert hub._upstream_activity_stale_seconds() == 180.0

    hub_hb_off = _make_hub(upstream_heartbeat_interval_s=0.0, upstream_activity_stale_seconds=0.0)
    assert hub_hb_off._upstream_activity_stale_seconds() == 0.0

    hub2 = _make_hub(upstream_activity_stale_seconds=120.0)
    assert hub2._upstream_activity_stale_seconds() == 120.0

    hub3 = _make_hub(upstream_heartbeat_interval_s=0.0, upstream_activity_stale_seconds=90.0)
    assert hub3._upstream_activity_stale_seconds() == 90.0


def test_active_heartbeat_only_when_upstream_idle() -> None:
    import time

    hub = _make_hub(upstream_heartbeat_interval_s=60.0)
    assert hub._upstream_needs_active_heartbeat()

    hub._last_upstream_rx_at = time.time() - 10.0
    assert not hub._upstream_needs_active_heartbeat()

    hub._last_upstream_rx_at = time.time() - 90.0
    assert hub._upstream_needs_active_heartbeat()

    hub2 = _make_hub(upstream_heartbeat_interval_s=60.0)
    hub2._last_upstream_heartbeat_reply_at = time.time() - 5.0
    assert not hub2._upstream_needs_active_heartbeat()


@pytest.mark.asyncio
async def test_recover_increments_generation_and_resets_streaks() -> None:
    hub = _make_hub()
    hub._upstream_command_fail_streak = 5
    hub._upstream_heartbeat_fail_streak = 2
    gen0 = hub._upstream_recover_generation

    with patch.object(hub, "_drop_upstream_transport", new_callable=AsyncMock):
        with patch.object(hub, "_schedule_upstream_reconnect") as mock_sched:
            await hub._recover_stale_upstream("command_streak")

    assert hub._upstream_recover_generation == gen0 + 1
    assert hub._upstream_command_fail_streak == 0
    assert hub._upstream_heartbeat_fail_streak == 0
    mock_sched.assert_called_once_with(replace=True)


@pytest.mark.asyncio
async def test_recover_when_transport_down_still_schedules_reconnect() -> None:
    hub = _make_hub()
    assert not hub._upstream_transport_usable()

    with patch.object(hub, "_drop_upstream_transport", new_callable=AsyncMock) as mock_drop:
        with patch.object(hub, "_schedule_upstream_reconnect") as mock_sched:
            await hub._recover_stale_upstream("command_streak")

    mock_drop.assert_not_called()
    mock_sched.assert_called_once_with(replace=True)


@pytest.mark.asyncio
async def test_forward_timeout_watcher_skipped_after_recover_generation() -> None:
    hub = _make_hub(upstream_client_forward_timeout_s=0.05)
    gen = hub._upstream_recover_generation
    hub._upstream_recover_generation = gen + 1

    with patch.object(hub, "_record_upstream_command_failure") as mock_fail:
        await hub._watch_client_forward_timeout("abc", AsyncMock(), "Status")

    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_should_recycle_on_activity_stale() -> None:
    hub = _make_hub(upstream_activity_stale_seconds=60.0)
    hub._last_upstream_rx_at = 0.0
    should, reason = hub._should_recycle_upstream()
    assert not should

    import time

    hub._last_upstream_rx_at = time.time() - 120.0
    should, reason = hub._should_recycle_upstream()
    assert should
    assert reason == "activity_stale"
