"""Setup, unload, missing feeds, outages and recovery."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.caltrans_roads.api import feed_url
from custom_components.caltrans_roads.const import DOMAIN, FEED_CAMERAS, FEEDS

from .conftest import d3_responses, fixture_bytes, make_entry, mock_feeds, restamped, setup_entry

CHAIN = "sensor.i_80_chain_control"


async def _tick(hass: HomeAssistant, frozen, delta: timedelta) -> None:
    frozen.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    entry = await setup_entry(hass, make_entry())
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(CHAIN).state == "r2"
    # Cameras are off by default, so their feed is never fetched.
    assert all(feed_url(3, FEED_CAMERAS) != str(call[1]) for call in d3_feeds.mock_calls)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_district_without_chain_control_or_weather(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """District 4 publishes no chain control or road weather; those are never requested."""
    mock_feeds(aioclient_mock, 4, {f: fixture_bytes(f"d04_{f}.json") for f in ("cms", "lcs", "cctv")})
    entry = await setup_entry(hass, make_entry(district=4, routes=["US-101", "SR-1"]))
    assert entry.state is ConfigEntryState.LOADED
    requested = {str(call[1]) for call in aioclient_mock.mock_calls}
    assert feed_url(4, "cc") not in requested
    assert feed_url(4, "rwis") not in requested
    assert hass.states.get("sensor.us_101_chain_control") is None
    assert hass.states.get("binary_sensor.us_101_chains_required") is None
    assert hass.states.get("sensor.us_101_message_signs").state == "0"  # both signs blank
    # A storm-damage repair closure in effect from 9 Sep to 9 Nov; the other one has finished.
    assert hass.states.get("sensor.sr_1_lane_closures").state == "1"


async def test_all_feeds_down_retries_setup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    mock_feeds(aioclient_mock, 3, dict.fromkeys(FEEDS, 500))
    entry = await setup_entry(hass, make_entry())
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_one_feed_down_at_start_then_arrives(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen):
    """Weather stations appear once their feed loads, even if it was down when Home Assistant started."""
    responses = d3_responses()
    responses["rwis"] = 503
    mock_feeds(aioclient_mock, 3, responses)
    await setup_entry(hass, make_entry())
    station = "sensor.i_80_hwy_80_at_donner_summit_air_temperature"
    assert hass.states.get(station) is None
    assert hass.states.get(CHAIN).state == "r2"

    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, d3_responses())
    await _tick(hass, frozen, timedelta(minutes=5))
    assert hass.states.get(station).state == "1.8"


async def test_outage_keeps_values_then_goes_unavailable_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen
):
    mock_feeds(aioclient_mock, 3, d3_responses())
    entry = await setup_entry(hass, make_entry())
    assert hass.states.get(CHAIN).state == "r2"

    # Caltrans goes down.
    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, dict.fromkeys(FEEDS, 500))

    # One failed fetch: the last good value stays.
    await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert hass.states.get(CHAIN).state == "r2"
    coordinator = entry.runtime_data.coordinators["cc"]
    assert coordinator.last_update_success is False
    assert "HTTP 500" in coordinator.last_error

    # Still failing after the 30 minute stale limit: unavailable.
    for _ in range(10):
        await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert hass.states.get(CHAIN).state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.i_80_chains_required").state == STATE_UNAVAILABLE

    # Data that is garbage counts as a failure too.
    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, dict.fromkeys(FEEDS, b"<html>busy</html>"))
    await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert hass.states.get(CHAIN).state == STATE_UNAVAILABLE
    assert "not valid JSON" in coordinator.last_error

    # Back up.
    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, d3_responses())
    await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert hass.states.get(CHAIN).state == "r2"
    assert coordinator.last_error is None


async def test_long_outage_raises_repair_issue_and_clears_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen
):
    mock_feeds(aioclient_mock, 3, d3_responses())
    entry = await setup_entry(hass, make_entry(feeds=["cc"]))
    issue_id = f"feed_unavailable_{entry.entry_id}_cc"
    registry = ir.async_get(hass)

    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, {"cc": 500})
    await _tick(hass, frozen, timedelta(hours=5))
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    await _tick(hass, frozen, timedelta(hours=1, minutes=5))
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders["feed"] == "chain control"

    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, {"cc": restamped("d03_cc.json")})
    await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get(CHAIN).state == "r2"


async def test_old_copy_of_a_file_counts_as_an_outage(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen):
    """Caltrans sometimes serves a frozen old file; chain controls from it must not look current."""
    mock_feeds(aioclient_mock, 3, d3_responses())
    entry = await setup_entry(hass, make_entry(feeds=["cc", "cms"]))
    coordinator = entry.runtime_data.coordinators["cc"]
    # The same file keeps being served while time moves on.
    for _ in range(50):
        await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert "old copy" in coordinator.last_error
    assert hass.states.get(CHAIN).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.i_80_message_signs").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, {"cc": restamped("d03_cc.json"), "cms": restamped("d03_cms.json")})
    await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert hass.states.get(CHAIN).state == "r2"
    assert hass.states.get("sensor.i_80_message_signs").state == "2"


async def test_unload_removes_repair_issue(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen):
    mock_feeds(aioclient_mock, 3, d3_responses())
    entry = await setup_entry(hass, make_entry(feeds=["cc"]))
    aioclient_mock.clear_requests()
    mock_feeds(aioclient_mock, 3, {"cc": 500})
    await _tick(hass, frozen, timedelta(hours=7))
    issue_id = f"feed_unavailable_{entry.entry_id}_cc"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_options_change_reloads(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    entry = await setup_entry(hass, make_entry())
    assert hass.states.get("sensor.sr_89_chain_control") is None
    hass.config_entries.async_update_entry(entry, options={**entry.options, "routes": ["SR-89"]})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sr_89_chain_control").state == "none"


async def test_dropped_route_and_data_type_are_cleaned_up(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.caltrans_roads import async_remove_config_entry_device

    entry = await setup_entry(hass, make_entry())
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    us50 = next(d for d in dr.async_entries_for_config_entry(devices, entry.entry_id) if d.name == "US-50")
    i80 = next(d for d in dr.async_entries_for_config_entry(devices, entry.entry_id) if d.name == "I-80")
    assert not await async_remove_config_entry_device(hass, entry, i80)
    assert entities.async_get("sensor.i_80_lane_closures") is not None

    hass.config_entries.async_update_entry(entry, options={"routes": ["I-80"], "data_types": ["cc", "rwis", "cms"]})
    await hass.async_block_till_done()
    assert devices.async_get(us50.id) is None
    assert entities.async_get("sensor.us_50_chain_control") is None
    assert entities.async_get("sensor.i_80_lane_closures") is None  # lane closures turned off
    assert entities.async_get("sensor.i_80_chain_control") is not None
    assert await async_remove_config_entry_device(hass, entry, us50)


async def test_dropped_route_device_removal_uses_current_api(
    hass: HomeAssistant, d3_feeds: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
):
    # Seen in a live 2026.10.1 instance: detaching the entry with remove_config_entry_id logs a
    # deprecation warning ("will stop working in Home Assistant 2027.8.0").
    from homeassistant.helpers import device_registry as dr

    entry = await setup_entry(hass, make_entry())
    devices = dr.async_get(hass)
    us50 = next(d for d in dr.async_entries_for_config_entry(devices, entry.entry_id) if d.name == "US-50")
    hass.config_entries.async_update_entry(entry, options={**entry.options, "routes": ["I-80"]})
    await hass.async_block_till_done()
    assert devices.async_get(us50.id) is None
    assert "remove_config_entry_id" not in caplog.text


async def test_repair_clock_survives_setup_retries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen):
    mock_feeds(aioclient_mock, 3, {"cc": 500})
    entry = await setup_entry(hass, make_entry(feeds=["cc"]))
    assert entry.state is ConfigEntryState.SETUP_RETRY
    for _ in range(8):
        frozen.tick(timedelta(hours=1))
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"feed_unavailable_{entry.entry_id}_cc") is not None


async def test_parser_crash_counts_as_failure(hass: HomeAssistant, d3_feeds: AiohttpClientMocker, frozen):
    from unittest.mock import patch

    entry = await setup_entry(hass, make_entry(feeds=["cc"]))
    coordinator = entry.runtime_data.coordinators["cc"]
    with patch.dict("custom_components.caltrans_roads.coordinator.PARSERS", {"cc": lambda raw: 1 / 0}):
        await _tick(hass, frozen, timedelta(minutes=3, seconds=1))
    assert "ZeroDivisionError" in coordinator.last_error
    assert coordinator.failing_since is not None


async def test_whole_weather_file_stale_is_an_outage(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen):
    """District 3's weather file served May readings on 26 Oct 2026: one repair notice, not silence."""
    mock_feeds(aioclient_mock, 3, {"rwis": fixture_bytes("d03_rwis.json")})
    entry = await setup_entry(hass, make_entry(feeds=["rwis"]))
    coordinator = entry.runtime_data.coordinators["rwis"]
    for _ in range(26):  # 13 hours of the same file
        await _tick(hass, frozen, timedelta(minutes=30))
    assert "old copy" in coordinator.last_error
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"feed_unavailable_{entry.entry_id}_rwis") is not None


async def test_large_feeds_load_in_background(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Setup does not depend on the lane closure file; it arrives afterwards."""
    responses = d3_responses()
    responses["lcs"] = 500
    mock_feeds(aioclient_mock, 3, responses)
    entry = await setup_entry(hass, make_entry())
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.i_80_lane_closures").state == STATE_UNAVAILABLE
    assert hass.states.get(CHAIN).state == "r2"


async def test_only_large_feeds_all_down_retries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    mock_feeds(aioclient_mock, 3, {"lcs": 500})
    entry = await setup_entry(hass, make_entry(feeds=["lcs"]))
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_options_change_reuses_fresh_downloads(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    entry = await setup_entry(hass, make_entry())
    calls = d3_feeds.call_count
    hass.config_entries.async_update_entry(entry, options={**entry.options, "routes": ["I-80"]})
    await hass.async_block_till_done()
    assert hass.states.get(CHAIN).state == "r2"
    assert d3_feeds.call_count == calls  # nothing downloaded again


async def test_startup_caps_the_wait_for_a_hung_server(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    """Seen live: a server that accepts and then hangs held Home Assistant's startup for 30 s."""
    from unittest.mock import patch

    from homeassistant.core import CoreState

    from custom_components.caltrans_roads import coordinator as coordinator_module
    from custom_components.caltrans_roads.const import REQUEST_TIMEOUTS, STARTUP_REQUEST_TIMEOUT

    seen: dict[str, float] = {}
    real_fetch = coordinator_module.fetch_feed

    async def recording_fetch(session, district, feed, seconds):
        seen[feed] = seconds
        return await real_fetch(session, district, feed, seconds)

    with patch.object(coordinator_module, "fetch_feed", recording_fetch):
        hass.set_state(CoreState.starting)
        entry = await setup_entry(hass, make_entry())
        assert seen["cc"] == STARTUP_REQUEST_TIMEOUT
        assert seen["cms"] == STARTUP_REQUEST_TIMEOUT
        assert seen["lcs"] == REQUEST_TIMEOUTS["lcs"]  # loads in the background, never capped
        hass.set_state(CoreState.running)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    coordinators = entry.runtime_data.coordinators
    assert all(c.request_timeout == REQUEST_TIMEOUTS[feed] for feed, c in coordinators.items())
